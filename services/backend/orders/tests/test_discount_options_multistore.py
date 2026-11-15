from unittest import skipUnless
from django.conf import settings
from django.test import TestCase, override_settings

from club_accounts.services import get_or_create_account_for_legacy_user
from coupons.models import Coupon, UserCoupon
from engagement.services import config
from orders.discount_recommendation import service_discount_options
from orders.tests.factories import make_service, make_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store


@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class DiscountStoreIsolationTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_same_numeric_coupon_and_account_ids_keep_store_rules_separate(self):
        first, second = resolve_store(), xinan_store()
        for store, discount in ((first, 1000), (second, 2000)):
            with store_context(store):
                config()
                user = make_user(); account = get_or_create_account_for_legacy_user(user)
                service = make_service(price=10000)
                coupon = Coupon.objects.create(name='本店券', discount_type='DIRECT', amount=discount)
                owned = UserCoupon.objects.create(account=account, coupon=coupon)
                options = service_discount_options(account, service, 1)
                self.assertEqual(options[0].coupon_id, owned.pk)
                self.assertEqual(options[0].amount, 10000 - discount)

    @override_settings(VIP_MONTHLY_DECISIONS_ENABLED=True)
    def test_effective_safe_box_rule_is_read_from_each_store(self):
        from datetime import timedelta
        from django.utils import timezone
        from coupons.benefits import grant_periodic
        from coupons.models import VipMonthlyCutover
        from engagement.models import VipTier
        from orders.tests.factories import make_boss_type

        results = []
        for store, new_rate in zip((resolve_store(), xinan_store()), (50, 70)):
            with store_context(store):
                user = make_user(); account = get_or_create_account_for_legacy_user(user)
                config()
                service = make_service(price=10000, benefit_kind='SAFEBOX_33', platform='PC')
                tier = VipTier.objects.create(level=6, boss_type=make_boss_type(discount_rate=100),
                    recharge_threshold=1, monthly_coupon_count=0, monthly_discount=90,
                    season_discount=0, safe_box_service_ids=[service.pk])
                account.boss_type = tier.boss_type; account.save(update_fields=['boss_type'])
                VipMonthlyCutover.objects.create(activated_at=timezone.now()-timedelta(minutes=1),
                    platform_actor_id='test', operation_id='same-cutover')
                grant_periodic(account)
                voucher = UserCoupon.objects.get(account=account, coupon__scope='SAFE_BOX')
                tier.season_discount = new_rate; tier.save(update_fields=['season_discount'])
                option = next(row for row in service_discount_options(account, service, 1) if row.coupon_id == voucher.pk)
                voucher.coupon.refresh_from_db()
                results.append((account.pk, service.pk, voucher.pk, option.amount, voucher.coupon.discount_rate))
        self.assertEqual(results[0][:3], results[1][:3])
        self.assertEqual([row[3:] for row in results], [(5000, 0), (7000, 0)])
