"""coupons 模块单元测试。

覆盖：
- Coupon.is_claimable / UserCoupon.is_usable 属性各分支
- GET  /api/coupons/                可领券列表（is_claimed / claimable 标记）
- POST /api/coupons/<id>/claim/      领取（成功+claimed_qty自增 / 404 / 抢光 / 过期 / 重复）
- GET  /api/coupons/mine/            我的券（usable 筛选 + amount 门槛筛选）
- POST /api/orders/create/           下单用券抵扣集成（门槛 / discount 计算 / 标记 USED）
"""

from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APITestCase

from coupons.models import Coupon, UserCoupon
from orders.models import Order
from orders.tests.factories import (
    make_coupon,
    make_service,
    make_user,
    make_user_coupon,
    make_wallet,
)


class CouponPropertyTest(APITestCase):
    """Coupon.is_claimable 各分支。"""

    def test_claimable_true_for_active_unlimited(self):
        coupon = make_coupon(total_qty=0)
        self.assertTrue(coupon.is_claimable)

    def test_not_claimable_when_inactive(self):
        coupon = make_coupon(is_active=False)
        self.assertFalse(coupon.is_claimable)

    def test_not_claimable_when_expired(self):
        coupon = make_coupon(valid_to=timezone.now() - timedelta(days=1))
        self.assertFalse(coupon.is_claimable)

    def test_not_claimable_when_sold_out(self):
        coupon = make_coupon(total_qty=2, claimed_qty=2)
        self.assertFalse(coupon.is_claimable)

    def test_claimable_when_qty_remaining(self):
        coupon = make_coupon(total_qty=2, claimed_qty=1)
        self.assertTrue(coupon.is_claimable)


class UserCouponPropertyTest(APITestCase):
    """UserCoupon.is_usable 各分支。"""

    def setUp(self):
        self.user = make_user()

    def test_usable_when_unused_and_valid(self):
        uc = make_user_coupon(self.user)
        self.assertTrue(uc.is_usable)

    def test_not_usable_when_used(self):
        uc = make_user_coupon(self.user, status=UserCoupon.Status.USED)
        self.assertFalse(uc.is_usable)

    def test_not_usable_when_coupon_expired(self):
        coupon = make_coupon(valid_to=timezone.now() - timedelta(days=1))
        uc = make_user_coupon(self.user, coupon=coupon)
        self.assertFalse(uc.is_usable)


class CouponListTest(APITestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_authenticate(self.user)

    def test_list_only_active_and_valid(self):
        active = make_coupon()
        make_coupon(is_active=False)
        make_coupon(valid_to=timezone.now() - timedelta(days=1))
        res = self.client.get('/api/coupons/')
        self.assertEqual(res.data['code'], 0)
        ids = [c['id'] for c in res.data['data']]
        self.assertEqual(ids, [active.id])

    def test_list_marks_claimed(self):
        coupon = make_coupon()
        make_user_coupon(self.user, coupon=coupon)
        res = self.client.get('/api/coupons/')
        item = res.data['data'][0]
        self.assertTrue(item['is_claimed'])
        self.assertTrue(item['claimable'])


class CouponClaimTest(APITestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_authenticate(self.user)

    def test_claim_success_increments_qty(self):
        coupon = make_coupon(total_qty=5, claimed_qty=0)
        res = self.client.post(f'/api/coupons/{coupon.id}/claim/')
        self.assertEqual(res.data['code'], 0)
        self.assertTrue(
            UserCoupon.objects.filter(user=self.user, coupon=coupon).exists()
        )
        coupon.refresh_from_db()
        self.assertEqual(coupon.claimed_qty, 1)

    def test_claim_nonexistent_returns_404(self):
        res = self.client.post('/api/coupons/999999/claim/')
        self.assertEqual(res.data['code'], 404)

    def test_claim_sold_out_returns_400(self):
        coupon = make_coupon(total_qty=1, claimed_qty=1)
        res = self.client.post(f'/api/coupons/{coupon.id}/claim/')
        self.assertEqual(res.data['code'], 400)
        self.assertFalse(
            UserCoupon.objects.filter(user=self.user, coupon=coupon).exists()
        )

    def test_claim_expired_returns_400(self):
        coupon = make_coupon(valid_to=timezone.now() - timedelta(days=1))
        res = self.client.post(f'/api/coupons/{coupon.id}/claim/')
        self.assertEqual(res.data['code'], 400)

    def test_claim_duplicate_returns_400(self):
        coupon = make_coupon()
        make_user_coupon(self.user, coupon=coupon)
        res = self.client.post(f'/api/coupons/{coupon.id}/claim/')
        self.assertEqual(res.data['code'], 400)
        self.assertEqual(
            UserCoupon.objects.filter(user=self.user, coupon=coupon).count(), 1
        )


class MyCouponListTest(APITestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_authenticate(self.user)

    def test_list_all_mine(self):
        make_user_coupon(self.user)
        make_user_coupon(self.user, status=UserCoupon.Status.USED)
        res = self.client.get('/api/coupons/mine/')
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(len(res.data['data']), 2)

    def test_usable_filter_excludes_used_and_expired(self):
        make_user_coupon(self.user)  # 可用
        make_user_coupon(self.user, status=UserCoupon.Status.USED)
        expired_coupon = make_coupon(valid_to=timezone.now() - timedelta(days=1))
        make_user_coupon(self.user, coupon=expired_coupon)
        res = self.client.get('/api/coupons/mine/?usable=1')
        self.assertEqual(len(res.data['data']), 1)

    def test_usable_amount_threshold_filter(self):
        low = make_coupon(threshold=5000)
        high = make_coupon(threshold=20000)
        make_user_coupon(self.user, coupon=low)
        make_user_coupon(self.user, coupon=high)
        res = self.client.get('/api/coupons/mine/?usable=1&amount=10000')
        ids = [c['id'] for c in res.data['data']]
        self.assertIn(low.id, ids)
        self.assertNotIn(high.id, ids)


class OrderCouponIntegrationTest(APITestCase):
    """下单用券抵扣集成。"""

    def setUp(self):
        self.user = make_user()
        self.service = make_service(price=10000)
        make_wallet(self.user, balance=100000)
        self.client.force_authenticate(self.user)

    def test_order_with_coupon_deducts_discount(self):
        coupon = make_coupon(threshold=10000, amount=2000)
        uc = make_user_coupon(self.user, coupon=coupon)
        res = self.client.post('/api/orders/create/', {
            'service_id': self.service.id,
            'user_coupon_id': uc.id,
        })
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data']['amount'], 8000)
        uc.refresh_from_db()
        self.assertEqual(uc.status, UserCoupon.Status.USED)
        self.assertIsNotNone(uc.order_id)
        self.assertIsNotNone(uc.used_at)

    def test_discount_capped_at_order_amount(self):
        service = make_service(price=1500)
        coupon = make_coupon(threshold=0, amount=5000)
        uc = make_user_coupon(self.user, coupon=coupon)
        res = self.client.post('/api/orders/create/', {
            'service_id': service.id,
            'user_coupon_id': uc.id,
        })
        self.assertEqual(res.data['code'], 0)
        # discount = min(5000, 1500) = 1500，应付 0
        self.assertEqual(res.data['data']['amount'], 0)

    def test_order_below_threshold_rejected(self):
        coupon = make_coupon(threshold=20000, amount=2000)
        uc = make_user_coupon(self.user, coupon=coupon)
        res = self.client.post('/api/orders/create/', {
            'service_id': self.service.id,
            'user_coupon_id': uc.id,
        })
        self.assertEqual(res.data['code'], 400)
        uc.refresh_from_db()
        self.assertEqual(uc.status, UserCoupon.Status.UNUSED)

    def test_used_coupon_rejected(self):
        uc = make_user_coupon(self.user, status=UserCoupon.Status.USED)
        res = self.client.post('/api/orders/create/', {
            'service_id': self.service.id,
            'user_coupon_id': uc.id,
        })
        self.assertEqual(res.data['code'], 400)

    def test_expired_coupon_rejected(self):
        coupon = make_coupon(valid_to=timezone.now() - timedelta(days=1))
        uc = make_user_coupon(self.user, coupon=coupon)
        res = self.client.post('/api/orders/create/', {
            'service_id': self.service.id,
            'user_coupon_id': uc.id,
        })
        self.assertEqual(res.data['code'], 400)

    def test_order_without_coupon_pays_full(self):
        res = self.client.post('/api/orders/create/', {
            'service_id': self.service.id,
        })
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data']['amount'], 10000)
        order = Order.objects.get(id=res.data['data']['id'])
        self.assertEqual(order.amount, 10000)
