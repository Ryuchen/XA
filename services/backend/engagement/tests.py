from datetime import date
from unittest.mock import patch
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from orders.tests.factories import make_user, make_order, make_wallet, make_console_user, make_provider, make_service
from orders.models import ServiceCategory, Order
from wallet.models import RechargeRecord
from users.models import BossType
from .models import (Broadcast, BroadcastRule, LotteryCampaign, LotteryPrize, LotteryDraw,
    MemberProfile, PointFundingLot, PointLedger, RewardItem, RewardClaim, VipTier, RechargePackage)
from .services import (allocate_order_points, award_order_points, reverse_order_points,
    release_order_allocations, redeem, draw, process_claim, broadcast, record_recharge)
from .tasks import publish_birthdays


def mapped(user):
    account = get_or_create_account_for_legacy_user(user)
    link_legacy_relations(user, account)
    return account


class EngagementTests(APITestCase):
    def setUp(self):
        self.user = make_user()
        self.account = mapped(self.user)
        self.wallet = make_wallet(self.user, balance=100000)
        link_legacy_relations(self.user, self.account)
        self.profile = MemberProfile.objects.create(account=self.account, points_balance=100000)
        self.client.force_authenticate(self.user)

    def campaign(self, kind='POINTS'):
        campaign = LotteryCampaign.objects.create(name='测试转盘', point_cost=100, enabled=True)
        first = RewardItem.objects.create(code='rare', name='珍稀奖', kind=kind, point_value=5, is_active=True, in_shop=False, stock=1)
        second = RewardItem.objects.create(code='common', name='10 积分', kind='POINTS', point_value=10, is_active=True, in_shop=False)
        one = LotteryPrize.objects.create(campaign=campaign, reward=first, probability=1)
        two = LotteryPrize.objects.create(campaign=campaign, reward=second, probability=9999)
        return campaign, one, two

    def test_fifo_funding_snapshot_and_refund_restore_exact_lots(self):
        one = PointFundingLot.objects.create(account=self.account, source_key='first', remaining=10000, points_rate=100)
        two = PointFundingLot.objects.create(account=self.account, source_key='second', remaining=10000, points_rate=150)
        order = make_order(self.user, amount=15000, customer_account=self.account)
        with transaction.atomic():
            allocate_order_points(order, self.wallet, 20000)
            award_order_points(order)
            award_order_points(order)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.points_balance, 117500)
        self.assertEqual(PointLedger.objects.count(), 1)
        # 生日后改、后续充值倍率改变，均不重新计算已支付的订单。
        self.profile.birthday = timezone.localdate().replace(year=2000)
        self.profile.save()
        with transaction.atomic():
            reverse_order_points(order)
            reverse_order_points(order)
            release_order_allocations(order)
            release_order_allocations(order)
        self.profile.refresh_from_db(); one.refresh_from_db(); two.refresh_from_db()
        self.assertEqual(self.profile.points_balance, 100000)
        self.assertEqual((one.remaining, two.remaining), (10000, 10000))

    def test_birthday_and_prepaid_multipliers_are_frozen_at_payment(self):
        today = timezone.localdate()
        self.profile.birthday = date(2000, today.month, today.day); self.profile.save()
        PointFundingLot.objects.create(account=self.account, source_key='prepaid', remaining=10000, points_rate=110)
        order = make_order(self.user, amount=10000, customer_account=self.account)
        with transaction.atomic():
            allocate_order_points(order, self.wallet, 10000)
            award_order_points(order)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.points_balance, 122000)

    def test_redeem_retry_returns_same_claim_and_stock_is_not_negative(self):
        reward = RewardItem.objects.create(code='headset', name='耳机', kind='PHYSICAL', point_cost=600, stock=1, is_active=True)
        first = redeem(self.account, reward.pk, 'request-0001')
        second = redeem(self.account, reward.pk, 'request-0001')
        self.assertEqual(first.pk, second.pk)
        with self.assertRaises(ValidationError):
            redeem(self.account, reward.pk, 'request-0002')
        self.profile.refresh_from_db(); reward.refresh_from_db()
        self.assertEqual(self.profile.points_balance, 40000)
        self.assertEqual(reward.stock, 0)
        self.assertEqual(RewardClaim.objects.count(), 1)
        reward.in_shop = False; reward.is_active = False; reward.save()
        self.assertEqual(redeem(self.account, reward.pk, 'request-0001').pk, first.pk)

    def test_cancel_redemption_returns_points_once(self):
        reward = RewardItem.objects.create(code='item', name='物品', kind='VIRTUAL', point_cost=10, stock=1, is_active=True)
        claim = redeem(self.account, reward.pk, 'request-0001')
        process_claim(claim.pk, 'CANCELLED', '库存实物损坏', self.account)
        process_claim(claim.pk, 'CANCELLED', '重复操作', self.account)
        self.profile.refresh_from_db(); reward.refresh_from_db()
        self.assertEqual(self.profile.points_balance, 100000)
        self.assertEqual(reward.stock, 1)

    def test_one_in_ten_thousand_and_replay(self):
        campaign, one, _ = self.campaign()
        with patch('engagement.services.secrets.randbelow', return_value=0):
            result = draw(self.account, campaign.pk, 'draw-00001', 1)
        again = draw(self.account, campaign.pk, 'draw-00001', 1)
        self.assertEqual(result.pk, again.pk)
        self.assertEqual(result.prize_id, one.pk)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.points_balance, 90500)
        self.assertEqual(LotteryDraw.objects.count(), 1)
        self.assertEqual(Broadcast.objects.get().kind, 'LOTTERY')

    def test_lottery_boundary_one_goes_to_second_prize(self):
        campaign, _, two = self.campaign()
        with patch('engagement.services.secrets.randbelow', return_value=1):
            result = draw(self.account, campaign.pk, 'draw-00001', 1)
        self.assertEqual(result.prize_id, two.pk)

    def test_lottery_fulfillment_and_broadcast_commit_together(self):
        campaign, one, _ = self.campaign('NAMING')
        with patch('engagement.services.secrets.randbelow', return_value=0):
            result = draw(self.account, campaign.pk, 'draw-00001', 1)
        self.assertEqual(result.claim.status, 'PENDING')
        self.assertEqual(Broadcast.objects.get().kind, 'LOTTERY_NAMING')
        process_claim(result.claim_id, 'COMPLETED', '已为指定打手完成冠名', self.account)
        self.assertEqual(set(Broadcast.objects.values_list('kind', flat=True)), {'LOTTERY_NAMING', 'NAMING'})

    def test_invalid_pool_or_version_does_not_deduct(self):
        campaign, one, _ = self.campaign()
        one.probability=2; one.save()
        with self.assertRaises(ValidationError): draw(self.account, campaign.pk, 'draw-00001', 1)
        with self.assertRaises(ValidationError): draw(self.account, campaign.pk, 'draw-00001', 2)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.points_balance, 100000)
        self.assertFalse(LotteryDraw.objects.exists())
        self.assertFalse(PointLedger.objects.exists())

    def test_failed_reward_write_rolls_back_points_stock_and_feed(self):
        campaign, one, _ = self.campaign('SERVICE')
        with patch('engagement.services.secrets.randbelow', return_value=0), patch('engagement.services.broadcast', side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError): draw(self.account, campaign.pk, 'draw-00001', 1)
        self.profile.refresh_from_db(); one.reward.refresh_from_db()
        self.assertEqual(self.profile.points_balance, 100000)
        self.assertEqual(one.reward.stock, 1)
        self.assertFalse(LotteryDraw.objects.exists())
        self.assertFalse(RewardClaim.objects.exists())

    def test_topups_do_not_upgrade_vip_or_award_consumption_points(self):
        boss_type = BossType.objects.create(name='VIP1', discount_rate=98)
        VipTier.objects.create(boss_type=boss_type, level=1, recharge_threshold=300000)
        first = RechargeRecord.objects.create(user=self.user, account=self.account, amount=100000, gift_amount=900000)
        second = RechargeRecord.objects.create(user=self.user, account=self.account, amount=200000)
        with transaction.atomic(): record_recharge(first); record_recharge(second); record_recharge(second)
        self.account.refresh_from_db()
        self.assertIsNone(self.account.boss_type_id)
        self.assertFalse(PointLedger.objects.exists())
        self.assertEqual(Broadcast.objects.filter(kind='UPGRADE').count(), 0)

    def test_birthday_and_anonymous_reviews_do_not_leak_identity(self):
        self.profile.birthday = timezone.localdate().replace(year=2000); self.profile.save()
        publish_birthdays(); publish_birthdays()
        self.assertEqual(Broadcast.objects.filter(kind='BIRTHDAY').count(), 1)
        row = broadcast('REVIEW', 'review:1', account=self.account, anonymous=True, provider='陪玩')
        self.assertNotIn(self.account.nickname, row.content)
        self.assertIn('一位老板', row.content)

    def test_read_only_operator_cannot_change_configuration(self):
        operator = make_console_user(perms=['engagement:view'])
        self.client.force_authenticate(operator)
        response = self.client.put('/api/admin/club-config/', {'points_enabled': False})
        self.assertEqual(response.status_code, 403)

    def test_customer_cannot_read_admin_shipping_or_other_claim(self):
        reward = RewardItem.objects.create(code='item', name='礼物', kind='PHYSICAL', point_cost=10, is_active=True)
        claim = redeem(self.account, reward.pk, 'request-0001', {'phone': '13800138000'})
        other = make_user(); mapped(other)
        self.client.force_authenticate(other)
        self.assertEqual(self.client.patch(f'/api/club/claims/{claim.pk}/', {'details': {}}, format='json').status_code, 404)
        self.assertEqual(self.client.get('/api/admin/club-claims/').status_code, 403)
        self.assertEqual(self.client.get('/api/club/claims/').data['data'], [])

    def test_vip_and_rate_coupon_choose_best_without_consuming_weaker_coupon(self):
        from coupons.benefits import issue
        from coupons.models import UserCoupon
        boss_type = BossType.objects.create(name='VIP6', discount_rate=90)
        self.account.boss_type = boss_type; self.account.save()
        self.user.boss_type = boss_type; self.user.save()
        service = make_service(price=10000)
        issue(self.account, 'weak-coupon', name='95 折券', rate=95)
        weak = UserCoupon.objects.get()
        response = self.client.post('/api/orders/create/', {'service_id': service.pk, 'user_coupon_id': weak.pk}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        self.assertEqual(response.data['data']['amount'], 9000)
        weak.refresh_from_db(); self.assertEqual(weak.status, 'UNUSED')
        issue(self.account, 'better-coupon', name='85 折券', rate=85)
        better = UserCoupon.objects.exclude(pk=weak.pk).get()
        response = self.client.post('/api/orders/create/', {'service_id': service.pk, 'user_coupon_id': better.pk}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        self.assertEqual(response.data['data']['amount'], 8500)
        self.assertEqual(response.data['data']['boss_discount'], 0)
        better.refresh_from_db(); self.assertEqual(better.status, 'USED')

    def test_private_free_service_coupon_scope_and_quantity(self):
        from coupons.benefits import issue
        from coupons.models import UserCoupon
        category = ServiceCategory.objects.create(name='陪玩单')
        companion = make_service(name='技术小时陪', price=5000, service_category=category, benefit_kind='COMPANION_HOUR', platform='PC', price_unit='小时')
        other = make_service(name='普通服务', price=5000)
        issue(self.account, 'free-service', name='技术小时陪赠券', rate=0, scope='COMPANION', variant='技术', limit_units=1, service_ids=[companion.pk])
        coupon = UserCoupon.objects.get()
        self.assertEqual(self.client.get('/api/coupons/').data['data'], [])
        response = self.client.post('/api/orders/create/', {'service_id': other.pk, 'user_coupon_id': coupon.pk}, format='json')
        self.assertEqual(response.data['code'], 400)
        response = self.client.post('/api/orders/create/', {'service_id': companion.pk, 'user_coupon_id': coupon.pk, 'game_rounds': 3}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        self.assertEqual(response.data['data']['amount'], 10000)

    def test_experience_lottery_claim_cannot_discount_poster_experience_orders(self):
        from coupons.models import UserCoupon
        campaign = LotteryCampaign.objects.create(name='体验', point_cost=100, enabled=True)
        reward = RewardItem.objects.create(code='lottery-experience-68', name='68 体验单', kind='SERVICE', in_shop=False, is_active=True)
        LotteryPrize.objects.create(campaign=campaign, reward=reward, probability=10000)
        from .models import MemberProfile
        before=MemberProfile.objects.get(account=self.account).points_balance
        result = draw(self.account,campaign.pk,'experience-draw-001',1)
        self.assertEqual(result.claim.status, 'PENDING')
        self.assertEqual(result.claim.reward_id, reward.pk)
        self.assertEqual(result.claim.source, 'LOTTERY')
        self.assertEqual(MemberProfile.objects.get(account=self.account).points_balance,before-10000)
        self.assertEqual(UserCoupon.objects.count(),0)
        self.assertEqual(LotteryDraw.objects.count(),1)
        # Winning is a dedicated fulfillment entitlement, never a coupon that
        # could change the price of a paid poster experience service.
        service = make_service(name='海报68体验单', price=6800, service_type='EXPERIENCE',
                               experience_kind='DAILY', platform='PC', allows_discount_coupons=False)
        response = self.client.post('/api/orders/create/', {'service_id': service.pk}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        self.assertEqual(response.data['data']['amount'], 6800)

    def test_disabled_points_at_payment_are_not_awarded_if_later_enabled(self):
        from .services import config
        conf = config(); conf.points_enabled = False; conf.save()
        order = make_order(self.user, amount=10000, customer_account=self.account)
        with transaction.atomic(): allocate_order_points(order, self.wallet, 10000)
        conf.points_enabled=True; conf.save()
        with transaction.atomic(): award_order_points(order)
        self.assertFalse(PointLedger.objects.exists())

    def test_refund_points_debt_blocks_redemption_but_accepts_future_earnings(self):
        from .services import change_points, locked_profile
        with transaction.atomic():
            profile=locked_profile(self.account)
            change_points(profile, -110000, 'refund-large', 'REFUND', '退款', allow_debt=True)
            with self.assertRaises(ValidationError): change_points(profile, -100, 'more-debit', 'REDEMPTION', '兑换')
            change_points(profile, 5000, 'new-earned', 'CONSUMPTION', '新消费')
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.points_balance, -5000)

    def test_monthly_coupon_grants_are_idempotent_and_top_up_difference_only(self):
        from coupons.benefits import grant_periodic
        from coupons.models import UserCoupon
        types = [BossType.objects.create(name=f'VIP{x}', discount_rate=90) for x in [2,3]]
        VipTier.objects.create(boss_type=types[0], level=2, recharge_threshold=600000, monthly_discount=90, monthly_coupon_count=1)
        VipTier.objects.create(boss_type=types[1], level=3, recharge_threshold=1000000, monthly_discount=90, monthly_coupon_count=2)
        self.account.boss_type = types[0]; self.account.save()
        grant_periodic(self.account); grant_periodic(self.account)
        self.assertEqual(UserCoupon.objects.filter(coupon__grant_key__startswith='vip-month:').count(), 1)
        self.account.boss_type = types[1]; self.account.save()
        grant_periodic(self.account); grant_periodic(self.account)
        self.assertEqual(UserCoupon.objects.filter(coupon__grant_key__startswith='vip-month:').count(), 2)


class GiftCommissionTests(APITestCase):
    def setUp(self):
        self.user = make_user(); self.account = mapped(self.user)
        make_wallet(self.user, balance=100000); link_legacy_relations(self.user, self.account)
        self.provider = make_provider(); self.provider_account = mapped(self.provider)
        category = ServiceCategory.objects.create(name='礼物', is_gift=True)
        self.gift = make_service(service_category=category, price=1000, commission_rate=None)
        self.client.force_authenticate(self.user)
    def send(self, **kwargs):
        return self.client.post('/api/orders/gifts/send/', {'gift_service_id': self.gift.pk,
            'provider_id': self.provider_account.pk, **kwargs}, format='json')
    def test_threshold_uses_transaction_quantity_total(self):
        response = self.send(quantity=1, client_request_id='gift-request-1')
        self.assertEqual(response.data['code'], 0)
        single = Order.objects.get(pk=response.data['data']['id'])
        self.assertEqual(single.provider_income, 1000)
        response = self.send(quantity=2, client_request_id='gift-request-2')
        self.assertEqual(response.data['code'], 0)
        double = Order.objects.get(pk=response.data['data']['id'])
        self.assertEqual(double.amount, 2000)
        self.assertEqual(double.provider_income, 1600)
    def test_price_changed_and_invalid_quantity_leave_money_unchanged(self):
        for payload in [{'quantity': True}, {'quantity': 1.5}, {'quantity': -1}, {'quantity': 1, 'expected_amount': 1}]:
            self.assertNotEqual(self.send(**payload).data['code'], 0)
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, 100000)
        self.assertFalse(Order.objects.exists())
