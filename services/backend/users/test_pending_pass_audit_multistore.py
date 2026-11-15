from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.tests.factories import make_provider
from stores.context import store_context
from stores.tests_hosts import xinan_store
from users.management.commands.audit_pending_provider_passes import audit_pending
from users.models import ProviderPassPurchase
from wallet.models import Transaction
from wallet.services import get_wallet


class PendingPassAuditStoreTests(TestCase):
    databases = {'default', 'store_b'}

    def make_pending(self, amount):
        provider = make_provider(deposit_mode='EARNINGS')
        account = get_or_create_account_for_legacy_user(provider)
        profile = provider.escort_profile
        profile.account = account
        profile.pass_expires_at = timezone.now() + timedelta(days=2)
        profile.save(update_fields=['account', 'pass_expires_at'])
        wallet = get_wallet(account=account, user=provider)
        tx = Transaction.objects.create(wallet=wallet, amount=-amount,
                                        tx_type=Transaction.TxType.PASS_PURCHASE)
        return ProviderPassPurchase.objects.create(
            provider=provider, account=account, tier='BRONZE', days=1,
            daily_price=amount, original_amount=amount, paid_amount=amount,
            starts_at=timezone.now() + timedelta(days=1),
            expires_at=timezone.now() + timedelta(days=2), transaction=tx,
        )

    def test_each_explicit_alias_reads_only_its_own_records(self):
        first = self.make_pending(100)
        with store_context(xinan_store()):
            second = self.make_pending(200)
            # Explicit database stays authoritative even in another store context.
            default_report = audit_pending('default')
            store_b_report = audit_pending('store_b')
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(default_report['paid_amount_units'], 100)
        self.assertEqual(store_b_report['paid_amount_units'], 200)
        self.assertEqual(default_report['pending_count'], 1)
        self.assertEqual(store_b_report['pending_count'], 1)
