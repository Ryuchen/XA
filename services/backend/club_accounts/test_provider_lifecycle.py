"""Lifecycle barrier and unavailable closure diagnostics in isolated stores."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import IntegrityError, close_old_connections, connection
from django.test import TransactionTestCase, override_settings

from club_accounts.models import ClubAccount, ProviderLifecycleLock
from club_accounts.provider_lifecycle import (ProviderLifecycleError, closure_facts,
    ensure_provider_lifecycle, lock_provider_closure, lock_provider_operation)
from common.mysql_test_support import require_disposable_mysql
from stores.context import resolve_store, store_context
from stores.db import transaction


@override_settings(LICENSE_ACCOUNT_ADMISSION_ENABLED=False)
class ProviderLifecycleGuardTest(TransactionTestCase):
    def setUp(self):
        self.store = resolve_store()

    def test_creation_and_bulk_creation_both_initialize_barrier(self):
        with store_context(self.store):
            one = ClubAccount.objects.create_account(username='life-one', account_type='PROVIDER')
            ClubAccount.objects.bulk_create([
                ClubAccount(username='life-two', account_type='PROVIDER', account_no='LIFE-TWO'),
                ClubAccount(username='life-three', account_type='PROVIDER', account_no='LIFE-THREE'),
            ])
            self.assertTrue(ProviderLifecycleLock.objects.filter(account=one).exists())
            self.assertEqual(ProviderLifecycleLock.objects.count(), 3)
            ensure_provider_lifecycle(one)
            facts = closure_facts(one)
            self.assertFalse(facts['closure_available'])
            self.assertFalse(facts['profile_present'])
            self.assertIsNone(facts['wallet_balance'])
            self.assertIsNone(facts['points_balance'])

    def test_barrier_create_failure_rolls_back_account(self):
        with store_context(self.store):
            with patch('club_accounts.models.ProviderLifecycleLock.objects.using') as using:
                using.return_value.create.side_effect = IntegrityError('injected barrier failure')
                with self.assertRaises(IntegrityError):
                    ClubAccount.objects.create_account(username='life-rollback', account_type='PROVIDER')
            self.assertFalse(ClubAccount.objects.filter(username='life-rollback').exists())

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '陪玩生命周期批量屏障失败回滚属正式契约深水区（18-盘点 陪玩生命周期待联调）；最终联调启用')
    def test_bulk_barrier_failure_rolls_back_accounts(self):
        with store_context(self.store):
            with patch('club_accounts.models.ProviderLifecycleLock.objects.using') as using:
                using.return_value.bulk_create.side_effect = IntegrityError('injected bulk barrier failure')
                with self.assertRaises(IntegrityError):
                    ClubAccount.objects.bulk_create([
                        ClubAccount(username='life-bulk-rollback', account_type='PROVIDER', account_no='LB-1'),
                    ])
            self.assertFalse(ClubAccount.objects.filter(username='life-bulk-rollback').exists())

    def test_missing_row_is_never_lazily_recreated(self):
        with store_context(self.store):
            account = ClubAccount.objects.create_account(username='life-missing', account_type='PROVIDER')
            ProviderLifecycleLock.objects.filter(account=account).delete()
            with self.assertRaises(ProviderLifecycleError):
                ensure_provider_lifecycle(account)
            self.assertFalse(ProviderLifecycleLock.objects.filter(account=account).exists())

    def test_known_funds_do_not_authorize_closure(self):
        from orders.tests.factories import make_user
        from users.models import EscortProfile
        from wallet.services import get_wallet
        with store_context(self.store):
            account = ClubAccount.objects.create_account(username='life-with-deposit', account_type='PROVIDER')
            user = make_user(role='PROVIDER')
            EscortProfile.objects.create(account=account, user=user, deposit_paid=100, status='OFFLINE')
            wallet = get_wallet(account=account, user=user)
            wallet.balance = 42
            wallet.save(update_fields=['balance'])
            facts = closure_facts(account)
            self.assertFalse(facts['closure_available'])
            self.assertEqual((facts['deposit_paid'], facts['wallet_balance'], facts['wallet_frozen']), (100, 42, 0))
            account.refresh_from_db()
            self.assertTrue(account.license_membership_active)


@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
@override_settings(LICENSE_ACCOUNT_ADMISSION_ENABLED=False)
class ProviderLifecycleStoresTest(TransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    reset_sequences = True

    def test_same_ids_are_bound_to_their_own_store(self):
        from stores.tests_hosts import xinan_store
        first, second = resolve_store(), xinan_store()
        with store_context(first):
            a = ClubAccount.objects.create_account(username='life-store-a', account_type='PROVIDER')
        with store_context(second):
            b = ClubAccount.objects.create_account(username='life-store-b', account_type='PROVIDER')
            self.assertEqual(a.pk, b.pk)
            ensure_provider_lifecycle(b)
            with self.assertRaises(ProviderLifecycleError):
                ensure_provider_lifecycle(a)
        with store_context(first):
            ensure_provider_lifecycle(a)
            with self.assertRaises(ProviderLifecycleError):
                ensure_provider_lifecycle(b)


@skipUnless(connection.vendor == 'mysql', 'Requires isolated MySQL row locks')
@override_settings(LICENSE_ACCOUNT_ADMISSION_ENABLED=False)
class ProviderLifecycleMySQLTest(TransactionTestCase):
    def test_shared_operations_block_closure_and_do_not_block_other_provider(self):
        require_disposable_mysql(connection)
        store = resolve_store()
        with store_context(store):
            account = ClubAccount.objects.create_account(username='life-race', account_type='PROVIDER')
            other = ClubAccount.objects.create_account(username='life-other', account_type='PROVIDER')
            ensure_provider_lifecycle(account)
            ensure_provider_lifecycle(other)
        first, second, closed, unrelated = (Event() for _ in range(4))
        release_ops, release_close, release_other = (Event() for _ in range(3))

        def hold(actor, fn, entered, release):
            close_old_connections()
            try:
                with store_context(store), transaction.atomic():
                    fn(actor)
                    entered.set()
                    if not release.wait(15):
                        raise TimeoutError('lifecycle test did not release lock')
            finally:
                close_old_connections()

        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                a = pool.submit(hold, account, lock_provider_operation, first, release_ops)
                self.assertTrue(first.wait(10))
                b = pool.submit(hold, account, lock_provider_operation, second, release_ops)
                self.assertTrue(second.wait(10))
                c = pool.submit(hold, account, lock_provider_closure, closed, release_close)
                self.assertFalse(closed.wait(.5))
                d = pool.submit(hold, other, lock_provider_closure, unrelated, release_other)
                self.assertTrue(unrelated.wait(10))
                release_ops.set(); a.result(timeout=10); b.result(timeout=10)
                self.assertTrue(closed.wait(10))
                release_close.set(); release_other.set()
                c.result(timeout=10); d.result(timeout=10)
        finally:
            release_ops.set(); release_close.set(); release_other.set()
