"""Report work queue follows a real refunded order without deleting history."""
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import override_settings
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from orders.tests.factories import make_console_user
from orders.tests.test_release_flow import ReleaseFlowTest, screenshot
from wallet.models import ProviderReport


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class RefundedReportWorkQueueTest(APITransactionTestCase):
    setUp = ReleaseFlowTest.setUp
    login = ReleaseFlowTest.login
    ok = ReleaseFlowTest.ok
    recharge = ReleaseFlowTest.recharge
    new_order = ReleaseFlowTest.new_order
    finish = ReleaseFlowTest.finish

    def prepare(self):
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'dashboard:view']
        role.save(update_fields=['permissions'])
        self.reviewer = make_console_user(['report:view', 'report:audit'])
        self.accounts[self.reviewer.pk] = get_or_create_account_for_legacy_user(self.reviewer)
        self.reviewer.admin_membership.account = self.accounts[self.reviewer.pk]
        self.reviewer.admin_membership.save(update_fields=['account'])
        self.order = self.new_order()
        self.report = self.finish(self.order)
        self.ok(self.client.post(f'/api/wallet/reports/{self.report.pk}/images/',
                                 {'kind': 'RESULT', 'image': screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{self.report.pk}/submit/'))
        self.manual = ProviderReport.objects.create(provider=self.provider, provider_account=self.accounts[self.provider.pk],
                                                    game_name='历史手工报单', amount=1000, status='PENDING')
        self.login(self.admin)
        dashboard = self.ok(self.client.get('/api/admin/dashboard/stats'))
        self.assertEqual((dashboard['pending_report_total'], dashboard['pending_report_order_blocked_total']), (2, 0))
        reports = self.ok(self.client.get('/api/admin/reports/', {'status': 'PENDING'}))['list']
        self.assertEqual({row['id']: row['order_approval_block_reason'] for row in reports},
                         {self.report.pk: '', self.manual.pk: ''})

    def refund(self):
        self.login(self.admin)
        preview = self.ok(self.client.get(f'/api/admin/orders/{self.order.pk}/refund-preview/'))
        self.ok(self.client.post(f'/api/admin/orders/{self.order.pk}/refund/', {
            'reason': '客服终止服务', 'plan_token': preview['plan_token'],
            'client_request_id': 'report-block-refund',
        }, format='json'))

    def verify_after_refund(self):
        self.login(self.admin)
        dashboard = self.ok(self.client.get('/api/admin/dashboard/stats'))
        self.assertEqual((dashboard['pending_report_total'], dashboard['pending_report_order_blocked_total']), (2, 1))
        rows = self.ok(self.client.get('/api/admin/reports/', {'status': 'PENDING', 'order_blocked': '1'}))
        self.assertEqual(rows['total'], 1)
        self.assertEqual(rows['list'][0]['id'], self.report.pk)
        self.assertEqual(rows['list'][0]['order_approval_block_reason'], '订单已退款，不能通过报单')
        detail = self.ok(self.client.get(f'/api/admin/reports/{self.report.pk}/'))
        self.assertEqual(detail['order_approval_block_reason'], '订单已退款，不能通过报单')
        other = self.ok(self.client.get(f'/api/admin/reports/{self.manual.pk}/'))
        self.assertEqual(other['order_approval_block_reason'], '')
        response = self.client.post(f'/api/admin/reports/{self.report.pk}/approve/', {}, format='json')
        self.assertEqual(response.data['code'], 400)
        self.ok(self.client.post(f'/api/admin/reports/{self.report.pk}/reject/', {'audit_remark': '订单已退款'}, format='json'))
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, 'REJECTED')
        dashboard = self.ok(self.client.get('/api/admin/dashboard/stats'))
        self.assertEqual((dashboard['pending_report_total'], dashboard['pending_report_order_blocked_total']), (1, 0))
        self.ok(self.client.post(f'/api/admin/reports/{self.manual.pk}/approve/', {}, format='json'))

    def test_refund_keeps_history_and_notifies_order_and_report_reviewers(self):
        self.prepare()
        staff_token, _ = issue_account_tokens(self.accounts[self.admin.pk])
        reviewer_token, _ = issue_account_tokens(self.accounts[self.reviewer.pk])
        provider_token, _ = issue_account_tokens(self.accounts[self.provider.pk])

        async def verify():
            staff = WebsocketCommunicator(application, f'/ws/orders/?token={staff_token}')
            reviewer = WebsocketCommunicator(application, f'/ws/orders/?token={reviewer_token}')
            provider = WebsocketCommunicator(application, f'/ws/orders/?token={provider_token}')
            self.assertTrue((await staff.connect())[0])
            self.assertTrue((await reviewer.connect())[0])
            self.assertTrue((await provider.connect())[0])
            try:
                await database_sync_to_async(self.refund)()
                event = await reviewer.receive_json_from(timeout=2)
                self.assertEqual((event['type'], event['data']['report_id']), ('report_status_update', self.report.pk))
                self.assertEqual(event['data']['status'], 'PENDING')
                self.assertTrue(await reviewer.receive_nothing(timeout=0.1))
                provider_events = []
                while not any(item['type'] == 'report_status_update' for item in provider_events):
                    provider_events.append(await provider.receive_json_from(timeout=2))
                report_events = [item for item in provider_events if item['type'] == 'report_status_update']
                self.assertEqual(report_events[0]['data']['report_id'], self.report.pk)
                received = []
                while len(received) < 2:
                    received.append((await staff.receive_json_from(timeout=2))['type'])
                self.assertIn('report_status_update', received)
                self.assertIn('order_status_update', received)
                await database_sync_to_async(self.verify_after_refund)()
            finally:
                await staff.disconnect()
                await reviewer.disconnect()
                await provider.disconnect()

        async_to_sync(verify)()

    def test_historical_pending_report_after_partial_refund_is_flagged(self):
        self.prepare()
        # Historical data can retain a pending report on a settled, partially
        # refunded order. Exercise the read/filter contract for that state.
        from orders.models import Order
        Order.objects.filter(pk=self.order.pk).update(payment_status=Order.PaymentStatus.PARTIALLY_REFUNDED)
        detail = self.ok(self.client.get(f'/api/admin/reports/{self.report.pk}/'))
        self.assertEqual(detail['order_approval_block_reason'], '订单已部分退款，不能通过报单')
        rows = self.ok(self.client.get('/api/admin/reports/', {'order_blocked': '1'}))
        self.assertEqual([row['id'] for row in rows['list']], [self.report.pk])
        response = self.client.post(f'/api/admin/reports/{self.report.pk}/approve/', {}, format='json')
        self.assertEqual(response.data['code'], 400)
