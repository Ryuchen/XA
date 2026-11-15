from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from coupons.benefits import grant_periodic
from coupons.models import Coupon, UserCoupon, VipMonthlyCouponGrant, VipMonthlyCutover
from engagement.models import ClubOperationConfig, ConsumptionConfirmation, VipTier
from engagement.refunds import reverse_refund_benefits
from engagement.services import config, update_vip
from orders.models import OrderRefund
from orders.tests import test_gift_basket as gift_fixture
from orders.tests.factories import make_boss_type, make_order, make_service, make_user
from stores.db import transaction


@override_settings(VIP_TERMS_FREEZE_ENABLED=True, VIP_MONTHLY_DECISIONS_ENABLED=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class VipGradeChangeTermsTest(APITestCase):
    def setUp(self):
        self.user = make_user()
        self.account = get_or_create_account_for_legacy_user(self.user)
        self.old_safe = make_service(name='旧安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC')
        self.new_safe = make_service(name='新安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC')
        self.low = VipTier.objects.create(level=2, boss_type=make_boss_type(), recharge_threshold=100,
            monthly_coupon_count=1, monthly_discount=80, season_discount=70, safe_box_service_ids=[self.old_safe.pk])
        self.high = VipTier.objects.create(level=3, boss_type=make_boss_type(), recharge_threshold=900,
            monthly_coupon_count=2, monthly_discount=60, season_discount=60, safe_box_service_ids=[self.old_safe.pk])
        config()
        VipMonthlyCutover.objects.create(activated_at=timezone.now() - timedelta(minutes=1),
            platform_actor_id='test', operation_id='grade-terms')

    def issue_at(self, tier):
        self.account.boss_type = tier.boss_type
        self.account.save(update_fields=['boss_type'])
        grant_periodic(self.account)
        self.monthly = UserCoupon.objects.filter(account=self.account, coupon__grant_key__startswith='vip-month:').order_by('pk').first()
        self.safe = UserCoupon.objects.get(account=self.account, coupon__grant_key__startswith='vip-safe:')

    def confirmed_order(self, amount):
        order = make_order(self.user, customer_account=self.account, amount=amount, status='COMPLETED',
                           payment_status='PAID')
        ConsumptionConfirmation.objects.create(order=order, account=self.account,
            business_cycle=self.account.business_cycle, amount=amount)
        return order

    def test_upgrade_preserves_monthly_rate_current_before_upgrade(self):
        self.issue_at(self.low)
        self.low.monthly_discount = 90; self.low.save(update_fields=['monthly_discount'])
        self.confirmed_order(1000)
        with patch('engagement.tasks.enqueue_broadcast'):
            update_vip(self.account)
        self.account.refresh_from_db(); self.monthly.refresh_from_db(); self.monthly.coupon.refresh_from_db()
        self.assertEqual(self.account.boss_type_id, self.high.boss_type_id)

        self.assertEqual((self.monthly.coupon.discount_rate, self.monthly.vip_terms_locked_by_downgrade), (90, True))
        self.assertEqual(UserCoupon.objects.filter(account=self.account, coupon__grant_key__startswith='vip-month:').count(), 1)
        self.assertTrue(VipMonthlyCouponGrant.objects.get(account=self.account).terms_frozen)

    def test_refund_downgrade_materializes_current_monthly_and_safe_terms(self):
        self.issue_at(self.high)
        self.high.monthly_discount = 75; self.high.season_discount = 50
        self.high.safe_box_service_ids = [self.new_safe.pk]
        self.high.save(update_fields=['monthly_discount', 'season_discount', 'safe_box_service_ids'])
        order = self.confirmed_order(1000)
        with transaction.atomic():
            ClubOperationConfig.objects.select_for_update().get(pk=1)
            refund = OrderRefund.objects.create(order=order, amount=500, reason='隔离部分退款')
            order.payment_status = 'PARTIALLY_REFUNDED'; order.save(update_fields=['payment_status'])
            effects = reverse_refund_benefits(order, refund, None)
        self.account.refresh_from_db(); self.monthly.refresh_from_db(); self.safe.refresh_from_db()
        self.monthly.coupon.refresh_from_db(); self.safe.coupon.refresh_from_db()
        self.assertEqual(self.account.boss_type_id, self.low.boss_type_id)
        self.assertEqual((self.monthly.coupon.discount_rate, self.safe.coupon.discount_rate,
                          self.safe.coupon.service_ids), (75, 50, [self.new_safe.pk]))
        self.assertTrue(self.monthly.vip_terms_locked_by_downgrade)
        self.assertTrue(self.safe.vip_terms_locked_by_downgrade)
        self.assertTrue(effects['vip_rights_preserved'])

    def test_another_accounts_unverifiable_coupon_does_not_block_upgrade(self):
        self.issue_at(self.low)
        other = get_or_create_account_for_legacy_user(make_user())
        coupon = Coupon.objects.create(name='其他老板的无效券', grant_key='vip-month:999:1:2026-11:0:0',
                                      discount_type='RATE', discount_rate=80, amount=1)
        UserCoupon.objects.create(account=other, coupon=coupon, business_cycle=other.business_cycle)
        self.confirmed_order(1000)
        with patch('engagement.tasks.enqueue_broadcast'):
            update_vip(self.account)
        self.account.refresh_from_db()
        self.assertEqual(self.account.boss_type_id, self.high.boss_type_id)

    def test_downgrade_does_not_reprice_previously_used_coupon(self):
        self.issue_at(self.high)
        self.monthly.status = 'USED'
        self.monthly.vip_terms_locked_by_downgrade = True
        self.monthly.save(update_fields=['status', 'vip_terms_locked_by_downgrade'])
        self.high.monthly_discount = 75; self.high.save(update_fields=['monthly_discount'])
        order = self.confirmed_order(1000)
        with transaction.atomic():
            ClubOperationConfig.objects.select_for_update().get(pk=1)
            refund = OrderRefund.objects.create(order=order, amount=500, reason='隔离部分退款')
            order.payment_status = 'PARTIALLY_REFUNDED'; order.save(update_fields=['payment_status'])
            reverse_refund_benefits(order, refund, None)
        self.monthly.refresh_from_db(); self.monthly.coupon.refresh_from_db()
        self.assertEqual((self.monthly.status, self.monthly.coupon.discount_rate), ('USED', 60))

    def test_unverifiable_shared_coupon_rolls_back_downgrade(self):
        self.issue_at(self.high)
        other = get_or_create_account_for_legacy_user(make_user())
        UserCoupon.objects.create(account=other, coupon=self.monthly.coupon, business_cycle=other.business_cycle)
        order = self.confirmed_order(1000)
        with self.assertRaises(ValidationError):
            with transaction.atomic():
                ClubOperationConfig.objects.select_for_update().get(pk=1)
                refund = OrderRefund.objects.create(order=order, amount=500, reason='隔离部分退款')
                order.payment_status = 'PARTIALLY_REFUNDED'; order.save(update_fields=['payment_status'])
                reverse_refund_benefits(order, refund, None)
        self.account.refresh_from_db(); order.refresh_from_db()
        self.assertEqual(self.account.boss_type_id, self.high.boss_type_id)
        self.assertEqual(order.payment_status, 'PAID')
        self.assertFalse(OrderRefund.objects.filter(order=order).exists())
        self.assertEqual(ConsumptionConfirmation.objects.get(order=order).refunded_amount, 0)


@override_settings(VIP_TERMS_FREEZE_ENABLED=True, VIP_MONTHLY_DECISIONS_ENABLED=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class VipGiftRefundTermsTest(APITestCase):
    setUp = gift_fixture.GiftBasketTests.setUp

    def test_gift_payment_and_refund_keep_pre_downgrade_terms(self):
        from orders.models import Order
        from orders.tests.factories import make_console_user
        low = VipTier.objects.create(level=2, boss_type=make_boss_type(), recharge_threshold=100,
                                     monthly_coupon_count=1, monthly_discount=90)
        original = make_service(name='原安全箱权益', price=10000, benefit_kind='SAFEBOX_33', platform='PC')
        revised = make_service(name='更新安全箱权益', price=10000, benefit_kind='SAFEBOX_33', platform='PC')
        high = VipTier.objects.create(level=3, boss_type=make_boss_type(), recharge_threshold=1500,
            monthly_coupon_count=2, monthly_discount=80, season_discount=60, safe_box_service_ids=[original.pk])
        config()
        VipMonthlyCutover.objects.create(activated_at=timezone.now() - timedelta(minutes=1),
                                         platform_actor_id='test', operation_id='gift-grade-terms')
        sent = self.client.post('/api/orders/gifts/send/', {'gift_service_id': self.one.pk,
            'provider_id': self.recipient.pk, 'quantity': 1, 'naming_title': '测试冠名',
            'expected_amount': 2100, 'client_request_id': 'gift-grade-terms'}, format='json')
        self.assertEqual(sent.data['code'], 0, sent.data)
        order = Order.objects.get(pk=sent.data['data']['id'])
        self.account.refresh_from_db(); self.assertEqual(self.account.boss_type_id, high.boss_type_id)
        monthly = UserCoupon.objects.filter(account=self.account, coupon__grant_key__startswith='vip-month:').first()
        safe = UserCoupon.objects.get(account=self.account, coupon__scope='SAFE_BOX')
        high.monthly_discount = 75; high.season_discount = 50; high.safe_box_service_ids = [revised.pk]
        high.save(update_fields=['monthly_discount', 'season_discount', 'safe_box_service_ids'])
        staff = make_console_user(['order:view', 'order:refund'])
        self.client.force_authenticate(staff)
        preview = self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/', {'refund_amount': 1700})
        self.assertEqual(preview.data['code'], 0, preview.data)
        with patch('orders.services.log_money_event') as money_log, self.captureOnCommitCallbacks(execute=True):
            refund = self.client.post(f'/api/admin/orders/{order.pk}/refund/', {'reason': '隔离会员降级退款',
                'refund_amount': 1700, 'plan_token': preview.data['data']['plan_token'],
                'client_request_id': 'gift-grade-terms-refund'}, format='json')
        self.assertEqual(refund.data['code'], 0, refund.data)
        money_log.assert_called_once()
        self.assertEqual(money_log.call_args.args[0], 'order.refund')
        self.account.refresh_from_db(); monthly.refresh_from_db(); safe.refresh_from_db()
        monthly.coupon.refresh_from_db(); safe.coupon.refresh_from_db()
        self.assertEqual(self.account.boss_type_id, low.boss_type_id)
        self.assertEqual((monthly.coupon.discount_rate, safe.coupon.discount_rate, safe.coupon.service_ids),
                         (75, 50, [revised.pk]))
        self.assertTrue(monthly.vip_terms_locked_by_downgrade)
        self.assertTrue(safe.vip_terms_locked_by_downgrade)

    def test_gift_refund_rolls_back_if_vip_coupon_ownership_is_ambiguous(self):
        from orders.models import Order
        from orders.tests.factories import make_console_user
        high = VipTier.objects.create(level=3, boss_type=make_boss_type(), recharge_threshold=1500,
                                      monthly_coupon_count=1, monthly_discount=80)
        config()
        VipMonthlyCutover.objects.create(activated_at=timezone.now() - timedelta(minutes=1),
                                         platform_actor_id='test', operation_id='gift-rollback')
        sent = self.client.post('/api/orders/gifts/send/', {'gift_service_id': self.one.pk,
            'provider_id': self.recipient.pk, 'quantity': 1, 'naming_title': '测试冠名',
            'expected_amount': 2100, 'client_request_id': 'gift-rollback'}, format='json')
        self.assertEqual(sent.data['code'], 0, sent.data)
        order = Order.objects.get(pk=sent.data['data']['id'])
        monthly = UserCoupon.objects.get(account=self.account, coupon__grant_key__startswith='vip-month:')
        other = get_or_create_account_for_legacy_user(make_user())
        UserCoupon.objects.create(account=other, coupon=monthly.coupon, business_cycle=other.business_cycle)
        self.wallet.refresh_from_db(); before = self.wallet.balance
        self.client.force_authenticate(make_console_user(['order:view', 'order:refund']))
        preview = self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/', {'refund_amount': 1700})
        self.assertEqual(preview.data['code'], 0, preview.data)
        with patch('orders.services.log_money_event') as money_log, self.captureOnCommitCallbacks(execute=True):
            refund = self.client.post(f'/api/admin/orders/{order.pk}/refund/', {'reason': '隔离核对异常券',
                'refund_amount': 1700, 'plan_token': preview.data['data']['plan_token'],
                'client_request_id': 'gift-rollback-refund'}, format='json')
        self.assertNotEqual(refund.data['code'], 0, refund.data)
        money_log.assert_not_called()
        self.wallet.refresh_from_db(); self.account.refresh_from_db(); order.refresh_from_db()
        self.assertEqual(self.wallet.balance, before)
        self.assertEqual(self.account.boss_type_id, high.boss_type_id)
        self.assertEqual(order.payment_status, 'PAID')
        self.assertFalse(OrderRefund.objects.filter(order=order).exists())
        self.assertEqual(ConsumptionConfirmation.objects.get(order=order).refunded_amount, 0)

