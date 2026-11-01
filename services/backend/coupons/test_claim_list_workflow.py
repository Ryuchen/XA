"""The claim centre uses real account and coupon state at a bounded query cost."""
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from coupons.models import Coupon
from orders.tests.factories import make_service, make_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class CouponClaimListWorkflowTest(APITransactionTestCase):
    def setUp(self):
        user = make_user()
        self.account = get_or_create_account_for_legacy_user(user)
        link_legacy_relations(user, self.account)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])

    def rows(self):
        response = self.client.get('/api/coupons/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['code'], 0, response.data)
        return {row['id']: row for row in response.data['data']}

    def test_ordinary_coupon_count_does_not_add_one_query_per_coupon(self):
        first = Coupon.objects.create(name='首张真实优惠券', discount_type='DIRECT', amount=1000)
        claimed = self.client.post(f'/api/coupons/{first.pk}/claim/')
        self.assertEqual(claimed.data['code'], 0, claimed.data)
        with CaptureQueriesContext(connection) as one:
            initial = self.rows()
        self.assertTrue(initial[first.pk]['is_claimed'])

        Coupon.objects.bulk_create([
            Coupon(name=f'其他优惠券 {index}', discount_type='DIRECT', amount=1000)
            for index in range(19)
        ])
        with CaptureQueriesContext(connection) as many:
            rows = self.rows()
        self.assertEqual(len(rows), 20)
        self.assertTrue(rows[first.pk]['is_claimed'])
        self.assertEqual(sum(row['is_claimed'] for row in rows.values()), 1)
        self.assertLessEqual(len(many), len(one) + 1, (len(one), len(many)))

    def test_qualification_claim_is_shared_across_templates_for_same_period(self):
        service = make_service(experience_kind='NEW_VALUE', platform='PC')
        first = Coupon.objects.create(name='新客资格 A', discount_type='QUALIFICATION', amount=0,
            service_ids=[service.pk], experience_kind='NEW_VALUE', platform='PC')
        second = Coupon.objects.create(name='新客资格 B', discount_type='QUALIFICATION', amount=0,
            service_ids=[service.pk], experience_kind='NEW_VALUE', platform='PC')
        result = self.client.post(f'/api/coupons/{first.pk}/claim/')
        self.assertEqual(result.data['code'], 0, result.data)
        rows = self.rows()
        self.assertTrue(rows[first.pk]['is_claimed'])
        self.assertTrue(rows[second.pk]['is_claimed'])

    def test_daily_qualification_scope_keeps_platforms_and_accounts_separate(self):
        pc_service = make_service(experience_kind='DAILY_VALUE', platform='PC')
        mobile_service = make_service(experience_kind='DAILY_VALUE', platform='MOBILE')
        pc = Coupon.objects.create(name='端游每日资格', discount_type='QUALIFICATION', amount=0,
            service_ids=[pc_service.pk], experience_kind='DAILY_VALUE', platform='PC')
        mobile = Coupon.objects.create(name='手游每日资格', discount_type='QUALIFICATION', amount=0,
            service_ids=[mobile_service.pk], experience_kind='DAILY_VALUE', platform='MOBILE')
        result = self.client.post(f'/api/coupons/{pc.pk}/claim/')
        self.assertEqual(result.data['code'], 0, result.data)
        rows = self.rows()
        self.assertTrue(rows[pc.pk]['is_claimed'])
        self.assertFalse(rows[mobile.pk]['is_claimed'])

        other = make_user()
        other_account = get_or_create_account_for_legacy_user(other)
        link_legacy_relations(other, other_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(other_account)[0])
        rows = self.rows()
        self.assertFalse(rows[pc.pk]['is_claimed'])
        self.assertFalse(rows[mobile.pk]['is_claimed'])
