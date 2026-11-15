from django.test import SimpleTestCase, TestCase

from orders.discount_options import (DiscountOptionError, PercentageOption, VoucherOption,
                                     discount_options)


class DiscountOptionsTest(SimpleTestCase):
    def setUp(self):
        self.rates = [PercentageOption('none', '原价', 100, 'NONE'),
                      PercentageOption('vip', '会员九折', 90, 'VIP'),
                      PercentageOption('activity:1', '门店八五折', 85, 'PROMOTION', 1)]

    def options(self, original=10000, vouchers=(), **rules):
        return discount_options(original, self.rates, list(vouchers),
                                amount_threshold_basis=rules.get('amount', 'AFTER_RATE'),
                                rate_threshold_basis=rules.get('rate', 'ORIGINAL'))

    def test_recommends_lowest_valid_combination_not_lowest_single_rate(self):
        coupons = [VoucherOption(7, '减300', 'AMOUNT', amount=3000),
                   VoucherOption(8, '八折券', 'RATE', rate=80)]
        options = self.options(vouchers=coupons)
        winner = options[0]
        self.assertEqual((winner.amount, winner.promo_discount, winner.coupon_discount, winner.coupon_id),
                         (5500, 1500, 3000, 7))
        self.assertEqual(next(x.amount for x in options if x.key == 'coupon:8'), 8000)
        self.assertTrue(all(not (x.boss_discount and x.promo_discount) for x in options))
        self.assertTrue(all(not (x.coupon_id == 8 and (x.boss_discount or x.promo_discount)) for x in options))

    def test_rounds_customer_payable_up_to_whole_coin(self):
        options = self.options(original=10100)
        promotion = next(x for x in options if x.key == 'activity:1')
        self.assertEqual((promotion.amount, promotion.promo_discount), (8590, 1510))
        self.assertTrue(all(x.amount % 10 == 0 for x in options))

    def test_coupon_threshold_policy_is_explicit(self):
        coupon = VoucherOption(7, '满950减100', 'AMOUNT', threshold=9500, amount=1000)
        after = self.options(vouchers=[coupon], amount='AFTER_RATE')
        original = self.options(vouchers=[coupon], amount='ORIGINAL')
        self.assertFalse(any(x.key == 'vip|coupon:7' for x in after))
        self.assertTrue(any(x.key == 'vip|coupon:7' for x in original))

    def test_rate_coupon_cap_and_equal_prices_do_not_consume_coupon_by_default(self):
        coupon = VoucherOption(9, '有限额八折券', 'RATE', rate=80, max_discount=999)
        options = self.options(vouchers=[coupon])
        selected = next(x for x in options if x.key == 'coupon:9')
        self.assertEqual((selected.amount, selected.coupon_discount), (9010, 990))
        equal = discount_options(10000, [PercentageOption('none', '原价', 100, 'NONE'),
            PercentageOption('vip', '会员', 90, 'VIP')], [VoucherOption(3, '九折券', 'RATE', rate=90)],
            amount_threshold_basis='AFTER_RATE', rate_threshold_basis='ORIGINAL')
        self.assertEqual(equal[0].key, 'vip')

    def test_free_voucher_and_invalid_configuration(self):
        options = self.options(vouchers=[VoucherOption(4, '免费权益', 'RATE', rate=0)])
        self.assertEqual(options[0].amount, 0)
        with self.assertRaises(DiscountOptionError):
            self.options(original=10001)
        with self.assertRaises(DiscountOptionError):
            discount_options(1000, self.rates, [], amount_threshold_basis='UNKNOWN', rate_threshold_basis='ORIGINAL')


class StoreDiscountRecommendationTest(TestCase):
    def setUp(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from engagement.services import config
        from orders.tests.factories import make_boss_type, make_service, make_user
        self.user = make_user(); self.account = get_or_create_account_for_legacy_user(self.user)
        self.account.boss_type = make_boss_type(name='隔离报价会员', discount_rate=90)
        self.account.save(update_fields=['boss_type'])
        self.service = make_service(price=10000)
        config()

    def coupon(self, name, discount_type, amount, **kwargs):
        from coupons.models import Coupon, UserCoupon
        coupon = Coupon.objects.create(name=name, discount_type=discount_type, amount=amount, **kwargs)
        return UserCoupon.objects.create(account=self.account, coupon=coupon)

    def options(self, service=None):
        from orders.discount_recommendation import service_discount_options
        return service_discount_options(self.account, service or self.service, 1)

    def test_real_store_rules_and_owned_available_coupons(self):
        from orders.tests.factories import make_promotion
        amount_coupon = self.coupon('减300', 'DIRECT', 3000)
        rate_coupon = self.coupon('八折券', 'RATE', 1, discount_rate=80)
        used = self.coupon('已用券', 'DIRECT', 9000); used.status = 'USED'; used.save(update_fields=['status'])
        active = make_promotion(title='隔离活动', discount_rate=85)
        make_promotion(title='停用活动', discount_rate=50, is_active=False)
        options = self.options()
        winner = options[0]
        self.assertEqual((winner.amount, winner.promotion_id, winner.coupon_id), (5500, active.pk, amount_coupon.pk))
        self.assertTrue(any(x.coupon_id == rate_coupon.pk and x.amount == 8000 for x in options))
        self.assertFalse(any(x.coupon_id == used.pk for x in options))

    def test_disabled_vip_no_longer_supplies_percentage_option(self):
        from engagement.services import config
        operation = config(); operation.vip_enabled = False; operation.save(update_fields=['vip_enabled'])
        self.assertFalse(any(x.percentage_key == 'vip' for x in self.options()))

    def test_confirmed_coupon_thresholds_use_selected_discount_and_original_respectively(self):
        amount = self.coupon('满950减100', 'THRESHOLD', 1000, threshold=9500)
        rate = self.coupon('满950八折', 'RATE', 1, threshold=9500, discount_rate=80)
        options = self.options()
        self.assertFalse(any(x.percentage_key == 'vip' and x.coupon_id == amount.pk for x in options))
        self.assertTrue(any(x.percentage_key == 'none' and x.coupon_id == amount.pk for x in options))
        self.assertTrue(any(x.coupon_id == rate.pk and x.amount == 8000 for x in options))

    def test_excludes_another_boss_expired_coupon_and_wrong_service_promotion(self):
        from datetime import timedelta
        from django.utils import timezone
        from club_accounts.services import get_or_create_account_for_legacy_user
        from coupons.models import Coupon, UserCoupon
        from orders.tests.factories import make_promotion, make_service, make_user
        expired = self.coupon('过期券', 'DIRECT', 9000, valid_to=timezone.now() - timedelta(seconds=1))
        other = get_or_create_account_for_legacy_user(make_user())
        coupon = Coupon.objects.create(name='其他老板的券', discount_type='DIRECT', amount=9000)
        foreign = UserCoupon.objects.create(account=other, coupon=coupon)
        other_service = make_service(price=10000)
        promo = make_promotion(title='其他商品活动', discount_rate=10, scope='ITEMS', items=[other_service])
        options = self.options()
        self.assertFalse(any(x.coupon_id in (expired.pk, foreign.pk) for x in options))
        self.assertFalse(any(x.promotion_id == promo.pk for x in options))
        self.assertEqual(options[0].amount, 9000)

    def test_experience_gift_addons_cannot_enter_ordinary_calculation(self):
        from orders.tests.factories import make_service, make_service_category
        from rest_framework.exceptions import ValidationError
        for service in (make_service(experience_kind='DAILY'),
                        make_service(service_category=make_service_category(is_gift=True)),
                        make_service(is_addon=True)):
            with self.subTest(pk=service.pk), self.assertRaises(ValidationError):
                self.options(service)
