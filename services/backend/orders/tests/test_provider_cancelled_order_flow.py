"""The provider can find a refunded order through the actual status-filtered API."""

import io
import tempfile
from uuid import uuid4

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from users.models import EscortProfile
from wallet.models import Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True)
class ProviderCancelledOrderFlowTest(APITestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.media_settings = override_settings(MEDIA_ROOT=self.media.name)
        self.media_settings.enable()
        self.addCleanup(self.media_settings.disable)
        self.boss = make_user()
        self.staff = make_console_user(['order:view', 'order:dispatch', 'order:refund'])
        self.provider = make_provider()
        self.other = make_provider()
        self.accounts = {user.pk: get_or_create_account_for_legacy_user(user)
                         for user in (self.boss, self.staff, self.provider, self.other)}
        self.staff.admin_membership.account = self.accounts[self.staff.pk]
        self.staff.admin_membership.save(update_fields=['account'])
        for user in (self.provider, self.other):
            EscortProfile.objects.filter(user=user).update(account=self.accounts[user.pk])
        self.service = make_service(price=10000, billing_provider_count=1, commission_rate=20)
        make_wallet(self.boss, balance=20000)

    def login(self, user):
        token, _ = issue_account_tokens(self.accounts[user.pk])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data.get('data')

    def test_full_refund_moves_service_to_provider_cancelled_history(self):
        self.login(self.boss)
        created = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'dispatch_route': 'STAFF',
            'client_request_id': str(uuid4()),
        }, format='json'))
        order_id = created['id']
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 10000)

        self.login(self.staff)
        self.ok(self.client.post(f'/api/admin/orders/{order_id}/assign/', {
            'provider_id': self.accounts[self.provider.pk].pk,
        }, format='json'))

        self.login(self.provider)
        data = io.BytesIO()
        Image.new('RGB', (4, 4), 'white').save(data, format='PNG')
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {
            'entry_image': SimpleUploadedFile('entry.png', data.getvalue(), content_type='image/png'),
        }, format='multipart'))
        before = self.ok(self.client.get('/api/orders/orders/', {'status': 'in_progress', 'role': 'provider'}))
        self.assertIn(order_id, [row['id'] for row in before])

        self.login(self.staff)
        preview = self.ok(self.client.get(f'/api/admin/orders/{order_id}/refund-preview/'))
        self.ok(self.client.post(f'/api/admin/orders/{order_id}/refund/', {
            'reason': '客服确认终止服务', 'plan_token': preview['plan_token'],
            'client_request_id': str(uuid4()),
        }, format='json'))
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 20000)

        self.login(self.provider)
        serving = self.ok(self.client.get('/api/orders/orders/', {'status': 'in_progress', 'role': 'provider'}))
        self.assertNotIn(order_id, [row['id'] for row in serving])
        rows = self.ok(self.client.get('/api/orders/orders/', {'status': 'cancelled', 'role': 'provider'}))
        order = next(row for row in rows if row['id'] == order_id)
        self.assertEqual((order['status'], order['payment_status'], order['cancel_reason']),
                         ('CANCELLED', 'REFUNDED', '客服确认终止服务'))
        self.assertEqual(order['refund_summary']['amount'], 10000)
        self.assertEqual(order['refund_summary']['remaining_amount'], 0)
        self.assertEqual(order['refund_summary']['recovered_amount'], 0)
        self.assertEqual(order['refund_summary']['net_income'], 0)
        self.assertEqual(order['allowed_actions'], [])
        self.login(self.other)
        other_rows = self.ok(self.client.get('/api/orders/orders/', {'status': 'cancelled', 'role': 'provider'}))
        self.assertNotIn(order_id, [row['id'] for row in other_rows])
