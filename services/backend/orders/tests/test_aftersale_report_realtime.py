"""Report reviewers see the current aftersale context without order access."""
from datetime import timedelta
from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase
from uuid import uuid4

from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from engagement.confirmation import process_order
from orders.tests import test_provider_aftersale_status as flow
from orders.tests.test_report_realtime import ReportFixtures
from wallet.models import ProviderReport, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class AfterSaleReportRealtimeTest(ReportFixtures, APITransactionTestCase):
    setUp = flow.ProviderAfterSaleStatusTest.setUp
    login = flow.ProviderAfterSaleStatusTest.login
    ok = flow.ProviderAfterSaleStatusTest.ok
    prepare = flow.ProviderAfterSaleStatusTest.prepare
    open_aftersale = flow.ProviderAfterSaleStatusTest.open_aftersale
    resolve = flow.ProviderAfterSaleStatusTest.resolve

    def test_case_lifecycle_refreshes_report_only_reviewer_and_keeps_settlement_blocked(self):
        order_id, report_id = self.prepare()
        reviewer, _ = self.make_reviewer(['report:view'])
        token, _ = issue_account_tokens(reviewer)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(self.ok(client.get(f'/api/admin/reports/{report_id}/'))['aftersale_status'], '')

        async def verify():
            socket = WebsocketCommunicator(application, f'/ws/orders/?token={token}')
            self.assertTrue((await socket.connect())[0])
            try:
                ticket = await database_sync_to_async(self.open_aftersale)(order_id)
                opened = await socket.receive_json_from(timeout=2)
                self.assertEqual(opened['type'], 'report_status_update')
                self.assertEqual(opened['data']['report_id'], report_id)
                detail = self.ok(await database_sync_to_async(client.get)(f'/api/admin/reports/{report_id}/'))
                self.assertEqual(detail['aftersale_status'], 'OPEN')
                self.assertNotIn('仅客服核对的具体售后说明', str(detail))
                await database_sync_to_async(self.assert_blocked)(order_id)
                await database_sync_to_async(self.resolve)(ticket)
                resolved = await socket.receive_json_from(timeout=2)
                self.assertEqual(resolved['type'], 'report_status_update')
                detail = self.ok(await database_sync_to_async(client.get)(f'/api/admin/reports/{report_id}/'))
                self.assertEqual(detail['aftersale_status'], 'RESOLVED')
                self.assertTrue(await socket.receive_nothing(timeout=0.2))
            finally:
                await socket.disconnect()

        async_to_sync(verify)()
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
            self.assertTrue(process_order(order_id))
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 8000)

    def assert_blocked(self, order_id):
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
            self.assertFalse(process_order(order_id))
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 0)

    def test_evidence_can_be_approved_during_case_without_releasing_income(self):
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'client_request_id': str(uuid4()),
        }, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': flow.image()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/', {'completion_image': flow.image()}, format='multipart'))
        report = ProviderReport.objects.get(order_id=order_id, provider=self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': flow.image()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        ticket = self.open_aftersale(order_id)
        self.login(self.staff)
        reports = self.ok(self.client.get('/api/admin/reports/'))
        row = next(item for item in reports['list'] if item['id'] == report.pk)
        self.assertEqual(row['aftersale_status'], 'OPEN')
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        self.assert_blocked(order_id)
        self.resolve(ticket)
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
            self.assertTrue(process_order(order_id))
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 8000)
