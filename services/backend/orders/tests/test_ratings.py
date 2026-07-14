"""评分聚合函数 apply_escort_rating / rollback_escort_rating 的单元测试。"""

from decimal import Decimal

from django.test import TestCase

from orders.ratings import apply_escort_rating, rollback_escort_rating
from orders.tests.factories import make_provider, make_user


class ApplyEscortRatingTest(TestCase):
    def setUp(self):
        self.provider = make_provider()
        self.profile = self.provider.escort_profile

    def test_first_rating_sets_avg_and_count(self):
        apply_escort_rating(self.provider.id, 4)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.rating_count, 1)
        self.assertEqual(self.profile.rating_avg, Decimal('4.00'))

    def test_multiple_ratings_average(self):
        apply_escort_rating(self.provider.id, 4)
        apply_escort_rating(self.provider.id, 2)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.rating_count, 2)
        self.assertEqual(self.profile.rating_avg, Decimal('3.00'))

    def test_average_rounds_to_two_decimals(self):
        apply_escort_rating(self.provider.id, 5)
        apply_escort_rating(self.provider.id, 4)
        apply_escort_rating(self.provider.id, 4)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.rating_count, 3)
        self.assertEqual(self.profile.rating_avg, Decimal('4.33'))

    def test_none_provider_is_noop(self):
        apply_escort_rating(None, 5)  # 不抛异常即可
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.rating_count, 0)

    def test_provider_without_profile_is_noop(self):
        customer = make_user()  # 普通用户无陪玩档案
        apply_escort_rating(customer.id, 5)  # 不抛异常即可


class RollbackEscortRatingTest(TestCase):
    def setUp(self):
        self.provider = make_provider()
        self.profile = self.provider.escort_profile

    def test_rollback_to_zero_when_last_one(self):
        apply_escort_rating(self.provider.id, 5)
        rollback_escort_rating(self.provider.id, 5)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.rating_count, 0)
        self.assertEqual(self.profile.rating_avg, Decimal('0.00'))

    def test_rollback_recomputes_average(self):
        apply_escort_rating(self.provider.id, 4)
        apply_escort_rating(self.provider.id, 2)
        rollback_escort_rating(self.provider.id, 2)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.rating_count, 1)
        self.assertEqual(self.profile.rating_avg, Decimal('4.00'))

    def test_rollback_on_empty_is_noop(self):
        rollback_escort_rating(self.provider.id, 5)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.rating_count, 0)
        self.assertEqual(self.profile.rating_avg, Decimal('0.00'))

    def test_none_provider_is_noop(self):
        rollback_escort_rating(None, 5)  # 不抛异常即可

    def test_apply_then_rollback_is_symmetric(self):
        apply_escort_rating(self.provider.id, 5)
        apply_escort_rating(self.provider.id, 3)
        apply_escort_rating(self.provider.id, 1)
        rollback_escort_rating(self.provider.id, 1)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.rating_count, 2)
        self.assertEqual(self.profile.rating_avg, Decimal('4.00'))
