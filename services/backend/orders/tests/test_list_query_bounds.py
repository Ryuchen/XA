"""List cost must not grow one query per provider/order; eligibility stays intact."""
from datetime import timedelta

from django.db import connection
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
        user = make_provider()
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
        return data, len(queries)

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
        self.assertLessEqual(larger_queries, 13)  # Qualifications plus two roster-wide monthly amount aggregates.

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
        self.assertLessEqual(larger_queries, 16)  # Shared qualification/skill prefetches remain independent of pool size.
        EscortProfile.objects.filter(account=account).update(deposit_required=100)
        rows, _ = self.read('/api/orders/orders/?status=pending')
        self.assertEqual(rows, [])

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
