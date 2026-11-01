"""Actual customer, reviewer and provider views agree on an open aftersale."""
import io
import tempfile
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from PIL import Image
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from engagement.confirmation import process_order
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from wallet.models import ProviderReport


def image():
    data = io.BytesIO()
    Image.new('RGB', (4, 4), 'white').save(data, format='PNG')
    return SimpleUploadedFile('proof.png', data.getvalue(), content_type='image/png')


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class ProviderAfterSaleStatusTest(APITransactionTestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.media_settings = override_settings(MEDIA_ROOT=self.media.name)
        self.media_settings.enable()
        self.addCleanup(self.media_settings.disable)
        self.boss, self.provider = make_user(), make_provider()
        self.staff = make_console_user(['report:view', 'report:audit', 'order:view', 'order:settle'])
        self.accounts = {u.pk: get_or_create_account_for_legacy_user(u) for u in (self.boss, self.provider, self.staff)}
        self.tokens = {pk: issue_account_tokens(account)[0] for pk, account in self.accounts.items()}
        self.staff.admin_membership.account = self.accounts[self.staff.pk]
        self.staff.admin_membership.save(update_fields=['account'])
        self.provider.escort_profile.account = self.accounts[self.provider.pk]
        self.provider.escort_profile.save(update_fields=['account'])
        self.service = make_service(price=10000, commission_rate=20)
        make_wallet(self.boss, balance=50000)

    def login(self, user):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + self.tokens[user.pk])

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data.get('data')

    def prepare(self):
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'client_request_id': str(uuid4()),
        }, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': image()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/', {'completion_image': image()}, format='multipart'))
        report = ProviderReport.objects.get(order_id=order_id, provider=self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': image()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.staff)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        return order_id, report.pk

    def open_aftersale(self, order_id):
        self.login(self.boss)
        return self.ok(self.client.post(f'/api/orders/orders/{order_id}/aftersale/', {
            'reason': '仅客服核对的具体售后说明', 'client_request_id': str(uuid4()),
        }, format='json'))['id']

    def resolve(self, ticket_id):
        self.login(self.staff)
        self.ok(self.client.post(f'/api/admin/aftersales/{ticket_id}/resolve/', {
            'resolution': '客服已与老板沟通',
        }, format='json'))

    def provider_state(self, order_id, report_id, expected, settled):
        self.login(self.provider)
        orders = self.ok(self.client.get('/api/orders/orders/', {'status': 'completed'}))
        reports = self.ok(self.client.get('/api/wallet/reports/'))
        order = next(row for row in orders if row['id'] == order_id)
        report = next(row for row in reports if row['id'] == report_id)
        self.assertEqual(order['aftersale_status'], expected)
        self.assertEqual(report['aftersale_status'], expected)
        self.assertEqual(bool(order['settled_at']), settled)
        self.assertEqual(bool(report['settled_at']), settled)
        self.assertNotIn('仅客服核对的具体售后说明', str(order))
        self.assertNotIn('仅客服核对的具体售后说明', str(report))

    def settle(self, order_id):
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
            self.assertTrue(process_order(order_id))

    def test_open_resolve_and_settled_reopen_reach_provider(self):
        order_id, report_id = self.prepare()

        async def verify():
            socket = WebsocketCommunicator(application, f'/ws/orders/?token={self.tokens[self.provider.pk]}')
            self.assertTrue((await socket.connect())[0])
            try:
                ticket = await database_sync_to_async(self.open_aftersale)(order_id)
                event = await socket.receive_json_from(timeout=2)
                self.assertEqual(event['type'], 'order_status_update')
                self.assertEqual(event['data']['aftersale_status'], 'OPEN')
                message_event = await socket.receive_json_from(timeout=2)
                self.assertEqual(message_event['type'], 'site_message_update')
                await database_sync_to_async(self.provider_state)(order_id, report_id, 'OPEN', False)
                await database_sync_to_async(self.resolve)(ticket)
                event = await socket.receive_json_from(timeout=2)
                self.assertEqual(event['data']['aftersale_status'], 'RESOLVED')
                message_event = await socket.receive_json_from(timeout=2)
                self.assertEqual(message_event['type'], 'site_message_update')
                await database_sync_to_async(self.provider_state)(order_id, report_id, 'RESOLVED', False)
                await database_sync_to_async(self.settle)(order_id)
                # Settlement also emits order/wallet updates; reconnect starts a clean stream.
            finally:
                await socket.disconnect()
            socket = WebsocketCommunicator(application, f'/ws/orders/?token={self.tokens[self.provider.pk]}')
            self.assertTrue((await socket.connect())[0])
            try:
                await database_sync_to_async(self.open_aftersale)(order_id)
                event = await socket.receive_json_from(timeout=2)
                self.assertEqual(event['data']['aftersale_status'], 'OPEN')
                await database_sync_to_async(self.provider_state)(order_id, report_id, 'OPEN', True)
            finally:
                await socket.disconnect()

        async_to_sync(verify)()

    def test_resolved_case_can_reopen_before_settlement_within_aftersale_window(self):
        order_id, report_id = self.prepare()
        self.login(self.boss)
        initial = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))
        self.assertTrue(initial['can_aftersale'])
        first = self.ok(self.client.post(f'/api/orders/orders/{order_id}/aftersale/', {
            'reason': '首次咨询服务问题', 'client_request_id': 'first-case',
        }, format='json'))
        self.assertFalse(self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['can_aftersale'])
        self.resolve(first['id'])

        self.login(self.boss)
        resolved = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))
        self.assertTrue(resolved['can_aftersale'])
        replay = self.ok(self.client.post(f'/api/orders/orders/{order_id}/aftersale/', {
            'reason': '首次咨询服务问题', 'client_request_id': 'first-case',
        }, format='json'))
        self.assertEqual(replay['status'], 'RESOLVED')
        reopened = self.ok(self.client.post(f'/api/orders/orders/{order_id}/aftersale/', {
            'reason': '补充新的服务问题', 'client_request_id': 'second-case',
        }, format='json'))
        self.assertEqual(reopened['id'], first['id'])
        self.assertEqual((reopened['status'], reopened['reason']), ('OPEN', '补充新的服务问题'))
        self.assertTrue(any(item['status'] == 'RESOLVED' and item['resolution'] == '客服已与老板沟通'
                            for item in reopened['history']))
        self.assertFalse(self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['can_aftersale'])
        self.provider_state(order_id, report_id, 'OPEN', False)

        self.login(self.staff)
        queue = self.ok(self.client.get('/api/admin/orders/', {'aftersale_open': '1'}))
        self.assertEqual([row['id'] for row in queue['list']], [order_id])
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
            self.assertFalse(process_order(order_id))
            self.resolve(first['id'])
            self.assertTrue(process_order(order_id))
        self.provider_state(order_id, report_id, 'RESOLVED', True)

    def test_resolved_unsettled_case_cannot_reopen_after_window(self):
        order_id, _ = self.prepare()
        ticket = self.open_aftersale(order_id)
        self.resolve(ticket)
        self.login(self.boss)
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
            self.assertFalse(self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['can_aftersale'])
            response = self.client.post(f'/api/orders/orders/{order_id}/aftersale/', {
                'reason': '售后期后再次申请', 'client_request_id': 'late-case',
            }, format='json')
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.ok(self.client.get(f'/api/orders/orders/{order_id}/aftersale/'))['status'], 'RESOLVED')

    def test_report_list_loads_aftersales_once_for_multiple_orders(self):
        for _ in range(3):
            self.prepare()
        self.login(self.provider)
        with CaptureQueriesContext(connection) as queries:
            reports = self.ok(self.client.get('/api/wallet/reports/'))
        self.assertEqual(len(reports), 3)
        self.assertEqual([report['aftersale_status'] for report in reports], ['', '', ''])
        aftersale_reads = [row['sql'] for row in queries.captured_queries
                           if 'orders_aftersale' in row['sql'].lower() and row['sql'].lstrip().upper().startswith('SELECT')]
        self.assertEqual(len(aftersale_reads), 1, aftersale_reads)
