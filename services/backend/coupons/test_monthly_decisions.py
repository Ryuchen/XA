from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from coupons.benefits import issue
from coupons.models import Coupon, UserCoupon, VipMonthlyCouponGrant
from coupons.monthly import MonthlyCouponEvidenceError, issue_monthly_coupons
from engagement.models import ClubOperationConfig, VipTier
from orders.tests.factories import make_boss_type, make_user


class MonthlyCouponDecisionsTest(TestCase):
    def setUp(self):
        boss = make_user()
        self.account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, self.account)
        self.low = VipTier.objects.create(level=1, boss_type=make_boss_type(), recharge_threshold=100,
            monthly_coupon_count=0, monthly_discount=95)
        self.high = VipTier.objects.create(level=2, boss_type=make_boss_type(), recharge_threshold=200,
            monthly_coupon_count=2, monthly_discount=80)
        self.now = datetime(2026, 11, 7, 12, tzinfo=ZoneInfo('Asia/Shanghai'))

    def tier(self, tier):
        self.account.boss_type = tier.boss_type
        self.account.save(update_fields=['boss_type'])

    def coupons(self):
        return UserCoupon.objects.filter(account=self.account, coupon__grant_key__startswith='vip-month:').select_related('coupon').order_by('pk')

    def test_zero_issuance_blocks_same_month_upgrade_but_next_month_uses_new_tier(self):
        self.tier(self.low)
        grant = issue_monthly_coupons(self.account, self.low.pk, now=self.now, allow_new_period=True)
        self.assertEqual((grant.issued_count, self.coupons().count()), (0, 0))
        self.tier(self.high)
        again = issue_monthly_coupons(self.account, self.high.pk, now=self.now)
        self.assertEqual((again.pk, again.terms_frozen, self.coupons().count()), (grant.pk, True, 0))
        future = datetime(2026, 12, 1, 0, tzinfo=ZoneInfo('Asia/Shanghai'))
        next_grant = issue_monthly_coupons(self.account, self.high.pk, now=future, allow_new_period=True)
        self.assertEqual((next_grant.issued_count, self.coupons().count()), (2, 2))
        self.assertEqual({row.coupon.discount_rate for row in self.coupons()}, {80})

    def test_same_tier_edits_change_unused_terms_but_not_count_or_used_terms(self):
        self.tier(self.high)
        issue_monthly_coupons(self.account, self.high.pk, now=self.now, allow_new_period=True)
        used, unused = list(self.coupons())
        used.status = 'USED'; used.save(update_fields=['status'])
        self.high.monthly_coupon_count = 1
        self.high.monthly_discount = 85
        self.high.save(update_fields=['monthly_coupon_count', 'monthly_discount'])
        issue_monthly_coupons(self.account, self.high.pk, now=self.now)
        used.coupon.refresh_from_db(); unused.coupon.refresh_from_db()
        self.assertEqual((self.coupons().count(), used.coupon.discount_rate, unused.coupon.discount_rate), (2, 80, 85))

    def test_upgrade_does_not_reprice_or_top_up_and_remains_frozen_after_return(self):
        self.low.monthly_coupon_count = 1; self.low.save(update_fields=['monthly_coupon_count'])
        self.tier(self.low)
        grant = issue_monthly_coupons(self.account, self.low.pk, now=self.now, allow_new_period=True)
        row = self.coupons().get()
        self.tier(self.high)
        issue_monthly_coupons(self.account, self.high.pk, now=self.now)
        self.tier(self.low)
        self.low.monthly_discount = 90; self.low.save(update_fields=['monthly_discount'])
        issue_monthly_coupons(self.account, self.low.pk, now=self.now)
        grant.refresh_from_db(); row.refresh_from_db(); row.coupon.refresh_from_db()
        self.assertEqual((grant.terms_frozen, self.coupons().count(), row.coupon.discount_rate), (True, 1, 95))
        self.assertTrue(row.vip_terms_locked_by_downgrade)

    def test_known_historical_coupon_is_adopted_without_guessing_tier_or_changing_terms(self):
        self.tier(self.high)
        prefix = f'vip-month:{self.account.pk}:1:2026-11'
        prior = issue(self.account, prefix + ':0', name='历史月券', rate=90)[0]
        grant = issue_monthly_coupons(self.account, self.high.pk, now=self.now)
        prior.refresh_from_db(); prior.coupon.refresh_from_db()
        self.assertEqual((grant.source, grant.tier_id_snapshot, grant.issued_count, grant.terms_frozen), ('LEGACY', None, 1, True))
        self.assertEqual((self.coupons().count(), prior.coupon.discount_rate), (1, 90))
        self.assertTrue(prior.vip_terms_locked_by_downgrade)

    def test_absent_historical_evidence_requires_explicit_policy_and_orphans_do_not_count(self):
        self.tier(self.high)
        with self.assertRaises(MonthlyCouponEvidenceError):
            issue_monthly_coupons(self.account, self.high.pk, now=self.now)
        self.assertFalse(VipMonthlyCouponGrant.objects.exists())
        Coupon.objects.create(name='历史不完整', discount_type='RATE', amount=1,
            grant_key=f'vip-month:{self.account.pk}:1:2026-11:0:0', is_public=False)
        with self.assertRaises(MonthlyCouponEvidenceError):
            issue_monthly_coupons(self.account, self.high.pk, now=self.now, allow_new_period=True)
        self.assertFalse(VipMonthlyCouponGrant.objects.exists())

    def test_disabled_vip_and_partial_write_failure_never_issue(self):
        self.tier(self.high)
        ClubOperationConfig.objects.update_or_create(pk=1, defaults={'vip_enabled':False})
        with self.assertRaises(MonthlyCouponEvidenceError):
            issue_monthly_coupons(self.account, self.high.pk, now=self.now, allow_new_period=True)
        ClubOperationConfig.objects.filter(pk=1).update(vip_enabled=True)
        calls = []
        def fail_second(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError('rollback')
            return issue(*args, **kwargs)
        with patch('coupons.monthly.issue', side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                issue_monthly_coupons(self.account, self.high.pk, now=self.now, allow_new_period=True)
        self.assertFalse(VipMonthlyCouponGrant.objects.exists())
        self.assertFalse(self.coupons().exists())
