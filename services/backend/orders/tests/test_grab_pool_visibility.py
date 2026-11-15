"""Entry pass visibility from real wallet purchases and stored snapshots."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import DatabaseError, close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.grab_config import save_grab_window
from orders.grab_pool_snapshot import create_initial_snapshot
from orders.grab_pool_visibility import initial_mixed_group_visible_at, initial_visibility
from orders.models import OrderGrabPoolPassSnapshot, OrderGrabPoolSnapshot
from orders.tests.factories import make_order, make_provider, make_service, make_user
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.tests_hosts import xinan_store
from wallet.models import SystemConfig
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class GrabPoolVisibilityTest(TransactionTestCase):
    def setUp(self):
        save_grab_window(30, 0, lambda *args: None)
        self.order = make_order(make_user(), service=make_service(), payment_status='PAID')

    def provider(self, tier='', *, offline=False, bind=True):
        user = make_provider(status='OFFLINE' if offline else 'AVAILABLE')
        account = get_or_create_account_for_legacy_user(user) if bind else None
        profile = user.escort_profile
        profile.account = account
        profile.pass_tier = tier
        profile.pass_expires_at = timezone.now() + timedelta(hours=1) if tier else None
        profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at'])
        return user, account, profile

    def open(self):
        with transaction.atomic():
            return create_initial_snapshot(self.order.pk)

    def test_offline_and_expired_card_remain_at_entry_tier(self):
        _, account, profile = self.provider('SILVER', offline=True)
        pool = self.open()
        self.assertTrue(pool.pass_snapshot_complete)
        self.assertEqual(OrderGrabPoolPassSnapshot.objects.get(pool=pool).tier, 'SILVER')
        profile.pass_tier, profile.pass_expires_at = '', timezone.now() - timedelta(seconds=1)
        profile.save(update_fields=['pass_tier', 'pass_expires_at'])
        SystemConfig.objects.update_or_create(key='provider_pass_delay_seconds_SILVER', defaults={'value': '40'})
        visibility = initial_visibility(pool.pk, account.pk)
        self.assertEqual((visibility.entry_tier, visibility.delay_seconds), ('SILVER', 60))
        self.assertEqual(visibility.visible_at, pool.opened_at + timedelta(seconds=60))

    def test_mixed_group_waits_for_no_pass_and_uses_entry_snapshot(self):
        pool = self.open()
        self.assertEqual(initial_mixed_group_visible_at(pool.pk), pool.opened_at + timedelta(seconds=300))
        # A later configuration edit cannot make this order visible early.
        SystemConfig.objects.update_or_create(key='provider_pass_delay_seconds_NONE', defaults={'value': '0'})
        self.assertEqual(initial_mixed_group_visible_at(pool.pk), pool.opened_at + timedelta(seconds=300))

    def test_mixed_group_does_not_accept_incomplete_or_invalid_snapshot(self):
        self.provider('GOLD')
        pool = self.open()
        OrderGrabPoolPassSnapshot.objects.filter(pool=pool).delete()
        with self.assertRaisesMessage(ValueError, '快照不完整'):
            initial_mixed_group_visible_at(pool.pk)
        OrderGrabPoolSnapshot.objects.filter(pk=pool.pk).update(pass_snapshot_count=0,
            visibility_delays={'BLACK': 0, 'GOLD': 30, 'SILVER': 60, 'BRONZE': 120, '': 1})
        with self.assertRaisesMessage(ValueError, '延迟快照异常'):
            initial_mixed_group_visible_at(pool.pk)
        with self.assertRaisesMessage(ValueError, '本店抢单池不存在'):
            initial_mixed_group_visible_at(pool.pk + 1000)

    def test_expiry_during_capture_is_not_active_at_entry(self):
        _, account, profile = self.provider('SILVER')
        first_clock = timezone.now()
        profile.pass_expires_at = first_clock + timedelta(seconds=2)
        profile.save(update_fields=['pass_expires_at'])
        with patch('orders.grab_pool_snapshot.timezone') as clock:
            clock.now.side_effect = [first_clock, first_clock + timedelta(seconds=5)]
            clock.is_naive.side_effect = timezone.is_naive
            pool = self.open()
        self.assertEqual((pool.pass_snapshot_count, initial_visibility(pool.pk, account.pk).delay_seconds), (0, 300))

    def test_purchasing_after_entry_does_not_advance_visibility(self):
        user, account, profile = self.provider()
        wallet = get_wallet(account=account, user=user)
        wallet.balance = 100000; wallet.save(update_fields=['balance'])
        pool = self.open()
        client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        response = client.post('/api/users/provider-pass/', {'tier': 'GOLD', 'days': 1, 'client_request_id': 'pool-buy-pass'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        profile.refresh_from_db(); self.assertEqual(profile.pass_tier, 'GOLD')
        visibility = initial_visibility(pool.pk, account.pk)
        self.assertEqual((visibility.entry_tier, visibility.delay_seconds), ('', 300))
        self.assertEqual(visibility.visible_at, pool.opened_at + timedelta(seconds=300))

    def test_new_provider_and_unverifiable_legacy_identity(self):
        legacy, _, profile = self.provider('GOLD', bind=False)
        pool = self.open()
        _, new_account, _ = self.provider('GOLD')
        self.assertEqual(initial_visibility(pool.pk, new_account.pk).delay_seconds, 300)
        account = get_or_create_account_for_legacy_user(legacy)
        profile.account = account; profile.save(update_fields=['account'])
        with self.assertRaisesMessage(ValueError, '归属无法核对'):
            initial_visibility(pool.pk, account.pk)

    def test_partial_snapshot_and_invalid_delay_fail_closed(self):
        _, account, _ = self.provider('GOLD')
        pool = self.open()
        OrderGrabPoolSnapshot.objects.filter(pk=pool.pk).update(pass_snapshot_complete=False)
        with self.assertRaisesMessage(ValueError, '快照不完整'):
            initial_visibility(pool.pk, account.pk)
        with transaction.atomic(), self.assertRaisesMessage(ValueError, '快照不完整'):
            create_initial_snapshot(self.order.pk)
        OrderGrabPoolSnapshot.objects.filter(pk=pool.pk).update(pass_snapshot_complete=True, visibility_delays={'GOLD': 0})
        with self.assertRaisesMessage(ValueError, '延迟快照异常'):
            initial_visibility(pool.pk, account.pk)

    def test_missing_record_cannot_be_mistaken_for_no_card(self):
        _, account, _ = self.provider('GOLD')
        pool = self.open()
        self.assertEqual(pool.pass_snapshot_count, 1)
        OrderGrabPoolPassSnapshot.objects.filter(pool=pool).delete()
        with self.assertRaisesMessage(ValueError, '快照不完整'):
            initial_visibility(pool.pk, account.pk)
        with transaction.atomic(), self.assertRaisesMessage(ValueError, '快照不完整'):
            create_initial_snapshot(self.order.pk)

    def test_card_snapshot_failure_rolls_back_entire_pool(self):
        self.provider('GOLD')
        with patch('orders.grab_pool_snapshot.OrderGrabPoolPassSnapshot.objects.bulk_create', side_effect=DatabaseError('failed')):
            with self.assertRaises(DatabaseError), transaction.atomic():
                create_initial_snapshot(self.order.pk)
        self.assertFalse(OrderGrabPoolSnapshot.objects.exists())
        self.assertFalse(OrderGrabPoolPassSnapshot.objects.exists())

    def test_purchase_and_entry_serialize_on_real_mysql(self):
        if connection.vendor != 'mysql':
            self.skipTest('requires isolated MySQL row locks')
        user, account, _ = self.provider()
        wallet = get_wallet(account=account, user=user)
        wallet.balance = 100000; wallet.save(update_fields=['balance'])
        token = issue_account_tokens(account)[0]
        started = Event()
        def buy():
            close_old_connections()
            try:
                client = APIClient(HTTP_AUTHORIZATION='Bearer ' + token)
                started.set()
                return client.post('/api/users/provider-pass/', {'tier': 'GOLD', 'days': 1,
                    'client_request_id': 'mysql-pool-buy'}, format='json').data
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=1) as executor:
            with transaction.atomic():
                pool = create_initial_snapshot(self.order.pk)
                future = executor.submit(buy)
                self.assertTrue(started.wait(5))
                self.assertFalse(future.done())
            self.assertEqual(future.result(timeout=10)['code'], 0)
        self.assertEqual(initial_visibility(pool.pk, account.pk).delay_seconds, 300)


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated databases')
class GrabPoolVisibilityMultiStoreTest(TransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    reset_sequences = True

    def test_identical_pool_and_account_ids_use_each_store_entry_tier(self):
        ids = []
        stores = (resolve_store(), xinan_store())
        for store, tier, no_pass_delay in zip(stores, ('GOLD', 'BRONZE'), (300, 600)):
            with store_context(store):
                save_grab_window(30, 0, lambda *args: None)
                SystemConfig.objects.update_or_create(key='provider_pass_delay_seconds_NONE',
                                                       defaults={'value': str(no_pass_delay)})
                user = make_provider(status='OFFLINE')
                account = get_or_create_account_for_legacy_user(user)
                profile = user.escort_profile
                profile.account, profile.pass_tier = account, tier
                profile.pass_expires_at = timezone.now() + timedelta(hours=1)
                profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at'])
                order = make_order(make_user(), service=make_service(), payment_status='PAID')
                with transaction.atomic():
                    pool = create_initial_snapshot(order.pk)
                ids.append((pool.pk, account.pk))
        self.assertEqual(ids[0], ids[1])
        for store, expected, no_pass_delay in zip(stores, (('GOLD', 30), ('BRONZE', 120)), (300, 600)):
            with store_context(store):
                result = initial_visibility(*ids[0])
                self.assertEqual((result.entry_tier, result.delay_seconds), expected)
                pool = OrderGrabPoolSnapshot.objects.get(pk=ids[0][0])
                self.assertEqual(initial_mixed_group_visible_at(pool.pk),
                                 pool.opened_at + timedelta(seconds=no_pass_delay))
