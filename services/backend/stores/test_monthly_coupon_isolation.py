from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from coupons.models import UserCoupon, VipMonthlyCouponGrant
from coupons.management.commands.audit_vip_monthly_evidence import audit_monthly
from coupons.monthly import issue_monthly_coupons
from engagement.models import VipTier
from orders.tests.factories import make_boss_type, make_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store


class MonthlyCouponIsolationTest(TestCase):
    databases = {'default', 'store_b'}

    def test_same_account_and_tier_ids_use_each_stores_own_monthly_rules(self):
        stores = (resolve_store(), xinan_store())
        fixtures = []
        now = datetime(2026, 11, 7, 12, tzinfo=ZoneInfo('Asia/Shanghai'))
        for store, count, rate in zip(stores, (1, 3), (90, 75)):
            with store_context(store):
                user = make_user()
                account = get_or_create_account_for_legacy_user(user)
                link_legacy_relations(user, account)
                tier = VipTier.objects.create(level=2, boss_type=make_boss_type(), recharge_threshold=100,
                    monthly_coupon_count=count, monthly_discount=rate)
                account.boss_type = tier.boss_type
                account.save(update_fields=['boss_type'])
                grant = issue_monthly_coupons(account, tier.pk, now=now, allow_new_period=True)
                fixtures.append((account.pk, tier.pk, grant.pk))
        self.assertEqual(fixtures[0], fixtures[1])
        for store, count, rate in zip(stores, (1, 3), (90, 75)):
            with store_context(store):
                grant = VipMonthlyCouponGrant.objects.get(pk=fixtures[0][2])
                rows = UserCoupon.objects.filter(account_id=fixtures[0][0], coupon__grant_key__startswith='vip-month:')
                self.assertEqual((grant.issued_count, rows.count()), (count, count))
                self.assertEqual(set(rows.values_list('coupon__discount_rate', flat=True)), {rate})
                result = audit_monthly(store.database_alias, month=now.date().replace(day=1))
                self.assertEqual((result['account_count'], result['records'][0]['recorded_coupon_count']), (1, count))

    @override_settings(VIP_MONTHLY_DECISIONS_ENABLED=True)
    def test_same_account_id_uses_its_store_cutover_and_historical_zero_policy(self):
        from club_accounts.models import ClubAccount
        from coupons.benefits import grant_periodic
        from coupons.models import VipMonthlyCutover
        now = datetime(2026, 11, 10, 12, tzinfo=ZoneInfo('Asia/Shanghai'))
        created = datetime(2026, 11, 8, 12, tzinfo=ZoneInfo('Asia/Shanghai'))
        fixtures = []
        for store, cutover_day in zip((resolve_store(), xinan_store()), (7, 9)):
            with store_context(store):
                VipMonthlyCutover.objects.create(activated_at=datetime(2026, 11, cutover_day, tzinfo=ZoneInfo('Asia/Shanghai')),
                                                 platform_actor_id='test', operation_id='same-operation')
                user = make_user(); account = get_or_create_account_for_legacy_user(user)
                ClubAccount.objects.filter(pk=account.pk).update(created_at=created)
                account.refresh_from_db()
                tier = VipTier.objects.create(level=2, boss_type=make_boss_type(), recharge_threshold=100,
                    monthly_coupon_count=2, monthly_discount=90)
                account.boss_type = tier.boss_type; account.save(update_fields=['boss_type'])
                with patch('django.utils.timezone.now', return_value=now):
                    grant_periodic(account)
                grant = VipMonthlyCouponGrant.objects.get(account=account)
                fixtures.append((account.pk, grant.source, grant.issued_count))
        self.assertEqual(fixtures[0][0], fixtures[1][0])
        self.assertEqual([row[1:] for row in fixtures], [('SYSTEM', 2), ('LEGACY', 0)])
