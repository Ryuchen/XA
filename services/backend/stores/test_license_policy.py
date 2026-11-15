from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from stores.license_policy import (CORE_ACTIONS, LicenseGrant, TIERS, can_add_account, evaluate,
                                   evaluate_scheduled, feature_mode)


class LicensePolicyTests(SimpleTestCase):
    def setUp(self):
        china = ZoneInfo('Asia/Shanghai')
        self.start = datetime(2026, 11, 1, 0, 0, tzinfo=china)
        self.end = datetime(2027, 11, 1, 0, 0, tzinfo=china)
        self.grant = LicenseGrant('shenglang', 'BASIC', self.start, self.end, 7 * 86400)

    def test_tiers_and_annual_prices(self):
        self.assertEqual([(x.code, x.boss_limit, x.provider_limit, x.annual_price_fen) for x in TIERS.values()],
                         [('BASIC', 500, 500, 288000), ('PRO', 1500, 2000, 388000),
                          ('ULTRA', None, None, 588000)])
        self.assertTrue(all(tier.order_limit is None for tier in TIERS.values()))

    def test_boundaries_and_core_business_are_available_in_every_state(self):
        points = [(self.start-timedelta(microseconds=1), 'NOT_YET_ACTIVE', False, False),
                  (self.start, 'ACTIVE', True, True),
                  (self.end-timedelta(microseconds=1), 'ACTIVE', True, True),
                  (self.end, 'GRACE', True, False),
                  (self.end+timedelta(days=7)-timedelta(microseconds=1), 'GRACE', True, False),
                  (self.end+timedelta(days=7), 'EXPIRED', False, False)]
        for now, status, new, open_store in points:
            result = evaluate(self.grant, store_slug='shenglang', now=now)
            self.assertEqual((result.status, result.allow_new_accounts, result.allow_new_benefits,
                              result.can_open_store), (status, new, new, open_store))
            self.assertTrue(result.allow_existing_business)
            self.assertTrue(result.allow_existing_benefits)

    def test_cached_grant_time_progresses_in_utc_and_zero_grace(self):
        grant = replace(self.grant, grace_seconds=0)
        result = evaluate(grant, store_slug='shenglang', now=self.end.astimezone(timezone.utc))
        self.assertEqual(result.status, 'EXPIRED')
        self.assertTrue(result.allow_existing_benefits)
        self.assertFalse(result.allow_new_accounts)
        instant = self.end + timedelta(days=3)
        self.assertEqual(evaluate(self.grant, store_slug='shenglang', now=instant).status, 'GRACE')
        shortened = replace(self.grant, grace_seconds=2 * 86400)
        self.assertEqual(evaluate(shortened, store_slug='shenglang', now=instant).status, 'EXPIRED')

    def test_missing_mismatched_invalid_or_revoked_grant_fails_closed_for_new_use(self):
        bad = (None, replace(self.grant, tier='OTHER'), replace(self.grant, tier=[]),
               replace(self.grant, store_slug='xinan'), replace(self.grant, grace_seconds=-1),
               replace(self.grant, starts_at=self.end), replace(self.grant, revoked=True),
               replace(self.grant, starts_at=self.start.replace(tzinfo=None)))
        for grant in bad:
            result = evaluate(grant, store_slug='shenglang', now=self.start)
            self.assertEqual(result.status, 'UNLICENSED')
            self.assertFalse(result.allow_new_accounts)
            self.assertFalse(result.allow_new_benefits)
            self.assertTrue(result.allow_existing_business)
            self.assertTrue(result.allow_existing_benefits)
        with self.assertRaises(ValueError):
            evaluate(self.grant, store_slug='shenglang', now=self.start.replace(tzinfo=None))

    def test_quotas_and_downgrade_keep_existing_users(self):
        for tier, boss_limit, provider_limit in [('BASIC', 500, 500), ('PRO', 1500, 2000)]:
            decision = evaluate(replace(self.grant, tier=tier), store_slug='shenglang', now=self.start)
            for kind, limit in [('BOSS', boss_limit), ('PROVIDER', provider_limit)]:
                self.assertTrue(can_add_account(decision, kind, used=limit-1))
                self.assertFalse(can_add_account(decision, kind, used=limit))
                self.assertFalse(can_add_account(decision, kind, used=limit+100))
            self.assertTrue(decision.allow_existing_business)
        unlimited = evaluate(replace(self.grant, tier='ULTRA'), store_slug='shenglang', now=self.start)
        self.assertTrue(can_add_account(unlimited, 'BOSS', used=1000000))
        self.assertTrue(can_add_account(unlimited, 'PROVIDER', used=1000000))
        expired = evaluate(self.grant, store_slug='shenglang', now=self.end+timedelta(days=7))
        self.assertFalse(can_add_account(expired, 'BOSS', used=0))
        for kind, used in [('BOSS', True), ('STAFF', 1), ('BOSS', -1)]:
            with self.assertRaises(ValueError):
                can_add_account(unlimited, kind, used=used)

    def test_tier_features_are_configurable_without_blocking_core_or_earned_assets(self):
        basic = evaluate(self.grant, store_slug='shenglang', now=self.start)
        pro = evaluate(replace(self.grant, tier='PRO'), store_slug='shenglang', now=self.start)
        ultra = evaluate(replace(self.grant, tier='ULTRA'), store_slug='shenglang', now=self.start)
        expired = evaluate(self.grant, store_slug='shenglang', now=self.end+timedelta(days=7))
        self.assertEqual([feature_mode(x, 'PROMOTIONS') for x in (basic, pro, ultra, expired)],
                         ['OFF', 'FULL', 'FULL', 'OFF'])
        self.assertEqual(feature_mode(basic, 'VIP_GROWTH'), 'READ_ONLY')
        self.assertEqual(feature_mode(pro, 'PAYROLL'), 'BASIC')
        self.assertEqual(feature_mode(ultra, 'LOTTERY'), 'FULL')
        self.assertEqual(feature_mode(pro, 'PROMOTIONS', overrides={'PROMOTIONS': 'OFF'}), 'OFF')
        self.assertEqual(feature_mode(expired, 'PROMOTIONS', overrides={'PROMOTIONS': 'FULL'}), 'READ_ONLY')
        for decision in (basic, pro, ultra, expired, evaluate(None, store_slug='shenglang', now=self.start)):
            for feature in CORE_ACTIONS:
                self.assertEqual(feature_mode(decision, feature, overrides={feature: 'OFF'}), 'FULL')
        with self.assertRaises(ValueError):
            feature_mode(pro, 'PROMOTIONS', overrides={'ORDER': 'OFF'})
        with self.assertRaises(ValueError):
            feature_mode(pro, 'PROMOTIONS', overrides={'PROMOTIONS': 'RECENT'})
        with self.assertRaises(ValueError):
            feature_mode(pro, 'UNKNOWN')

    def test_scheduled_change_keeps_old_tier_until_effective_instant(self):
        switch = self.start + timedelta(days=30)
        future = replace(self.grant, tier='PRO', starts_at=switch)
        before = evaluate_scheduled(self.grant, future, store_slug='shenglang',
                                    now=switch-timedelta(microseconds=1))
        after = evaluate_scheduled(self.grant, future, store_slug='shenglang', now=switch)
        self.assertEqual((before.tier.code, after.tier.code), ('BASIC', 'PRO'))
        self.assertFalse(can_add_account(before, 'BOSS', used=500))
        self.assertTrue(can_add_account(after, 'BOSS', used=500))
        pending_first = evaluate_scheduled(None, future, store_slug='shenglang', now=self.start)
        self.assertEqual(pending_first.status, 'NOT_YET_ACTIVE')
        self.assertFalse(pending_first.can_open_store)
        wrong_store = evaluate_scheduled(self.grant, replace(future, store_slug='xinan'),
                                         store_slug='shenglang', now=switch)
        self.assertEqual(wrong_store.status, 'UNLICENSED')
