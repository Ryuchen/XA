import os
from datetime import timedelta
from unittest import skipUnless

from django.test import LiveServerTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from coupons.benefits import grant_periodic
from coupons.models import UserCoupon, VipMonthlyCutover
from engagement.models import ConsumptionConfirmation, MemberBenefitGrant, VipTier
from engagement.services import config
from orders.tests.factories import make_boss_type, make_console_user, make_order, make_user


@skipUnless(os.environ.get('XA_VIP_THRESHOLD_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
class VipThresholdBrowserTest(LiveServerTestCase):
    def test_admin_configures_coupon_terms(self):
        high = VipTier.objects.create(level=2, boss_type=make_boss_type(name='核对高级'),
            recharge_threshold=400, monthly_coupon_count=1, monthly_discount=90, season_discount=80)
        config()
        VipMonthlyCutover.objects.create(activated_at=timezone.now()-timedelta(minutes=1),
                                         platform_actor_id='test', operation_id='coupon-config-ui')
        permissions = ['engagement:view', 'engagement:edit', 'boss_type:view', 'service:view']
        user = make_console_user(permissions); staff = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = staff; user.admin_membership.save(update_fields=['account'])
        operator = APIClient()
        operator.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
        saved = operator.patch(f'/api/admin/vip-tiers/{high.pk}/',
            {'monthly_coupon_count': 3, 'monthly_discount': 88, 'season_discount': 65}, format='json')
        self.assertEqual((saved.status_code, saved.data['code']), (200, 0), saved.data)
        high.refresh_from_db()
        self.assertEqual((high.monthly_coupon_count, high.monthly_discount, high.season_discount), (3, 88, 65))

    def test_admin_preview_and_save_recalculates_member(self):
        user = make_user(); boss = get_or_create_account_for_legacy_user(user)
        low = VipTier.objects.create(level=1, boss_type=make_boss_type(name='核对初级'),
                                     recharge_threshold=100, monthly_coupon_count=1)
        high = VipTier.objects.create(level=2, boss_type=make_boss_type(name='核对高级'),
                                      recharge_threshold=900, monthly_coupon_count=2)
        boss.boss_type = low.boss_type; boss.save(update_fields=['boss_type'])
        order = make_order(user, customer_account=boss, amount=500, payment_status='PAID')
        ConsumptionConfirmation.objects.create(order=order, account=boss, business_cycle=boss.business_cycle, amount=500)
        config()
        VipMonthlyCutover.objects.create(activated_at=timezone.now()-timedelta(minutes=1),
                                         platform_actor_id='test', operation_id='threshold-ui')
        grant_periodic(boss)
        permissions = ['engagement:view', 'engagement:edit', 'boss_type:view', 'service:view']
        user = make_console_user(permissions); staff = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = staff; user.admin_membership.save(update_fields=['account'])
        operator = APIClient()
        operator.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
        preview = operator.post(f'/api/admin/vip-tiers/{high.pk}/recalculation-preview/',
            {'consumption_threshold': 500}, format='json')
        self.assertEqual((preview.status_code, preview.data['code']), (200, 0), preview.data)
        self.assertEqual((preview.data['data']['affected_count'], preview.data['data']['upgrade_count'],
                          preview.data['data']['downgrade_count']), (1, 1, 0))
        saved = operator.patch(f'/api/admin/vip-tiers/{high.pk}/',
            {'consumption_threshold': 500, 'recalculation_token': preview.data['data']['token']}, format='json')
        self.assertEqual((saved.status_code, saved.data['code']), (200, 0), saved.data)
        boss.refresh_from_db(); high.refresh_from_db()
        self.assertEqual((boss.boss_type_id, high.recharge_threshold), (high.boss_type_id, 500))
        self.assertEqual(MemberBenefitGrant.objects.filter(account=boss, tier=high).count(), 1)
        self.assertEqual(UserCoupon.objects.filter(account=boss, coupon__grant_key__startswith='vip-month:').count(), 1)

    def test_admin_reactivates_tier_and_recalculates_member(self):
        user = make_user(); boss = get_or_create_account_for_legacy_user(user)
        low = VipTier.objects.create(level=1, boss_type=make_boss_type(name='核对初级'),
                                     recharge_threshold=100, monthly_coupon_count=1)
        high = VipTier.objects.create(level=2, boss_type=make_boss_type(name='核对高级'),
                                      recharge_threshold=400, monthly_coupon_count=2, active=False)
        boss.boss_type = low.boss_type; boss.save(update_fields=['boss_type'])
        order = make_order(user, customer_account=boss, amount=500, payment_status='PAID')
        ConsumptionConfirmation.objects.create(order=order, account=boss, business_cycle=boss.business_cycle, amount=500)
        config()
        VipMonthlyCutover.objects.create(activated_at=timezone.now()-timedelta(minutes=1),
                                         platform_actor_id='test', operation_id='reactivate-ui')
        grant_periodic(boss)
        permissions = ['engagement:view', 'engagement:edit', 'boss_type:view', 'service:view']
        user = make_console_user(permissions); staff = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = staff; user.admin_membership.save(update_fields=['account'])
        operator = APIClient()
        operator.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
        preview = operator.post(f'/api/admin/vip-tiers/{high.pk}/recalculation-preview/',
            {'active': True}, format='json')
        self.assertEqual((preview.status_code, preview.data['code']), (200, 0), preview.data)
        self.assertEqual((preview.data['data']['affected_count'], preview.data['data']['upgrade_count'],
                          preview.data['data']['downgrade_count']), (1, 1, 0))
        saved = operator.patch(f'/api/admin/vip-tiers/{high.pk}/',
            {'active': True, 'recalculation_token': preview.data['data']['token']}, format='json')
        self.assertEqual((saved.status_code, saved.data['code']), (200, 0), saved.data)
        boss.refresh_from_db(); high.refresh_from_db()
        self.assertTrue(high.active)
        self.assertEqual(boss.boss_type_id, high.boss_type_id)
        self.assertEqual(MemberBenefitGrant.objects.filter(account=boss, tier=high).count(), 1)
        self.assertEqual(UserCoupon.objects.filter(account=boss, coupon__grant_key__startswith='vip-month:').count(), 1)

    def test_admin_creates_tier_and_recalculates_member(self):
        user = make_user(); boss = get_or_create_account_for_legacy_user(user)
        low = VipTier.objects.create(level=1, boss_type=make_boss_type(name='核对初级'),
                                     recharge_threshold=100, monthly_coupon_count=1)
        kind = make_boss_type(name='核对新等级')
        boss.boss_type = low.boss_type; boss.save(update_fields=['boss_type'])
        order = make_order(user, customer_account=boss, amount=500, payment_status='PAID')
        ConsumptionConfirmation.objects.create(order=order, account=boss, business_cycle=boss.business_cycle, amount=500)
        config()
        VipMonthlyCutover.objects.create(activated_at=timezone.now()-timedelta(minutes=1),
                                         platform_actor_id='test', operation_id='create-ui')
        grant_periodic(boss)
        permissions = ['engagement:view', 'engagement:edit', 'boss_type:view', 'service:view']
        user = make_console_user(permissions); staff = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = staff; user.admin_membership.save(update_fields=['account'])
        operator = APIClient()
        operator.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
        payload = {'boss_type': kind.pk, 'level': 3, 'consumption_threshold': 400, 'benefits': {},
                   'upgrade_rewards': [], 'companion_service_ids': [], 'safe_box_service_ids': [],
                   'monthly_discount': 90, 'monthly_coupon_count': 0, 'season_discount': 80, 'active': True}
        preview = operator.post('/api/admin/vip-tiers/creation-preview/', payload, format='json')
        self.assertEqual((preview.status_code, preview.data['code']), (200, 0), preview.data)
        self.assertEqual((preview.data['data']['affected_count'], preview.data['data']['upgrade_count'],
                          preview.data['data']['downgrade_count']), (1, 1, 0))
        saved = operator.post('/api/admin/vip-tiers/',
            {**payload, 'recalculation_token': preview.data['data']['token']}, format='json')
        self.assertEqual((saved.status_code, saved.data['code']), (201, 0), saved.data)
        tier = VipTier.objects.get(boss_type=kind)
        boss.refresh_from_db()
        self.assertEqual((tier.level, tier.recharge_threshold, boss.boss_type_id), (3, 400, kind.pk))
        self.assertEqual(MemberBenefitGrant.objects.filter(account=boss, tier=tier).count(), 1)
        self.assertEqual(UserCoupon.objects.filter(account=boss, coupon__grant_key__startswith='vip-month:').count(), 1)
