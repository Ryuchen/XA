"""Exercise fee configuration, preview and history against the isolated backend."""
import os
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import PaymentFeePolicy, PaymentFeePolicyHistory
from orders.tests.factories import make_console_user


@skipUnless(os.environ.get('XA_PAYMENT_FEE_BROWSER') == '1', 'Requires isolated browser and database')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class PaymentFeeBrowserTest(LiveServerTestCase):
    def test_landscape_configuration_preview_and_history(self):
        user = make_console_user(['payment_fee:view', 'payment_fee:edit'])
        account = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = account
        user.admin_membership.save(update_fields=['account'])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        saved = client.put('/api/admin/config/payment-fee/', {'revision': 0, 'merchant_id': '1234567890',
            'appid': 'wx1234567890abcdef', 'rate_percent': '0.6', 'fee_basis': 'PAYMENT_TOTAL',
            'rounding': 'UP'}, format='json')
        self.assertEqual(saved.data['code'], 0, saved.data)
        self.assertEqual(saved.data['data']['revision'], 1)
        preview = client.post('/api/admin/config/payment-fee/preview/', {'service_fen': 10000}, format='json')
        self.assertEqual(preview.data['code'], 0, preview.data)
        self.assertEqual((preview.data['data']['revision'], preview.data['data']['service_fen'],
                          preview.data['data']['fee_fen'], preview.data['data']['total_fen']), (1, 10000, 61, 10061))
        row = PaymentFeePolicy.objects.get(pk=1)
        self.assertEqual((row.revision, row.rate_millionths, row.fee_basis, row.rounding),
                         (1, 6000, 'PAYMENT_TOTAL', 'UP'))
        self.assertEqual(PaymentFeePolicyHistory.objects.count(), 1)