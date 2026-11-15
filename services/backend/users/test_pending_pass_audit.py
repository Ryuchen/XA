from datetime import timedelta
import json
from io import StringIO

from django.core.management import call_command
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_provider
from users.management.commands.audit_pending_provider_passes import audit_pending
from users.models import ProviderPassPurchase
from wallet.models import Transaction
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class PendingPassAuditTests(APITestCase):
    def setUp(self):
        self.provider = make_provider(deposit_mode='EARNINGS')
        self.account = get_or_create_account_for_legacy_user(self.provider)
        self.profile = self.provider.escort_profile
        self.profile.account = self.account
        self.profile.save(update_fields=['account'])
        self.wallet = get_wallet(account=self.account, user=self.provider)
        self.wallet.balance = 100000
        self.wallet.save(update_fields=['balance'])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
        response = self.client.post('/api/users/provider-pass/', {
            'tier': 'BRONZE', 'client_request_id': 'pass-audit',
        }, format='json')
        self.assertEqual(response.data['code'], 0)
        self.purchase = ProviderPassPurchase.objects.get(account=self.account)
        self.now = timezone.now()
        self.purchase.starts_at = self.now + timedelta(days=1)
        self.purchase.expires_at = self.now + timedelta(days=2)
        self.purchase.save(update_fields=['starts_at', 'expires_at'])
        self.profile.pass_expires_at = self.purchase.expires_at
        self.profile.save(update_fields=['pass_expires_at'])

    def test_read_only_explicit_alias_and_verified_payment(self):
        with CaptureQueriesContext(connection) as captured:
            report = audit_pending('default', now=self.now)
        self.assertTrue(all(q['sql'].lstrip().upper().startswith('SELECT') for q in captured.captured_queries))
        self.assertEqual(report['pending_count'], 1)
        self.assertEqual(report['records'][0]['issues'], [])
        self.assertEqual(report['paid_amount_units'], self.purchase.paid_amount)
        output = StringIO()
        call_command('audit_pending_provider_passes', database='default', stdout=output)
        self.assertEqual(json.loads(output.getvalue())['records'][0]['purchase_id'], self.purchase.pk)

    def test_flags_missing_counterparty_shared_payment_and_overlap(self):
        Transaction.objects.filter(tx_type=Transaction.TxType.PASS_INCOME).delete()
        ProviderPassPurchase.objects.create(
            provider=self.provider, account=self.account, tier=self.purchase.tier,
            days=self.purchase.days, daily_price=self.purchase.daily_price,
            original_amount=self.purchase.original_amount, discount_amount=self.purchase.discount_amount,
            paid_amount=self.purchase.paid_amount, transaction=self.purchase.transaction,
            starts_at=self.purchase.starts_at + timedelta(hours=1),
            expires_at=self.purchase.expires_at + timedelta(hours=1),
        )
        report = audit_pending('default', now=self.now)
        self.assertEqual(report['pending_count'], 2)
        self.assertIn('platform_receipt_missing_or_duplicate', report['records'][0]['issues'])
        self.assertIn('payment_shared_by_purchases', report['records'][0]['issues'])
        self.assertIn('pending_period_overlap', report['records'][1]['issues'])

    def test_payment_mismatch_is_not_treated_as_a_clean_record(self):
        Transaction.objects.filter(pk=self.purchase.transaction_id).update(amount=-1)
        row = audit_pending('default', now=self.now)['records'][0]
        self.assertIn('payment_amount_mismatch', row['issues'])
