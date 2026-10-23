from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework.exceptions import ValidationError
from club_accounts.models import ClubAccount
from orders.models import ServiceCategory
from orders.tests.factories import make_user, make_wallet, make_service, make_console_user, make_order
from users.models import BossType
from wallet.models import RechargeRecord
from coupons.models import Coupon, UserCoupon
from coupons.benefits import issue, applies_to
from .tests import mapped
from .models import VipTier, ConsumptionConfirmation
from .services import update_vip
from .serializers import TierSerializer


class CouponBindingTests(APITestCase):
    def setUp(self):
        self.user = make_user()
        self.account = mapped(self.user)
        make_wallet(self.user, balance=100000)
        self.category = ServiceCategory.objects.create(name='陪玩单')
        self.single = make_service(name='单陪娱乐', price=5000, service_category=self.category, benefit_kind='COMPANION_HOUR', platform='PC', price_unit='小时')
        self.double = make_service(name='双陪娱乐', price=10000, service_category=self.category)
        self.client.force_authenticate(self.user)

    def test_explicit_binding_can_match_a_renamed_service_but_never_another_tier(self):
        issue(self.account, 'bound', name='陪玩赠券', rate=0, scope='COMPANION', variant='娱乐女陪',
            service_ids=[self.single.pk], limit_units=1)
        coupon = UserCoupon.objects.get()
        self.single.name = '新版娱乐服务'; self.single.save()
        self.assertTrue(applies_to(coupon.coupon, self.single))
        self.assertFalse(applies_to(coupon.coupon, self.double))
        allowed = self.client.get(f'/api/coupons/mine/?usable=1&service_id={self.single.pk}').data['data']
        denied = self.client.get(f'/api/coupons/mine/?usable=1&service_id={self.double.pk}').data['data']
        self.assertEqual([row['id'] for row in allowed], [coupon.pk])
        self.assertEqual(allowed[0]['service_ids'], [self.single.pk])
        self.assertEqual(denied, [])
        failed = self.client.post('/api/orders/create/', {'service_id': self.double.pk, 'user_coupon_id': coupon.pk}, format='json')
        self.assertEqual(failed.data['code'], 400)
        paid = self.client.post('/api/orders/create/', {'service_id': self.single.pk, 'user_coupon_id': coupon.pk, 'game_rounds': 2}, format='json')
        self.assertEqual(paid.data['code'], 0, paid.data)
        self.assertEqual(paid.data['data']['amount'], 5000)

    def test_vip_upgrade_saves_binding_and_config_changes_do_not_rewrite_issued_coupons(self):
        boss_type = BossType.objects.create(name='VIP1', discount_rate=98)
        tier = VipTier.objects.create(boss_type=boss_type, level=1, recharge_threshold=100,
            companion_service_ids=[self.single.pk])
        order = make_order(self.user, amount=100, customer_account=self.account, status='COMPLETED', payment_status='PAID')
        ConsumptionConfirmation.objects.create(order=order, account=self.account, business_cycle=1, amount=100)
        update_vip(self.account)
        self.assertEqual(UserCoupon.objects.filter(coupon__scope='COMPANION').count(), 2)
        self.assertTrue(all(row.service_ids == [self.single.pk] for row in Coupon.objects.filter(scope='COMPANION')))
        tier.companion_service_ids = [self.double.pk]; tier.save()
        update_vip(self.account)
        self.assertEqual(UserCoupon.objects.filter(coupon__scope='COMPANION').count(), 2)
        self.assertTrue(all(row.service_ids == [self.single.pk] for row in Coupon.objects.filter(scope='COMPANION')))

    def test_malformed_or_unorderable_bindings_rejected(self):
        gift_category = ServiceCategory.objects.create(name='礼物', is_gift=True)
        invalid = [make_service(is_active=False), make_service(is_addon=True),
            make_service(purchase_mode='CONSULT'), make_service(service_category=gift_category)]
        serializer = TierSerializer()
        for value in [[True], ['1'], [self.single.pk, self.single.pk], [999999], *[[row.pk] for row in invalid]]:
            with self.assertRaises(ValidationError): serializer.validate_companion_service_ids(value)
        self.assertEqual(serializer.validate_companion_service_ids([self.single.pk]), [self.single.pk])
        self.assertEqual(serializer.validate_companion_service_ids(None), [])

    def test_empty_snapshot_and_unbound_legacy_coupons_fail_closed(self):
        issue(self.account, 'empty', name='待配置', rate=0, service_ids=[])
        issue(self.account, 'legacy', name='原娱乐券', rate=0, scope='COMPANION', variant='娱乐')
        empty = Coupon.objects.get(grant_key='empty:0')
        legacy = Coupon.objects.get(grant_key='legacy:0')
        self.assertFalse(applies_to(empty, self.single))
        self.assertFalse(applies_to(legacy, self.single))

    def test_missing_legacy_mapping_does_not_report_successful_award(self):
        account = ClubAccount.objects.create(username='unlinked-award', account_type='BOSS')
        with self.assertRaises(ValidationError): issue(account, 'unmapped', name='赠券', rate=0)
        self.assertFalse(Coupon.objects.exists())

    def test_binding_options_need_operations_permission_and_exclude_gifts(self):
        operator = make_console_user(perms=['engagement:view'])
        self.client.force_authenticate(operator)
        response = self.client.get('/api/admin/vip-tiers/service-options/')
        self.assertEqual(response.data['code'], 0, response.data)
        self.assertIn(self.single.pk, [row['id'] for row in response.data['data']])
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get('/api/admin/vip-tiers/service-options/').status_code, 403)
