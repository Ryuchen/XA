from django.conf import settings
from django.test import TestCase, override_settings
from unittest import skipUnless

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.tests.factories import make_completed_order, make_provider, make_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from wallet.models import RefundDepositDeficit, RefundDepositIncomeApplication, get_platform_wallet
from wallet.services import get_wallet


@skipUnless('store_b' in settings.DATABASES, '需要两套隔离测试数据库')
@override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
class RefundDepositMultiStoreTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_same_identifiers_do_not_share_refund_deficits(self):
        from wallet.test_refund_deposit_deficit import RefundDepositDeficitTest
        stores = [resolve_store(), xinan_store()]
        identities = []
        for store in stores:
            with store_context(store):
                self.boss = make_user()
                self.provider = make_provider(deposit_mode='EARNINGS', deposit_paid=10000)
                self.account = get_or_create_account_for_legacy_user(self.provider)
                self.profile = self.provider.escort_profile
                self.profile.account = self.account; self.profile.save(update_fields=['account'])
                self.wallet = get_wallet(account=self.account, user=self.provider)
                self.platform = get_platform_wallet()
                self.platform.balance = 10000; self.platform.save(update_fields=['balance'])
                self.order = make_completed_order(self.boss, self.provider, amount=10000,
                    provider_account=self.account, payment_status='PAID')
                deficit = RefundDepositDeficitTest.deficit(self, 200)
                identities.append((self.account.pk, self.wallet.pk, self.order.pk, deficit.pk))
        self.assertEqual(identities[0], identities[1])
        with store_context(stores[0]):
            account_id, wallet_id, order_id, deficit_id = identities[0]
            from club_accounts.models import ClubAccount
            from orders.models import Order
            from users.models import EscortProfile
            from wallet.models import Wallet
            self.account = ClubAccount.objects.get(pk=account_id)
            self.profile = EscortProfile.objects.get(account=self.account)
            self.wallet = Wallet.objects.get(pk=wallet_id)
            self.order = Order.objects.get(pk=order_id)
            RefundDepositDeficitTest.income(self, 200)
            self.assertEqual(RefundDepositDeficit.objects.get(pk=deficit_id).remaining, 0)
            self.assertEqual(RefundDepositIncomeApplication.objects.count(), 1)
        with store_context(stores[1]):
            self.assertEqual(RefundDepositDeficit.objects.get(pk=identities[1][3]).remaining, 200)
            self.assertEqual(RefundDepositIncomeApplication.objects.count(), 0)
            from orders.services import ensure_provider_dispatch_locks, lock_provider_dispatch
            from orders.tests.factories import make_order
            from stores.db import transaction
            from users.models import EscortProfile
            from wallet.models import RefundDepositRecoveryPermit
            from wallet.refund_recovery_order import reserve
            profile = EscortProfile.objects.get(account_id=identities[1][0])
            recovery = make_order(profile.user, provider=profile.user, provider_account_id=profile.account_id,
                                  status='GRABBED', payment_status='PAID')
            ensure_provider_dispatch_locks([profile.account_id])
            with transaction.atomic():
                lock_provider_dispatch([profile.account_id])
                reserve(profile, recovery)
            self.assertEqual(RefundDepositRecoveryPermit.objects.count(), 1)
        with store_context(stores[0]):
            self.assertEqual(RefundDepositRecoveryPermit.objects.count(), 0)
