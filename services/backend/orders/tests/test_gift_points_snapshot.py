"""Gift points use payment-time membership, not historic recharge lots."""
from datetime import date, timedelta

from django.utils import timezone
from rest_framework.test import APITestCase
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from engagement.models import MemberProfile, PointFundingLot, PointLedger, VipTier
from engagement.services import config
from orders.models import Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_service_category, make_user, make_wallet
from users.models import BossType


class GiftPointsSnapshotTest(APITestCase):
    def setUp(self):
        self.boss = make_user()
        self.owner = get_or_create_account_for_legacy_user(self.boss)
        link_legacy_relations(self.boss, self.owner)
        provider = make_provider(is_verified=True)
        self.recipient = get_or_create_account_for_legacy_user(provider)
        link_legacy_relations(provider, self.recipient)
        self.wallet = make_wallet(self.boss, balance=2100)
        link_legacy_relations(self.boss, self.owner)
        self.gift = make_service(name='会员积分隔离礼物', price=2100,
                                 service_category=make_service_category(is_gift=True))
        self.profile = MemberProfile.objects.create(account=self.owner)
        self.client.force_authenticate(self.boss)

    def send(self, key):
        result = self.client.post('/api/orders/gifts/send/', {'gift_service_id': self.gift.pk,
            'provider_id': self.recipient.pk, 'quantity': 1, 'expected_amount': 2100,
            'client_request_id': key}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        return Order.objects.get(pk=result.data['data']['id'])

    def test_member_birthday_snapshot_ignores_lot_rates_and_refund_uses_original_terms(self):
        tier_type = BossType.objects.create(name='积分倍率会员', discount_rate=100)
        tier = VipTier.objects.create(boss_type=tier_type, level=1, recharge_threshold=1, points_rate=150)
        self.owner.boss_type = tier_type; self.owner.save(update_fields=['boss_type'])
        self.boss.boss_type = tier_type; self.boss.save(update_fields=['boss_type'])
        today = timezone.localdate()
        self.profile.birthday = date(2000, today.month, today.day); self.profile.save(update_fields=['birthday'])
        conf = config(); conf.birthday_multiplier = 2; conf.save(update_fields=['birthday_multiplier'])
        PointFundingLot.objects.create(account=self.owner, source_key='gift-points-first', kind='PREPAID', remaining=1050, points_rate=100)
        PointFundingLot.objects.create(account=self.owner, source_key='gift-points-second', kind='PREPAID', remaining=1050, points_rate=500)
        order = self.send('gift-points-membership')
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.points_balance, 6300)
        self.assertEqual((order.gift_points_snapshot['vip_tier_id'], order.gift_points_snapshot['points_rate'],
                          order.gift_points_snapshot['birthday_multiplier']), (tier.pk, 150, 2))
        tier.points_rate = 250; tier.save(update_fields=['points_rate'])
        conf.birthday_multiplier = 4; conf.save(update_fields=['birthday_multiplier'])
        self.profile.birthday = (today + timedelta(days=1)).replace(year=2000); self.profile.save(update_fields=['birthday'])
        staff = make_console_user(['order:view', 'order:refund']); self.client.force_authenticate(staff)
        preview = self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/', {'refund_amount': 1000})
        self.assertEqual(preview.data['code'], 0, preview.data)
        refund = self.client.post(f'/api/admin/orders/{order.pk}/refund/', {'reason': '礼物积分快照核对',
            'refund_amount': 1000, 'plan_token': preview.data['data']['plan_token'],
            'client_request_id': 'gift-points-partial-refund'}, format='json')
        self.assertEqual(refund.data['code'], 0, refund.data)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.points_balance, 3300)
        self.assertEqual(PointLedger.objects.get(order=order, kind='REFUND').amount, -3000)

    def test_ordinary_member_uses_base_rate_with_single_rounding_across_lots(self):
        conf = config(); conf.base_points_rate = 101; conf.save(update_fields=['base_points_rate'])
        PointFundingLot.objects.create(account=self.owner, source_key='round-first', kind='PREPAID', remaining=1050, points_rate=100)
        PointFundingLot.objects.create(account=self.owner, source_key='round-second', kind='PREPAID', remaining=1050, points_rate=500)
        order = self.send('gift-points-ordinary')
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.points_balance, 2121)
        self.assertEqual(order.gift_points_snapshot['points_rate'], 101)
        self.assertIsNone(order.gift_points_snapshot['vip_tier_id'])
        self.assertEqual(sum(order.point_allocations.values_list('points_amount', flat=True)), 2121)

    def test_partial_refund_reverses_rounding_difference_to_match_retained_principal(self):
        conf = config(); conf.base_points_rate = 101; conf.save(update_fields=['base_points_rate'])
        self.gift.price = 250; self.gift.save(update_fields=['price'])
        result = self.client.post('/api/orders/gifts/send/', {'gift_service_id': self.gift.pk,
            'provider_id': self.recipient.pk, 'quantity': 1, 'expected_amount': 250,
            'client_request_id': 'gift-points-rounding'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get(pk=result.data['data']['id'])
        self.assertEqual(PointLedger.objects.get(order=order, kind='CONSUMPTION').amount, 252)
        staff = make_console_user(['order:view', 'order:refund']); self.client.force_authenticate(staff)
        preview = self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/', {'refund_amount': 60})
        self.assertEqual(preview.data['code'], 0, preview.data)
        result = self.client.post(f'/api/admin/orders/{order.pk}/refund/', {'reason': '积分取整核对',
            'refund_amount': 60, 'plan_token': preview.data['data']['plan_token'],
            'client_request_id': 'gift-points-rounding-refund'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        self.assertEqual(PointLedger.objects.get(order=order, kind='REFUND').amount, -61)

    def test_inactive_tier_uses_base_rate(self):
        tier_type = BossType.objects.create(name='停用积分等级', discount_rate=100)
        VipTier.objects.create(boss_type=tier_type, level=1, recharge_threshold=1, points_rate=300, active=False)
        self.owner.boss_type = tier_type; self.owner.save(update_fields=['boss_type'])
        self.boss.boss_type = tier_type; self.boss.save(update_fields=['boss_type'])
        conf = config(); conf.base_points_rate = 125; conf.save(update_fields=['base_points_rate'])
        order = self.send('gift-points-inactive')
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.points_balance, 2625)
        self.assertIsNone(order.gift_points_snapshot['vip_tier_id'])

    def test_staff_configures_rates_without_resetting_existing_vip_benefits(self):
        tier_type = BossType.objects.create(name='原有权益会员', discount_rate=100)
        tier = VipTier.objects.create(boss_type=tier_type, level=1, recharge_threshold=1,
                                     monthly_coupon_count=7, monthly_discount=75)
        staff = make_console_user(['engagement:view', 'engagement:edit'])
        self.client.force_authenticate(staff)
        base = self.client.put('/api/admin/club-config/', {'base_points_rate': 135}, format='json')
        self.assertEqual(base.data['code'], 0, base.data)
        changed = self.client.patch(f'/api/admin/vip-tiers/{tier.pk}/', {'points_rate': 165}, format='json')
        self.assertEqual(changed.data['code'], 0, changed.data)
        tier.refresh_from_db()
        self.assertEqual((tier.points_rate, tier.monthly_coupon_count, tier.monthly_discount), (165, 7, 75))
        self.assertEqual(config().base_points_rate, 135)
        self.assertEqual(self.client.put('/api/admin/club-config/', {'base_points_rate': 1001}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(f'/api/admin/vip-tiers/{tier.pk}/', {'points_rate': -1}, format='json').status_code, 400)
