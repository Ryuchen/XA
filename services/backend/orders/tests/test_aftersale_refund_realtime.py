"""A case refund refreshes a report-only reviewer after the committed outcome."""
from datetime import timedelta

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from engagement.confirmation import process_order
from orders.models import Order
from orders.tests import test_provider_aftersale_status as flow
from orders.tests.test_report_realtime import ReportFixtures
from wallet.models import ProviderReport, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class AfterSaleRefundRealtimeTest(ReportFixtures, APITransactionTestCase):
    setUp = flow.ProviderAfterSaleStatusTest.setUp
    login = flow.ProviderAfterSaleStatusTest.login
    ok = flow.ProviderAfterSaleStatusTest.ok
    prepare = flow.ProviderAfterSaleStatusTest.prepare
    open_aftersale = flow.ProviderAfterSaleStatusTest.open_aftersale

    def test_unsettled_full_case_refund_updates_report_reviewer_and_parties(self):
        order_id, report_id = self.prepare()
        ticket = self.open_aftersale(order_id)
        role = self.staff.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'order:refund']
        role.save(update_fields=['permissions'])
        self.login(self.staff)
        preview = self.ok(self.client.get(f'/api/admin/orders/{order_id}/refund-preview/'))
        payload = {'resolution': '协商全额退款', 'plan_token': preview['plan_token'],
                   'client_request_id': 'aftersale-unsettled-refund'}
        reviewer, _ = self.make_reviewer(['report:view'])
        token, _ = issue_account_tokens(reviewer)
        reviewer_client = APIClient()
        reviewer_client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(self.ok(reviewer_client.get(f'/api/admin/reports/{report_id}/'))['aftersale_status'], 'OPEN')

        async def verify():
            socket = WebsocketCommunicator(application, f'/ws/orders/?token={token}')
            self.assertTrue((await socket.connect())[0])
            try:
                result = await database_sync_to_async(self.client.post)(
                    f'/api/admin/aftersales/{ticket}/refund/', payload, format='json')
                self.assertEqual(self.ok(result)['status'], 'REFUNDED')
                event = await socket.receive_json_from(timeout=2)
                self.assertEqual(event['type'], 'report_status_update')
                self.assertEqual(event['data']['report_id'], report_id)
                report = self.ok(await database_sync_to_async(reviewer_client.get)(f'/api/admin/reports/{report_id}/'))
                self.assertEqual(report['aftersale_status'], 'REFUNDED')
                self.assertTrue(await socket.receive_nothing(timeout=0.2))
                replay = await database_sync_to_async(self.client.post)(
                    f'/api/admin/aftersales/{ticket}/refund/', payload, format='json')
                self.assertEqual(self.ok(replay)['status'], 'REFUNDED')
                self.assertTrue(await socket.receive_nothing(timeout=0.2))
            finally:
                await socket.disconnect()

        async_to_sync(verify)()
        for user in (self.boss, self.provider):
            self.login(user)
            order = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))
            self.assertEqual((order['status'], order['payment_status'], order['aftersale_status']),
                             ('CANCELLED', 'REFUNDED', 'REFUNDED'))
            notices = self.ok(self.client.get('/api/messages/'))['messages']
            self.assertEqual(len([row for row in notices if row['related_order_id'] == order_id
                                  and row['title'] == '订单已退款']), 1)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 50000)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 0)

    def test_settled_partial_case_refund_reaches_each_role_and_dashboard(self):
        order_id, report_id = self.prepare()
        # Move the isolated fixture into its actual settled window; no financial
        # service or provider response is replaced by a mock.
        matured = timezone.now() - timedelta(hours=73)
        Order.objects.filter(pk=order_id).update(completed_at=matured)
        ProviderReport.objects.filter(pk=report_id).update(submitted_at=matured)
        self.assertTrue(process_order(order_id))
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 8000)
        ticket = self.open_aftersale(order_id)
        role = self.staff.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'order:refund', 'dashboard:view']
        role.save(update_fields=['permissions'])
        self.login(self.staff)
        preview = self.ok(self.client.get(f'/api/admin/orders/{order_id}/refund-preview/', {'refund_amount': 2500}))
        payload = {'resolution': '协商退还部分服务费', 'refund_amount': 2500,
                   'plan_token': preview['plan_token'], 'client_request_id': 'case-partial-refund'}
        reviewer, _ = self.make_reviewer(['report:view'])
        reviewer_token, _ = issue_account_tokens(reviewer)
        reviewer_client = APIClient()
        reviewer_client.credentials(HTTP_AUTHORIZATION='Bearer ' + reviewer_token)

        async def verify():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={token}') for token in
                       (self.tokens[self.boss.pk], self.tokens[self.provider.pk], reviewer_token)]
            for socket in sockets:
                self.assertTrue((await socket.connect())[0])
            try:
                response = await database_sync_to_async(self.client.post)(
                    f'/api/admin/aftersales/{ticket}/refund/', payload, format='json')
                self.assertEqual(self.ok(response)['status'], 'PARTIALLY_REFUNDED')
                for socket in sockets[:2]:
                    events = []
                    for _ in range(8):
                        event = await socket.receive_json_from(timeout=2)
                        events.append(event)
                        if event['type'] == 'order_status_update':
                            break
                    else:
                        self.fail(f'订单更新未送达: {events}')
                    self.assertEqual(event['data']['payment_status'], 'PARTIALLY_REFUNDED')
                    self.assertEqual(event['data']['aftersale_status'], 'PARTIALLY_REFUNDED')
                event = await sockets[2].receive_json_from(timeout=2)
                self.assertEqual(event['type'], 'report_status_update')
                self.assertEqual(event['data']['report_id'], report_id)
                report = self.ok(await database_sync_to_async(reviewer_client.get)(f'/api/admin/reports/{report_id}/'))
                self.assertEqual(report['aftersale_status'], 'PARTIALLY_REFUNDED')
            finally:
                for socket in sockets:
                    await socket.disconnect()
        async_to_sync(verify)()

        self.login(self.staff)
        replay = self.ok(self.client.post(f'/api/admin/aftersales/{ticket}/refund/', payload, format='json'))
        self.assertEqual(replay['status'], 'PARTIALLY_REFUNDED')

        for user in (self.boss, self.provider):
            self.login(user)
            order = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))
            self.assertEqual((order['status'], order['payment_status'], order['aftersale_status']),
                             ('COMPLETED', 'PARTIALLY_REFUNDED', 'PARTIALLY_REFUNDED'))
            self.assertEqual(order['refund_summary']['amount'], 2500)
            notices = self.ok(self.client.get('/api/messages/'))['messages']
            self.assertEqual(len([row for row in notices if row['related_order_id'] == order_id
                                  and row['title'] == '订单部分退款']), 1)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 42500)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 6000)
        self.login(self.staff)
        dashboard = self.ok(self.client.get('/api/admin/dashboard/stats'))
        self.assertEqual((dashboard['paid_amount_total'], dashboard['open_aftersale_order_total']), (7500, 0))
