"""Provider bans serialize with dispatch; these checks need real row locks."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, current_thread
from unittest.mock import patch

from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.ban_utils import apply_ban
from console.models import AccountBan
from console.views import _capacity_reject_reason, _provider_active_orders
from orders.models import Order
from orders.services import lock_provider_dispatch
from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user, make_wallet
from users.models import EscortProfile
from wallet.models import Transaction, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
@skipUnlessDBFeature('has_select_for_update')
class ProviderBanDispatchRaceTest(TransactionTestCase):
    def setUp(self):
        self.boss = make_user()
        self.boss_account = get_or_create_account_for_legacy_user(self.boss)
        self.wallet = make_wallet(self.boss, balance=50000)
        self.provider = make_provider()
        self.provider_account = get_or_create_account_for_legacy_user(self.provider)
        EscortProfile.objects.filter(user=self.provider).update(account=self.provider_account)
        self.profile = EscortProfile.objects.get(user=self.provider)
        staff = make_console_user(['escort:view', 'user:ban', 'order:view', 'order:dispatch'])
        self.staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = self.staff_account
        staff.admin_membership.save(update_fields=['account'])
        self.staff_token, _ = issue_account_tokens(self.staff_account)
        self.boss_token, _ = issue_account_tokens(self.boss_account)
        self.service = make_service(price=10000)
        self.order = make_order(self.boss, service=self.service, customer_account=self.boss_account,
            dispatch_route='STAFF', auto_cancel_at=timezone.now() + timedelta(minutes=5))

    def post(self, token, path, data):
        close_old_connections()
        try:
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
            response = client.post(path, data, format='json')
            return response.status_code, response.data
        finally:
            close_old_connections()

    def ban(self):
        return self.post(self.staff_token, f'/api/admin/escorts/{self.profile.pk}/ban/', {'reason': '并发验收'})

    def assign(self):
        return self.post(self.staff_token, f'/api/admin/orders/{self.order.pk}/assign/',
                         {'provider_id': self.provider_account.pk})

    def test_assignment_wins_non_forced_ban_rechecks_active_orders(self):
        checked, release, ban_started, ban_checked = Event(), Event(), Event(), Event()

        def hold_capacity(*args, **kwargs):
            result = _capacity_reject_reason(*args, **kwargs)
            checked.set()
            if not release.wait(15):
                raise AssertionError('Assignment lock was not released')
            return result

        def observe_ban(account):
            ban_checked.set()
            return _provider_active_orders(account)

        def observe_ban_lock(ids):
            ban_started.set()
            return lock_provider_dispatch(ids)

        with patch('console.views._capacity_reject_reason', side_effect=hold_capacity), \
             patch('console.views._provider_active_orders', side_effect=observe_ban), \
             patch('console.views.lock_provider_dispatch', side_effect=observe_ban_lock), \
             patch('console.views.notify_order_update'), patch('console.views._push_message_safe'), \
             ThreadPoolExecutor(max_workers=2) as workers:
            assign = workers.submit(self.assign)
            try:
                self.assertTrue(checked.wait(10), 'Assignment did not hold the capacity lock')
                ban = workers.submit(self.ban)
                self.assertTrue(ban_started.wait(10), 'Ban did not attempt the provider lock')
                self.assertFalse(ban_checked.wait(1), 'Ban checked the order before taking the provider lock')
            finally:
                release.set()
            assign_status, assign_data = assign.result(timeout=15)
            ban_status, ban_data = ban.result(timeout=15)

        self.assertEqual((assign_status, assign_data.get('code')), (200, 0), assign_data)
        self.assertEqual((ban_status, ban_data.get('code')), (409, 409), ban_data)
        self.order.refresh_from_db()
        self.provider_account.refresh_from_db()
        self.assertEqual((self.order.status, self.order.provider_account_id), ('GRABBED', self.provider_account.pk))
        self.assertTrue(self.provider_account.can_login)
        self.assertFalse(AccountBan.objects.filter(account=self.provider_account, status='ACTIVE').exists())

    def test_ban_wins_assignment_rechecks_fresh_account(self):
        locked, release, attempted = Event(), Event(), Event()

        def hold_ban(*args, **kwargs):
            locked.set()
            if not release.wait(15):
                raise AssertionError('Ban lock was not released')
            return apply_ban(*args, **kwargs)

        def observe_assignment_lock(ids):
            attempted.set()
            return lock_provider_dispatch(ids)

        with patch('console.views.apply_ban', side_effect=hold_ban), \
             patch('orders.services.lock_provider_dispatch', side_effect=observe_assignment_lock), \
             patch('console.views.notify_order_update'), patch('console.views._push_message_safe'), \
             ThreadPoolExecutor(max_workers=2) as workers:
            ban = workers.submit(self.ban)
            try:
                self.assertTrue(locked.wait(10), 'Ban did not hold the capacity lock')
                assign = workers.submit(self.assign)
                self.assertTrue(attempted.wait(10), 'Assignment did not attempt the provider lock')
            finally:
                release.set()
            ban_status, ban_data = ban.result(timeout=15)
            _, assign_data = assign.result(timeout=15)

        self.assertEqual((ban_status, ban_data.get('code')), (200, 0), ban_data)
        self.assertNotEqual(assign_data.get('code'), 0, assign_data)
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.provider_account_id), ('PENDING', None))

    def test_ban_after_order_serializer_before_dispatch_does_not_debit_wallet(self):
        checked, release = Event(), Event()

        def before_provider_lock(ids):
            if current_thread().name == 'boss-checkout':
                checked.set()
                if not release.wait(15):
                    raise AssertionError('Checkout lock was not released')
            return lock_provider_dispatch(ids)

        def checkout():
            current_thread().name = 'boss-checkout'
            return self.post(self.boss_token, '/api/orders/create/', {
                'service_id': self.service.pk, 'provider_id': self.provider_account.pk,
                'client_request_id': 'ban-race-checkout',
            })

        with patch('orders.views.lock_provider_dispatch', side_effect=before_provider_lock), \
             patch('orders.views.notify_order_update'), patch('orders.views._schedule_auto_cancel'), \
             ThreadPoolExecutor(max_workers=2) as workers:
            purchase = workers.submit(checkout)
            try:
                self.assertTrue(checked.wait(10), 'Checkout did not reach the provider lock')
                ban_status, ban_data = workers.submit(self.ban).result(timeout=15)
                self.assertEqual((ban_status, ban_data.get('code')), (200, 0), ban_data)
            finally:
                release.set()
            _, purchase_data = purchase.result(timeout=15)

        self.assertNotEqual(purchase_data.get('code'), 0, purchase_data)
        self.assertEqual(Wallet.objects.get(pk=self.wallet.pk).balance, 50000)
        self.assertFalse(Transaction.objects.filter(wallet_id=self.wallet.pk, tx_type='PAY').exists())
        self.assertEqual(Order.objects.count(), 1)  # The original pending test order only.
