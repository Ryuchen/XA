"""A real settled refund invalidates report-only reviewer sessions."""
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import override_settings
from rest_framework.test import APITransactionTestCase

from club_accounts.tokens import issue_account_tokens
from club_accounts.services import get_or_create_account_for_legacy_user
from config.asgi import application
from orders.models import Order, OrderRefund
from orders.tests.factories import make_provider
from orders.tests import test_release_flow as release_flow
from orders.tests.test_report_realtime import ReportFixtures


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   LOGIN_OTP_REQUIRED=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class RefundedReportLiveTest(ReportFixtures, APITransactionTestCase):
    setUp = release_flow.ReleaseFlowTest.setUp
    login = release_flow.ReleaseFlowTest.login
    ok = release_flow.ReleaseFlowTest.ok
    recharge = release_flow.ReleaseFlowTest.recharge
    new_order = release_flow.ReleaseFlowTest.new_order
    finish = release_flow.ReleaseFlowTest.finish
    settle_after_window = release_flow.ReleaseFlowTest.settle_after_window

    def test_approved_report_updates_reviewer_after_partial_refund_once(self):
        order = self.new_order()
        report = self.finish(order)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                                 {'kind': 'RESULT', 'image': release_flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
        self.settle_after_window()
        reviewer, _ = self.make_reviewer(['report:view'])
        order_reader, _ = self.make_reviewer(['order:view'])
        reviewer_token, _ = issue_account_tokens(reviewer)
        order_reader_token, _ = issue_account_tokens(order_reader)
        admin_token, _ = issue_account_tokens(self.accounts[self.admin.pk])
        from rest_framework.test import APIClient
        reviewer_client, admin_client = APIClient(), APIClient()
        reviewer_client.credentials(HTTP_AUTHORIZATION='Bearer ' + reviewer_token)
        admin_client.credentials(HTTP_AUTHORIZATION='Bearer ' + admin_token)
        before = self.ok(reviewer_client.get(f'/api/admin/reports/{report.pk}/'))
        self.assertEqual(before['settlement_status'], 'SETTLED')
        preview = self.ok(admin_client.get(f'/api/admin/orders/{order.pk}/refund-preview/', {'refund_amount': 2500}))
        payload = {'reason': '隔离部分退款', 'refund_amount': 2500,
                   'client_request_id': 'report-view-realtime-refund', 'plan_token': preview['plan_token']}

        async def verify():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={token}')
                       for token in (reviewer_token, order_reader_token)]
            try:
                for socket in sockets:
                    self.assertTrue((await socket.connect())[0])
                response = await database_sync_to_async(admin_client.post)(
                    f'/api/admin/orders/{order.pk}/refund/', payload, format='json')
                self.ok(response)
                self.assertEqual(await sockets[0].receive_json_from(), {
                    'type': 'report_status_update',
                    'data': {'report_id': report.pk, 'order_id': order.pk, 'status': 'APPROVED'},
                })
                updated = await database_sync_to_async(reviewer_client.get)(f'/api/admin/reports/{report.pk}/')
                self.assertEqual(self.ok(updated)['settlement_status'], 'REVIEW')
                self.assertTrue(await sockets[0].receive_nothing(timeout=0.2))
                order_event = await sockets[1].receive_json_from()
                self.assertEqual(order_event['type'], 'order_status_update')
                self.assertTrue(await sockets[1].receive_nothing(timeout=0.2))
                replay = await database_sync_to_async(admin_client.post)(
                    f'/api/admin/orders/{order.pk}/refund/', payload, format='json')
                self.ok(replay)
                self.assertTrue(await sockets[0].receive_nothing(timeout=0.2))
                self.assertTrue(await sockets[1].receive_nothing(timeout=0.2))
            finally:
                for socket in sockets:
                    await socket.disconnect()

        async_to_sync(verify)()
        report.refresh_from_db()
        self.assertEqual(report.status, 'APPROVED')
        self.assertEqual(OrderRefund.objects.filter(order=order).count(), 1)

    def test_double_order_invalidates_both_reports_for_report_only_reviewer(self):
        self.service.price = 5000
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['price', 'billing_provider_count'])
        second = make_provider()
        self.accounts[second.pk] = get_or_create_account_for_legacy_user(second)
        second.escort_profile.account = self.accounts[second.pk]
        second.escort_profile.save(update_fields=['account'])
        self.ok(self.recharge())
        data = self.ok(self.client.post('/api/admin/orders/dispatch/', {
            'customer_id': self.accounts[self.boss.pk].pk, 'service_id': self.service.pk,
            'client_request_id': 'report-live-double', 'escort_mode': 'DOUBLE',
            'providers': [{'provider_id': self.accounts[self.provider.pk].pk, 'commission_rate': 20},
                          {'provider_id': self.accounts[second.pk].pk, 'commission_rate': 30}],
        }, format='json'))
        order = Order.objects.get(pk=data['id'])
        self.finish(order)
        report_ids = []
        for user in (self.provider, second):
            self.login(user)
            report = self.ok(self.client.post('/api/wallet/reports/', {'order_id': order.pk}))
            report_ids.append(report['id'])
            for kind in ('ENTRY', 'COMPLETION', 'RESULT'):
                self.ok(self.client.post(f"/api/wallet/reports/{report['id']}/images/",
                                         {'kind': kind, 'image': release_flow.screenshot()}, format='multipart'))
            self.ok(self.client.post(f"/api/wallet/reports/{report['id']}/submit/"))
            self.login(self.admin)
            self.ok(self.client.post(f"/api/admin/reports/{report['id']}/approve/"))
        self.settle_after_window()
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        reviewer, _ = self.make_reviewer(['report:view'])
        token, _ = issue_account_tokens(reviewer)
        preview = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/', {'refund_amount': 2500}))
        payload = {'reason': '隔离双陪部分退款', 'refund_amount': 2500,
                   'client_request_id': 'report-live-double-refund', 'plan_token': preview['plan_token']}

        async def verify():
            socket = WebsocketCommunicator(application, f'/ws/orders/?token={token}')
            self.assertTrue((await socket.connect())[0])
            try:
                result = await database_sync_to_async(self.client.post)(
                    f'/api/admin/orders/{order.pk}/refund/', payload, format='json')
                self.ok(result)
                events = [await socket.receive_json_from() for _ in report_ids]
                self.assertEqual({event['data']['report_id'] for event in events}, set(report_ids))
                self.assertTrue(all(event['type'] == 'report_status_update' and
                                    event['data']['status'] == 'APPROVED' for event in events))
                self.assertTrue(await socket.receive_nothing(timeout=0.2))
            finally:
                await socket.disconnect()

        async_to_sync(verify)()
