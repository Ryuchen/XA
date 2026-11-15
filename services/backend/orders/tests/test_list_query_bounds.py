"""List cost must not grow one query per provider/order; eligibility stays intact."""
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.db import DatabaseError, connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderProvider
from orders.tests.factories import make_order, make_provider, make_service, make_user
from users.models import EscortProfile, EscortSchedule


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class ListQueryBoundsTest(APITestCase):
    def setUp(self):
        self.boss = make_user()
        self.boss_account = get_or_create_account_for_legacy_user(self.boss)
        self.service = make_service()

    def provider(self):
        user = make_provider(deposit_mode='EARNINGS')
        account = get_or_create_account_for_legacy_user(user)
        profile = user.escort_profile
        profile.account = account
        profile.save(update_fields=['account'])
        return user, account

    def login(self, account):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])

    def read(self, path):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['code'], 0)
            data = response.data['data']
        # Resolving a store must be checked on every request so disabling it
        # takes effect immediately. Keep its constant directory lookup separate
        # from the original business-query budgets, rather than hiding it in a
        # cross-request cache or relaxing the roster/order bounds.
        directory_queries = [query for query in queries if 'stores_store' in query['sql']]
        self.assertEqual(len(directory_queries),
                         int('stores.middleware.StoreMiddleware' in settings.MIDDLEWARE))
        return data, len(queries) - len(directory_queries)

    def pending(self):
        order = make_order(self.boss, service=self.service, customer_account=self.boss_account,
                           payment_status='PAID', provider_income=8000)
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=1))
        return order

    def test_provider_list_query_count_is_bounded_as_roster_grows(self):
        self.provider()
        self.login(self.boss_account)
        first, queries = self.read('/api/users/escorts/')
        for _ in range(20):
            self.provider()
        rows, larger_queries = self.read('/api/users/escorts/')
        self.assertEqual((len(first), len(rows)), (1, 21))
        self.assertLessEqual(larger_queries, queries + 1)
        # The fixed budget includes current store deposit configuration and
        # the batched WeChat-identity prefetch and the store capacity setting.
        self.assertLessEqual(larger_queries, 16)

    def test_provider_list_keeps_legacy_double_and_released_capacity_rules(self):
        full_user, full = self.provider()
        double_user, double = self.provider()
        released_user, released = self.provider()
        settled_user, settled = self.provider()
        legacy_user = make_provider()  # no account mapping, legacy-only rows
        for _ in range(3):
            make_order(self.boss, service=self.service, provider=full_user, status='GRABBED')
            make_order(self.boss, service=self.service, provider=legacy_user, status='IN_SERVICE')
            dual = make_order(self.boss, service=self.service, status='GRABBED',
                              provider=double_user, provider_account=double)
            OrderProvider.objects.create(order=dual, provider=double_user, provider_account=double)
            OrderProvider.objects.create(order=dual, provider=released_user, provider_account=released,
                                         released_at=timezone.now())
            other = make_order(self.boss, service=self.service, status='IN_SERVICE')
            OrderProvider.objects.create(order=other, provider=settled_user, provider_account=settled,
                                         settled_at=timezone.now())
        self.login(self.boss_account)
        rows, _ = self.read('/api/users/escorts/')
        self.assertEqual({row['id'] for row in rows}, {released.pk, settled.pk})
        self.assertNotIn(full.pk, {row['id'] for row in rows})

    def test_provider_list_prefetched_schedule_still_excludes_off_shift(self):
        off_user, off = self.provider()
        on_user, on = self.provider()
        today = timezone.localdate().weekday()
        EscortSchedule.objects.create(provider=off_user, account=off, weekday=(today+1) % 7,
                                      start_minute=0, end_minute=1440)
        EscortSchedule.objects.create(provider=on_user, account=on, weekday=today,
                                      start_minute=0, end_minute=1440)
        self.login(self.boss_account)
        rows, _ = self.read('/api/users/escorts/')
        self.assertEqual([row['id'] for row in rows], [on.pk])
        # A new request sees the changed schedule; no cross-request cache.
        EscortSchedule.objects.filter(provider=off_user).update(weekday=today)
        rows, _ = self.read('/api/users/escorts/')
        self.assertEqual({row['id'] for row in rows}, {on.pk, off.pk})

    def test_merged_assignment_prefetch_preserves_active_and_legacy_released_visibility(self):
        first_user, first = self.provider()
        second_user, second = self.provider()
        released_user, released = self.provider()
        order = make_order(self.boss, service=self.service, customer_account=self.boss_account,
            provider=first_user, provider_account=first, status='IN_SERVICE', payment_status='PAID',
            escort_mode='DOUBLE', required_provider_count=2)
        for user, account, name in ((first_user, first, '甲'), (second_user, second, '乙')):
            OrderProvider.objects.create(order=order, provider=user, provider_account=account,
                provider_name_snapshot=name, settlement_base=500, provider_income=400)
        OrderProvider.objects.create(order=order, provider=released_user, provider_account=None,
            provider_name_snapshot='历史转出', settlement_base=200, provider_income=160, released_at=timezone.now())
        for account, expected_income, was_released in ((first, 400, False), (second, 400, False),
                                                        (released, 160, True)):
            self.login(account)
            rows, _ = self.read('/api/orders/orders/')
            row = next(item for item in rows if item['id'] == order.pk)
            self.assertEqual({item['id'] for item in row['participants']}, {first.pk, second.pk})
            self.assertEqual((row['expected_income'], row['my_assignment_released']),
                             (expected_income, was_released))
            if was_released:
                self.assertEqual(row['my_assignment_departure'], 'TRANSFERRED')

    def test_unavailable_points_evidence_does_not_display_ordinary_income(self):
        user, account = self.provider()
        order = make_order(self.boss, service=self.service, customer_account=self.boss_account,
            provider=user, provider_account=account, status='IN_SERVICE', payment_status='PAID')
        OrderProvider.objects.create(order=order, provider=user, provider_account=account,
                                     settlement_base=500, provider_income=400)
        self.login(account)
        with patch('orders.points_service_payment.points_receipt_table_exists',
                   side_effect=DatabaseError('cannot inspect receipt schema')):
            rows, _ = self.read('/api/orders/orders/')
        row = next(item for item in rows if item['id'] == order.pk)
        self.assertIsNone(row['expected_income'])

    def test_pool_queries_are_bounded_and_action_changes_on_next_request(self):
        user, account = self.provider()
        self.login(account)
        self.pending()
        first, queries = self.read('/api/orders/orders/?status=pending')
        for _ in range(19):
            self.pending()
        rows, larger_queries = self.read('/api/orders/orders/?status=pending')
        self.assertEqual((len(first), len(rows)), (1, 20))
        self.assertTrue(all('GRAB' in row['allowed_actions'] for row in rows))
        self.assertLessEqual(larger_queries, queries + 1)
        # One shared prefetch loads complaint states for the whole page.
        # Shared qualification, refund and settlement prefetches plus the
        # current store capacity read remain independent of pool size.
        self.assertLessEqual(larger_queries, 22)
        EscortProfile.objects.filter(account=account).update(status='OFFLINE')
        rows, _ = self.read('/api/orders/orders/?status=pending')
        self.assertEqual(rows, [])

    def test_pool_personal_income_query_count_does_not_grow_without_level(self):
        user, account = self.provider()
        self.service.commission_policy = 'REGULAR'
        self.service.save(update_fields=['commission_policy'])
        self.login(account)
        self.pending()
        _, initial_queries = self.read('/api/orders/orders/?status=pending')
        for _ in range(19):
            self.pending()
        rows, larger_queries = self.read('/api/orders/orders/?status=pending')
        self.assertEqual(len(rows), 20)
        self.assertLessEqual(larger_queries, initial_queries + 1)

    def test_teammate_picker_queries_are_bounded_and_respect_capacity_and_schedule(self):
        requester_user, requester = self.provider()
        first_user, first = self.provider()
        service = make_service(name='双陪测试', billing_provider_count=2)
        order = make_order(self.boss, service=service, customer_account=self.boss_account,
                           payment_status='PAID', requires_boss_selection=True)
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=1))
        self.login(requester)
        url = f'/api/orders/orders/{order.pk}/teammates/'
        initial, queries = self.read(url)
        added = [self.provider() for _ in range(19)]
        rows, larger_queries = self.read(url)
        self.assertEqual(len(initial), 1)
        self.assertEqual(len(rows), 20)
        self.assertLessEqual(larger_queries, queries + 2)
        # Full-capacity and off-shift teammates must disappear on the next read.
        for _ in range(Order.MAX_CONCURRENT_ORDERS):
            make_order(self.boss, service=service, provider=first_user, provider_account=first, status='GRABBED')
        off_user, off = added[0]
        EscortSchedule.objects.create(provider=off_user, account=off,
                                      weekday=(timezone.localdate().weekday() + 1) % 7,
                                      start_minute=0, end_minute=1440)
        rows, _ = self.read(url)
        self.assertNotIn(first.pk, {row['id'] for row in rows})
        self.assertNotIn(off.pk, {row['id'] for row in rows})

    def test_prefetched_income_is_personal_and_does_not_leak_other_provider_share(self):
        first_user, first = self.provider()
        second_user, second = self.provider()
        order = make_order(self.boss, service=self.service, customer_account=self.boss_account,
                           provider=first_user, provider_account=first, status='COMPLETED',
                           provider_income=7000, escort_mode='DOUBLE')
        for user, account, income in [(first_user, first, 3000), (second_user, second, 4000)]:
            OrderProvider.objects.create(order=order, provider=user, provider_account=account,
                                         settlement_base=5000, provider_income=income)
        for account, expected in [(first, 3000), (second, 4000), (self.boss_account, 7000)]:
            self.login(account)
            rows, _ = self.read('/api/orders/orders/')
            self.assertEqual(rows[0]['expected_income'], expected)
