"""A downgrade freezes only the issuing store's own VIP coupon terms."""
from django.test import TestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from coupons.benefits import issue
from coupons.models import UserCoupon
from engagement.models import ConsumptionConfirmation, VipTier
from engagement.refunds import reverse_refund_benefits
from orders.models import OrderRefund
from orders.tests.factories import make_boss_type, make_order, make_service, make_user
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.tests_hosts import xinan_store


class VipRefundRightsIsolationTest(TestCase):
    databases = {'default', 'store_b'}

    def test_same_account_and_coupon_ids_are_locked_only_in_refunding_store(self):
        stores = (resolve_store(), xinan_store())
        fixtures = []
        for store in stores:
            with store_context(store):
                boss = make_user()
                account = get_or_create_account_for_legacy_user(boss)
                tier = VipTier.objects.create(level=1, boss_type=make_boss_type(), recharge_threshold=1000)
                account.boss_type = tier.boss_type
                account.save(update_fields=['boss_type'])
                order = make_order(boss, service=make_service(price=2000), customer_account=account,
                    status='COMPLETED', payment_status='PAID', amount=2000)
                ConsumptionConfirmation.objects.create(order=order, account=account, business_cycle=1, amount=2000)
                coupon = issue(account, f'vip-safe:{account.pk}:1:0', name='隔离安全箱券', rate=70)[0]
                fixtures.append((account.pk, order.pk, coupon.pk))
        self.assertEqual(fixtures[0], fixtures[1])
        with store_context(stores[0]), transaction.atomic():
            account_id, order_id, _ = fixtures[0]
            from orders.models import Order
            order = Order.objects.get(pk=order_id, customer_account_id=account_id)
            refund = OrderRefund.objects.create(order=order, amount=2000, reason='隔离测试')
            self.assertTrue(reverse_refund_benefits(order, refund, None)['vip_rights_preserved'])
        for store, expected in zip(stores, (True, False)):
            with store_context(store):
                coupon = UserCoupon.objects.select_related('coupon').get(pk=fixtures[0][2])
                self.assertEqual(coupon.vip_terms_locked_by_downgrade, expected)
                self.assertEqual((coupon.status, coupon.coupon.discount_rate), ('UNUSED', 70))
