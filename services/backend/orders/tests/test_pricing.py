"""促销活动 / 陪玩等级计价与抽成单元测试。

覆盖：
- compute_order_amount 折扣链（老板VIP → 活动折扣 / 优惠券，互斥）
- resolve_commission_rate 抽成优先级（活动 > 陪玩等级 > 店铺）
- resolve_active_promotion 时间窗 + 范围 + 优先级
- C 端下单接口落库计价明细与拆账
"""

from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APITestCase

from orders.models import Order
from orders.pricing import (
    PricingError,
    compute_order_amount,
    resolve_active_promotion,
    resolve_commission_rate,
)
from orders.tests.factories import (
    make_boss_type,
    make_escort_level,
    make_escort_profile,
    make_promotion,
    make_provider,
    make_service,
    make_service_category,
    make_user,
    make_user_coupon,
    make_wallet,
)
from promotions.models import Promotion
from support.models import SupportContactCard
from wallet.models import COMMISSION_RATE_KEY, SystemConfig


class ComputeOrderAmountTest(APITestCase):
    """折扣链纯函数测试。"""

    def test_no_discount(self):
        r = compute_order_amount(original_amount=10000, boss_rate=100)
        self.assertEqual(r.amount, 10000)
        self.assertEqual(r.boss_discount, 0)
        self.assertEqual(r.promo_discount, 0)
        self.assertEqual(r.coupon_discount, 0)

    def test_boss_discount_only(self):
        r = compute_order_amount(original_amount=10000, boss_rate=90)
        self.assertEqual(r.amount, 9000)
        self.assertEqual(r.boss_discount, 1000)

    def test_promotion_stacks_after_boss(self):
        # 原价 10000 → 老板九折 9000 → 活动八折 7200
        promo = make_promotion(discount_rate=80)
        r = compute_order_amount(original_amount=10000, boss_rate=90, promotion=promo)
        self.assertEqual(r.amount, 7200)
        self.assertEqual(r.boss_discount, 1000)
        self.assertEqual(r.promo_discount, 1800)

    def test_coupon_only(self):
        # 老板九折 9000 → 满减 2000 → 7000
        r = compute_order_amount(
            original_amount=10000, boss_rate=90,
            coupon_amount=2000, coupon_threshold=5000,
        )
        self.assertEqual(r.amount, 7000)
        self.assertEqual(r.coupon_discount, 2000)
        self.assertEqual(r.promo_discount, 0)

    def test_coupon_capped_at_amount(self):
        # 券面额超过应付，封顶不出现负数
        r = compute_order_amount(
            original_amount=3000, boss_rate=100,
            coupon_amount=5000, coupon_threshold=0,
        )
        self.assertEqual(r.amount, 0)
        self.assertEqual(r.coupon_discount, 3000)

    def test_coupon_below_threshold_raises(self):
        with self.assertRaises(PricingError):
            compute_order_amount(
                original_amount=4000, boss_rate=100,
                coupon_amount=2000, coupon_threshold=5000,
            )

    def test_promotion_and_coupon_mutually_exclusive(self):
        promo = make_promotion(discount_rate=80)
        with self.assertRaises(PricingError):
            compute_order_amount(
                original_amount=10000, boss_rate=100, promotion=promo,
                coupon_amount=2000, coupon_threshold=0,
            )

    def test_commission_only_promotion_allows_coupon(self):
        # 纯抽成活动（discount_rate=None）不影响实付，可与券共存
        promo = make_promotion(commission_rate=10)  # discount_rate=None
        r = compute_order_amount(
            original_amount=10000, boss_rate=100, promotion=promo,
            coupon_amount=2000, coupon_threshold=0,
        )
        self.assertEqual(r.amount, 8000)
        self.assertEqual(r.coupon_discount, 2000)
        self.assertEqual(r.promo_discount, 0)


class ResolveCommissionRateTest(APITestCase):
    """抽成优先级：活动 > 陪玩等级 > 店铺(商品级→全局)。"""

    def test_shop_global_fallback(self):
        SystemConfig.objects.update_or_create(
            key=COMMISSION_RATE_KEY, defaults={'value': '25'}
        )
        service = make_service(price=10000)  # commission_rate=None
        self.assertEqual(resolve_commission_rate(service, None, None), 25)

    def test_service_level_over_global(self):
        service = make_service(price=10000, commission_rate=30)
        self.assertEqual(resolve_commission_rate(service, None, None), 30)

    def test_escort_level_over_shop(self):
        service = make_service(price=10000, commission_rate=30)
        level = make_escort_level(commission_rate=15)
        provider = make_provider()
        profile = provider.escort_profile
        profile.level = level
        profile.save(update_fields=['level'])
        self.assertEqual(resolve_commission_rate(service, profile, None), 15)

    def test_inactive_escort_level_ignored(self):
        service = make_service(price=10000, commission_rate=30)
        level = make_escort_level(commission_rate=15, is_active=False)
        provider = make_provider()
        profile = provider.escort_profile
        profile.level = level
        profile.save(update_fields=['level'])
        self.assertEqual(resolve_commission_rate(service, profile, None), 30)

    def test_promotion_over_escort_level(self):
        service = make_service(price=10000, commission_rate=30)
        level = make_escort_level(commission_rate=15)
        provider = make_provider()
        profile = provider.escort_profile
        profile.level = level
        profile.save(update_fields=['level'])
        promo = make_promotion(commission_rate=5)
        self.assertEqual(resolve_commission_rate(service, profile, promo), 5)

    def test_promotion_without_commission_falls_through(self):
        # 活动只配折扣率、不配抽成 → 抽成回落等级/店铺
        service = make_service(price=10000, commission_rate=30)
        promo = make_promotion(discount_rate=80)  # commission_rate=None
        self.assertEqual(resolve_commission_rate(service, None, promo), 30)


class ResolveActivePromotionTest(APITestCase):
    def test_all_scope_matches(self):
        service = make_service()
        promo = make_promotion(scope=Promotion.Scope.ALL, discount_rate=90)
        self.assertEqual(resolve_active_promotion(service), promo)

    def test_category_scope(self):
        gift_cat = make_service_category(is_gift=True)
        normal_cat = make_service_category(is_gift=False)
        gift = make_service(service_category=gift_cat)
        normal = make_service(service_category=normal_cat)
        promo = make_promotion(
            scope=Promotion.Scope.CATEGORY, category=gift_cat,
            discount_rate=80,
        )
        self.assertEqual(resolve_active_promotion(gift), promo)
        self.assertIsNone(resolve_active_promotion(normal))

    def test_items_scope(self):
        target = make_service()
        other = make_service()
        promo = make_promotion(
            scope=Promotion.Scope.ITEMS, discount_rate=80, items=[target]
        )
        self.assertEqual(resolve_active_promotion(target), promo)
        self.assertIsNone(resolve_active_promotion(other))

    def test_inactive_excluded(self):
        service = make_service()
        make_promotion(discount_rate=90, is_active=False)
        self.assertIsNone(resolve_active_promotion(service))

    def test_out_of_window_excluded(self):
        service = make_service()
        now = timezone.now()
        make_promotion(
            discount_rate=90,
            start_at=now + timedelta(days=1), end_at=now + timedelta(days=2),
        )
        self.assertIsNone(resolve_active_promotion(service))

    def test_highest_priority_wins(self):
        service = make_service()
        make_promotion(discount_rate=90, priority=1)
        high = make_promotion(discount_rate=70, priority=10)
        self.assertEqual(resolve_active_promotion(service), high)


class CreateOrderPricingTest(APITestCase):
    """C 端下单接口的计价明细落库与拆账。"""

    def setUp(self):
        self.customer = make_user()
        make_wallet(self.customer, balance=1000000)
        self.client.force_authenticate(self.customer)

    def _create(self, service, **data):
        payload = {'service_id': service.id}
        payload.update(data)
        return self.client.post('/api/orders/create/', payload)

    def test_promotion_discount_persisted(self):
        make_promotion(discount_rate=80, priority=5)
        service = make_service(price=10000, commission_rate=20)
        res = self._create(service)
        self.assertEqual(res.data['code'], 0)
        order = Order.objects.get(id=res.data['data']['id'])
        self.assertEqual(order.original_amount, 10000)
        self.assertEqual(order.promo_discount, 2000)
        self.assertEqual(order.amount, 8000)
        self.assertIsNotNone(order.promotion_id)
        # 抽成仍按商品级 20%
        self.assertEqual(order.commission_rate, 20)
        self.assertEqual(order.provider_income, 6400)

    def test_selected_support_contact_is_snapshotted_on_order(self):
        service = make_service(price=10000)
        contact = SupportContactCard.objects.create(name='小安客服', is_active=True)
        res = self._create(service, support_contact_id=contact.id)
        self.assertEqual(res.data['code'], 0)
        order = Order.objects.get(id=res.data['data']['id'])
        self.assertEqual(order.support_contact_id, contact.id)
        self.assertEqual(order.support_contact_name_snapshot, '小安客服')

    def test_inactive_support_contact_is_rejected(self):
        service = make_service(price=10000)
        contact = SupportContactCard.objects.create(name='离岗客服', is_active=False)
        res = self._create(service, support_contact_id=contact.id)
        self.assertEqual(res.data['code'], 400)
        self.assertIn('客服', res.data['msg'])

    def test_promotion_overrides_commission(self):
        make_promotion(commission_rate=10, priority=5)
        service = make_service(price=10000, commission_rate=30)
        res = self._create(service)
        order = Order.objects.get(id=res.data['data']['id'])
        self.assertEqual(order.amount, 10000)  # 无折扣率
        self.assertEqual(order.commission_rate, 10)
        self.assertEqual(order.provider_income, 9000)

    def test_escort_level_commission_on_designated_provider(self):
        level = make_escort_level(commission_rate=10)
        provider = make_provider()
        profile = provider.escort_profile
        profile.level = level
        profile.save(update_fields=['level'])
        service = make_service(price=10000, commission_rate=30)
        res = self._create(service, provider_id=provider.id)
        self.assertEqual(res.data['code'], 0)
        order = Order.objects.get(id=res.data['data']['id'])
        self.assertEqual(order.commission_rate, 10)  # 等级覆盖商品级 30
        self.assertEqual(order.provider_income, 9000)

    def test_promotion_and_coupon_conflict_rejected(self):
        make_promotion(discount_rate=80, priority=5)
        service = make_service(price=10000)
        uc = make_user_coupon(self.customer)
        res = self._create(service, user_coupon_id=uc.id)
        self.assertEqual(res.data['code'], 400)
        self.assertIn('活动', res.data['msg'])

    def test_boss_and_promotion_persisted_detail(self):
        boss = make_boss_type(discount_rate=90)
        self.customer.boss_type = boss
        self.customer.save(update_fields=['boss_type'])
        make_promotion(discount_rate=80, priority=5)
        service = make_service(price=10000, commission_rate=20)
        res = self._create(service)
        order = Order.objects.get(id=res.data['data']['id'])
        # 原价10000 → 老板九折 9000(boss_discount 1000) → 活动八折 7200(promo_discount 1800)
        self.assertEqual(order.boss_discount, 1000)
        self.assertEqual(order.promo_discount, 1800)
        self.assertEqual(order.coupon_discount, 0)
        self.assertEqual(order.amount, 7200)
