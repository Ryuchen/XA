"""Boss order details update as the real report clock changes."""
import io
import tempfile
from datetime import timedelta
from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from engagement.confirmation import refresh_report_clock
from orders.models import Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from wallet.models import ProviderReport


def screenshot():
    data = io.BytesIO()
    Image.new('RGB', (4, 4), 'white').save(data, format='PNG')
    return SimpleUploadedFile('evidence.png', data.getvalue(), content_type='image/png')


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, LOGIN_OTP_REQUIRED=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class ReportClockBossRealtimeTest(APITransactionTestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.boss = make_user()
        self.provider = make_provider()
        self.admin = make_console_user(['report:view', 'report:audit'])
        self.accounts = {user.pk: get_or_create_account_for_legacy_user(user)
                         for user in (self.boss, self.provider, self.admin)}
        self.admin.admin_membership.account = self.accounts[self.admin.pk]
        self.admin.admin_membership.save(update_fields=['account'])
        self.provider.escort_profile.account = self.accounts[self.provider.pk]
        self.provider.escort_profile.save(update_fields=['account'])
        self.tokens = {pk: issue_account_tokens(account)[0] for pk, account in self.accounts.items()}
        self.service = make_service(price=10000, commission_rate=20)
        make_wallet(self.boss, balance=20000)

    def login(self, user):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + self.tokens[user.pk])

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data.get('data')

    def prepare_order(self):
        self.login(self.boss)
        order = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk,
            'provider_id': self.accounts[self.provider.pk].pk,
            'client_request_id': 'boss-clock-realtime',
        }, format='json'))
        self.login(self.provider)
        self.ok(self.client.post(f"/api/orders/orders/{order['id']}/start/", {'entry_image': screenshot()}, format='multipart'))
        self.ok(self.client.post(f"/api/orders/orders/{order['id']}/complete/", {'completion_image': screenshot()}, format='multipart'))
        report = ProviderReport.objects.get(order_id=order['id'], provider=self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart'))
        return order['id'], report.pk

    def submit(self, report_id):
        self.login(self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{report_id}/submit/', {}, format='json'))

    def reject(self, report_id):
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report_id}/reject/', {'audit_remark': '凭证不清晰'}, format='json'))

    def approve_after_window(self, report_id):
        self.login(self.admin)
        # Advance only time; audit, settlement, wallet, and notification are real.
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
            self.ok(self.client.post(f'/api/admin/reports/{report_id}/approve/', {}, format='json'))

    def detail(self, order_id):
        self.login(self.boss)
        return self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))

    def recalculate_unchanged(self, order_id):
        with transaction.atomic():
            refresh_report_clock(Order.objects.select_for_update().get(pk=order_id))

    def test_boss_receives_submit_reject_and_resubmit_clock_changes(self):
        order_id, report_id = self.prepare_order()
        token = self.tokens[self.boss.pk]

        async def verify():
            socket = WebsocketCommunicator(application, f'/ws/orders/?token={token}')
            self.assertTrue((await socket.connect())[0])
            try:
                for operation, clock_present in ((self.submit, True), (self.reject, False), (self.submit, True)):
                    await database_sync_to_async(operation)(report_id)
                    event = await socket.receive_json_from(timeout=2)
                    self.assertEqual(event['type'], 'order_status_update')
                    self.assertEqual(event['data']['id'], order_id)
                    detail = await database_sync_to_async(self.detail)(order_id)
                    self.assertEqual(bool(detail['consumption_ready_at']), clock_present)
                    self.assertEqual(event['data']['consumption_ready_at'], detail['consumption_ready_at'])
                    self.assertTrue(await socket.receive_nothing(timeout=0.1))
                await database_sync_to_async(self.recalculate_unchanged)(order_id)
                self.assertTrue(await socket.receive_nothing(timeout=0.1))
            finally:
                await socket.disconnect()

        async_to_sync(verify)()

    def test_boss_receives_order_when_approval_immediately_settles(self):
        order_id, report_id = self.prepare_order()
        self.submit(report_id)
        token = self.tokens[self.boss.pk]

        async def verify():
            socket = WebsocketCommunicator(application, f'/ws/orders/?token={token}')
            self.assertTrue((await socket.connect())[0])
            try:
                await database_sync_to_async(self.approve_after_window)(report_id)
                event = await socket.receive_json_from(timeout=2)
                self.assertEqual(event['type'], 'order_status_update')
                self.assertEqual(event['data']['id'], order_id)
                self.assertIsNotNone(event['data']['settled_at'])
                detail = await database_sync_to_async(self.detail)(order_id)
                self.assertEqual(detail['settled_at'], event['data']['settled_at'])
                self.assertTrue(await socket.receive_nothing(timeout=0.1))
            finally:
                await socket.disconnect()

        async_to_sync(verify)()
