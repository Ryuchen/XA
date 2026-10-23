"""Service picker ordering uses live eligibility and paid provider shares."""
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import Order, OrderProvider, OrderRefund
from orders.tests.factories import make_order, make_provider, make_service, make_service_category, make_user
from users.companion_picker import companion_service_amounts, recent_service_amounts
from users.models import EscortLevel, EscortProfile, EscortSchedule


@override_settings(KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class CompanionPickerTest(APITestCase):
    def setUp(self):
        self.boss = make_user()
        self.account = get_or_create_account_for_legacy_user(self.boss)
        self.service = make_service()
        self.low = EscortLevel.objects.create(name='初级', sort_order=1, commission_rate=30)
        self.high = EscortLevel.objects.create(name='高级', sort_order=5, commission_rate=20)
        self.client.force_authenticate(self.boss)

    def provider(self, **kwargs):
        user = make_provider(**kwargs)
        account = get_or_create_account_for_legacy_user(user)
        profile = user.escort_profile
        profile.account = account
        profile.save(update_fields=['account'])
        profile.service_items.add(self.service)
        return profile

    def order(self, profile, amount=1000, **kwargs):
        return make_order(self.boss, service=kwargs.pop('service', self.service),
                          customer_account=self.account, provider=profile.user,
                          provider_account=profile.account, status=kwargs.pop('status', 'COMPLETED'),
                          amount=amount, **kwargs)

    def rows(self, **params):
        response = self.client.get('/api/users/escorts/', {'picker': '1', 'service_item': self.service.pk, **params})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data['data']

    def test_four_slots_then_four_distinct_amount_leaders_then_levels(self):
        recommended = [self.provider(recommendation_slot=slot, level=self.low) for slot in (4, 2, 1, 3)]
        for profile in recommended:
            self.order(profile, 100000)
        popular = [self.provider(level=self.low) for _ in range(4)]
        for profile, amount in zip(popular, (1000, 4000, 3000, 2000)):
            self.order(profile, amount)
        low = self.provider(level=self.low, pass_tier='BLACK', pass_expires_at=timezone.now() + timedelta(days=1), rating_avg=5)
        high = self.provider(level=self.high)
        unset = self.provider()
        rows = self.rows()
        expected = [recommended[2], recommended[1], recommended[3], recommended[0],
                    popular[1], popular[2], popular[3], popular[0], high, low, unset]
        self.assertEqual([row['id'] for row in rows], [p.account_id for p in expected])
        self.assertEqual([row['picker_group'] for row in rows], ['recommended'] * 4 + ['popular'] * 4 + [''] * 3)
        self.assertEqual(rows[8]['level_name'], '高级')
        self.assertEqual(len({row['id'] for row in rows}), len(rows))

    def test_shares_count_once_include_legacy_identity_and_released_earned_share(self):
        first, second = self.provider(), self.provider()
        legacy = self.order(first, 300)
        Order.objects.filter(pk=legacy.pk).update(provider_account=None)
        single = self.order(first, 400)
        OrderProvider.objects.create(order=single, provider=first.user, provider_account=first.account, settlement_base=400)
        double = self.order(first, 1000, escort_mode='DOUBLE')
        OrderProvider.objects.create(order=double, provider=first.user, provider_account=first.account, settlement_base=500)
        OrderProvider.objects.create(order=double, provider=second.user, provider_account=second.account, settlement_base=500)
        released = self.order(second, 200)
        OrderProvider.objects.create(order=released, provider=second.user, provider_account=second.account,
                                     settlement_base=200, released_at=timezone.now())
        profiles = [first, second]
        with self.assertNumQueries(2):
            totals = recent_service_amounts(profiles)
        self.assertEqual(totals, {first.account_id: 1200, second.account_id: 700})

    def test_unrelated_or_old_partial_refunds_keep_the_two_query_budget(self):
        profile, other = self.provider(), self.provider()
        self.order(profile, 250)
        self.order(other, 99999, payment_status=Order.PaymentStatus.PARTIALLY_REFUNDED)
        self.order(profile, 99999, payment_status=Order.PaymentStatus.PARTIALLY_REFUNDED,
                   grabbed_at=timezone.now() - timedelta(days=31))
        with self.assertNumQueries(2):
            totals = recent_service_amounts([profile])
        self.assertEqual(totals, {profile.account_id: 250})

    def test_partial_legacy_and_team_shares_use_net_amounts_without_double_counting(self):
        first, second = self.provider(), self.provider()
        legacy = self.order(first, 1000, payment_status=Order.PaymentStatus.PARTIALLY_REFUNDED)
        OrderRefund.objects.create(order=legacy, amount=300)
        team = self.order(first, 1201, escort_mode='DOUBLE', payment_status=Order.PaymentStatus.PARTIALLY_REFUNDED)
        OrderRefund.objects.create(order=team, amount=201)
        for profile, base in ((first, 600), (second, 601)):
            OrderProvider.objects.create(order=team, provider=profile.user,
                                         provider_account=profile.account, settlement_base=base)
        totals = companion_service_amounts([first, second], include_recent=True)
        expected = {first.account_id: 1200, second.account_id: 500}
        self.assertEqual(totals['recent'], expected)
        self.assertEqual(totals['monthly'], expected)

    def test_old_unpaid_refunded_cancelled_gifts_and_child_orders_do_not_rank(self):
        profile = self.provider()
        valid = self.order(profile, 250)
        self.order(profile, 99999, status='CANCELLED')
        self.order(profile, 99999, payment_status='UNPAID')
        self.order(profile, 99999, payment_status='REFUNDED')
        self.order(profile, 99999, source_order=valid)
        gift = make_service(service_category=make_service_category(is_gift=True))
        self.order(profile, 99999, service=gift)
        old = self.order(profile, 99999)
        Order.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=31))
        old_team = self.order(profile, 99999)
        share = OrderProvider.objects.create(order=old_team, provider=profile.user,
                                             provider_account=profile.account, settlement_base=99999)
        OrderProvider.objects.filter(pk=share.pk).update(created_at=timezone.now() - timedelta(days=31))
        released = self.order(profile, 99999)
        OrderProvider.objects.create(order=released, provider=profile.user, provider_account=profile.account,
                                     settlement_base=0, released_at=timezone.now())
        self.assertEqual(recent_service_amounts([profile]), {profile.account_id: 250})

    def test_acceptance_date_is_used_when_available_and_ties_follow_level(self):
        low, high = self.provider(level=self.low), self.provider(level=self.high)
        old_created = self.order(low, 1000, grabbed_at=timezone.now())
        Order.objects.filter(pk=old_created.pk).update(created_at=timezone.now() - timedelta(days=45))
        self.order(high, 1000)
        self.order(low, 99999, grabbed_at=timezone.now() - timedelta(days=31))
        self.assertEqual([row['id'] for row in self.rows()], [high.account_id, low.account_id])

    def test_eligibility_gender_capacity_schedule_and_service_are_applied_before_ranking(self):
        good = self.provider(level=self.high, gender='FEMALE')
        self.order(good, 100)
        offline = self.provider(recommendation_slot=1, status='OFFLINE', gender='FEMALE')
        wrong_gender = self.provider(recommendation_slot=2, gender='MALE')
        unskilled = self.provider(recommendation_slot=3, gender='FEMALE')
        unskilled.service_items.clear()
        low = self.provider(recommendation_slot=4, gender='FEMALE', level=self.low)
        full = self.provider(gender='FEMALE', level=self.high)
        for _ in range(3):
            self.order(full, 99999, status='GRABBED')
        off_shift = self.provider(gender='FEMALE', level=self.high)
        EscortSchedule.objects.create(provider=off_shift.user, account=off_shift.account,
                                      weekday=(timezone.localdate().weekday() + 1) % 7, start_minute=0, end_minute=1440)
        self.service.required_level = self.high
        self.service.save(update_fields=['required_level'])
        for profile in (offline, wrong_gender, unskilled, low, off_shift):
            self.order(profile, 99999)
        self.assertEqual([row['id'] for row in self.rows(gender='FEMALE')], [good.account_id])

    def test_next_request_reads_changed_recommendations_orders_and_service_membership(self):
        first, second = self.provider(), self.provider()
        self.order(first, 100)
        self.assertEqual(self.rows()[0]['id'], first.account_id)
        self.order(second, 200)
        self.assertEqual(self.rows()[0]['id'], second.account_id)
        EscortProfile.objects.filter(pk=first.pk).update(recommendation_slot=1)
        self.assertEqual(self.rows()[0]['id'], first.account_id)
        first.service_items.clear()
        self.assertEqual([row['id'] for row in self.rows()], [second.account_id])

    def test_missing_activity_keeps_slots_then_levels_and_unset_levels_last(self):
        inactive = EscortLevel.objects.create(name='已停用', sort_order=99, commission_rate=0, is_active=False)
        unset = self.provider(level=inactive)
        low = self.provider(level=self.low)
        high = self.provider(level=self.high)
        recommended = self.provider(recommendation_slot=2)
        rows = self.rows()
        self.assertEqual([row['id'] for row in rows], [recommended.account_id, high.account_id, low.account_id, unset.account_id])
        self.assertEqual([row['picker_group'] for row in rows], ['recommended', '', '', ''])

    def test_query_count_does_not_grow_with_roster_size(self):
        self.provider()
        with CaptureQueriesContext(connection) as first_queries:
            first = self.rows()
        for _ in range(12):
            self.provider()
        with CaptureQueriesContext(connection) as larger_queries:
            larger = self.rows()
        self.assertEqual((len(first), len(larger)), (1, 13))
        self.assertLessEqual(len(larger_queries), len(first_queries) + 1)

    def test_picker_requires_authentication(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get('/api/users/escorts/', {'picker': '1'}).status_code, (401, 403))

    @patch('users.companion_picker.timezone.now', return_value=datetime(2026, 10, 8, 12, tzinfo=UTC))
    def test_monthly_amount_uses_shanghai_month_boundary_and_not_the_rolling_30_days(self, now):
        profile = self.provider()
        timestamps = [
            (datetime(2026, 9, 30, 15, 59, 59, tzinfo=UTC), 9000),
            (datetime(2026, 9, 30, 16, tzinfo=UTC), 1200),
            (datetime(2026, 10, 8, 10, tzinfo=UTC), 700),
            (datetime(2026, 10, 9, tzinfo=UTC), 99000),
        ]
        for accepted_at, amount in timestamps:
            self.order(profile, amount, grabbed_at=accepted_at)
        with self.assertNumQueries(2):
            totals = companion_service_amounts([profile], include_recent=True)
        self.assertEqual(totals['monthly'], {profile.account_id: 1900})
        self.assertEqual(totals['recent'], {profile.account_id: 10900})
        rows = self.rows()
        self.assertEqual(rows[0]['monthly_order_amount'], 1900)

    @patch('users.companion_picker.timezone.now', return_value=datetime(2026, 10, 8, 12, tzinfo=UTC))
    def test_monthly_team_shares_use_assignment_date_and_include_other_service_orders(self, now):
        first, second = self.provider(), self.provider()
        double = self.order(first, 1000, escort_mode='DOUBLE')
        Order.objects.filter(pk=double.pk).update(created_at=datetime(2026, 9, 29, tzinfo=UTC))
        first_share = OrderProvider.objects.create(order=double, provider=first.user,
                                                   provider_account=first.account, settlement_base=500)
        second_share = OrderProvider.objects.create(order=double, provider=second.user,
                                                    provider_account=second.account, settlement_base=500)
        OrderProvider.objects.filter(pk=first_share.pk).update(created_at=datetime(2026, 9, 30, 15, 59, tzinfo=UTC))
        OrderProvider.objects.filter(pk=second_share.pk).update(created_at=datetime(2026, 9, 30, 16, tzinfo=UTC))
        self.order(second, 750, service=make_service())
        self.assertEqual(companion_service_amounts([first, second])['monthly'], {second.account_id: 1250})
        self.assertEqual({row['id']: row['monthly_order_amount'] for row in self.rows()},
                         {first.account_id: 0, second.account_id: 1250})

    def test_directory_and_shortcuts_return_live_monthly_amount_without_pass_levels(self):
        profile = self.provider(recommendation_slot=1, pass_tier='BLACK', pass_expires_at=timezone.now() + timedelta(days=1))
        for params in ({'directory': '1'}, {'shortcuts': '1', 'service_item': self.service.pk}):
            with self.subTest(params=params):
                data = self.client.get('/api/users/escorts/', params).data['data']
                rows = data['recommended'] if 'shortcuts' in params else data
                self.assertEqual(rows[0]['monthly_order_amount'], 0)
                self.assertNotIn('active_pass_tier', rows[0])
        order = self.order(profile, 3210)
        self.assertEqual(self.rows()[0]['monthly_order_amount'], 3210)
        Order.objects.filter(pk=order.pk).update(payment_status='REFUNDED')
        self.assertEqual(self.rows()[0]['monthly_order_amount'], 0)
