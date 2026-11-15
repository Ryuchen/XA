from django.utils import timezone
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase
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


@override_settings(LEGACY_FORCE_AUTH_COMPAT=True, CELERY_TASK_ALWAYS_EAGER=False)
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

    @override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
    def test_independent_account_award_is_owned_and_cannot_leak_to_same_numbered_legacy_user(self):
        from club_accounts.tokens import issue_account_tokens
        collision = max(self.user.pk, self.account.pk) + 100
        unrelated = make_user(pk=collision)
        unrelated_account = mapped(unrelated)
        account = ClubAccount.objects.create(pk=collision, username='unlinked-award', account_type='BOSS')
        rows = issue(account, 'unmapped', name='赠券', rate=0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].account_id, account.pk)
        self.assertIsNone(rows[0].user_id)
        self.assertEqual(issue(account, 'unmapped', name='赠券', rate=0)[0].pk, rows[0].pk)
        self.assertEqual(UserCoupon.objects.filter(account=account).count(), 1)
        self.assertFalse(UserCoupon.objects.filter(user=unrelated).exists())
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        owned = client.get('/api/coupons/mine/').data['data']
        self.assertEqual([row['id'] for row in owned], [rows[0].pk])
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(unrelated_account)[0])
        self.assertNotIn(rows[0].pk, {row['id'] for row in client.get('/api/coupons/mine/').data['data']})

    def test_binding_options_need_operations_permission_and_exclude_gifts(self):
        operator = make_console_user(perms=['engagement:view'])
        self.client.force_authenticate(operator)
        response = self.client.get('/api/admin/vip-tiers/service-options/')
        self.assertEqual(response.data['code'], 0, response.data)
        self.assertIn(self.single.pk, [row['id'] for row in response.data['data']])
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get('/api/admin/vip-tiers/service-options/').status_code, 403)

    def test_binding_options_match_each_business_category(self):
        safe = make_service(name='单陪安全箱', benefit_kind='SAFEBOX_33', platform='PC', billing_provider_count=1)
        double_safe = make_service(name='双陪安全箱', benefit_kind='SAFEBOX_33', platform='PC', billing_provider_count=2)
        non_hour = make_service(name='按局陪玩', benefit_kind='COMPANION_HOUR', platform='PC', price_unit='局')
        operator = make_console_user(perms=['engagement:view'])
        from club_accounts.services import get_or_create_account_for_legacy_user
        from club_accounts.tokens import issue_account_tokens
        account = get_or_create_account_for_legacy_user(operator)
        operator.admin_membership.account = account
        operator.admin_membership.save(update_fields=['account'])
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        def ids(kind):
            response = self.client.get('/api/admin/vip-tiers/service-options/', {'benefit_kind': kind})
            self.assertEqual(response.data['code'], 0, response.data)
            return {item['id'] for item in response.data['data']}
        self.assertEqual(ids('COMPANION_HOUR'), {self.single.pk})
        self.assertEqual(ids('SAFEBOX_33'), {safe.pk})
        self.assertNotIn(double_safe.pk, ids('SAFEBOX_33'))
        self.assertNotIn(non_hour.pk, ids('COMPANION_HOUR'))
        self.assertEqual(self.client.get('/api/admin/vip-tiers/service-options/', {'benefit_kind': 'UNKNOWN'}).status_code, 400)

    def test_existing_binding_beyond_first_page_remains_available(self):
        from orders.models import ServiceItem
        ServiceItem.objects.bulk_create([ServiceItem(name=f'安全箱 {i}', price=10000, benefit_kind='SAFEBOX_33',
            platform='PC', billing_provider_count=1) for i in range(501)])
        last = ServiceItem.objects.filter(benefit_kind='SAFEBOX_33').order_by('-pk').first()
        operator = make_console_user(perms=['engagement:view'])
        from club_accounts.services import get_or_create_account_for_legacy_user
        from club_accounts.tokens import issue_account_tokens
        account = get_or_create_account_for_legacy_user(operator)
        operator.admin_membership.account = account
        operator.admin_membership.save(update_fields=['account'])
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        response = self.client.get('/api/admin/vip-tiers/service-options/',
                                   {'benefit_kind': 'SAFEBOX_33', 'selected_ids': str(last.pk)})
        self.assertEqual(response.data['code'], 0, response.data)
        self.assertEqual(len(response.data['data']), 501)
        self.assertIn(last.pk, {row['id'] for row in response.data['data']})
        found = self.client.get('/api/admin/vip-tiers/service-options/',
                                {'benefit_kind': 'SAFEBOX_33', 'keyword': last.name})
        self.assertEqual([row['id'] for row in found.data['data']], [last.pk])
