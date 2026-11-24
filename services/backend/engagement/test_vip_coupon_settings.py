from datetime import timedelta
import os
from unittest import skipUnless
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from coupons.benefits import grant_periodic, grant_safety_box
from coupons.models import UserCoupon, VipMonthlyCouponGrant, VipMonthlyCutover
from coupons.monthly import issue_monthly_coupons
from coupons.pricing_terms import effective_coupon
from engagement.models import VipTier, VipUpgradeReview
from engagement.services import config
from orders.tests.factories import make_boss_type, make_console_user, make_user


@override_settings(VIP_MONTHLY_DECISIONS_ENABLED=True, VIP_TERMS_FREEZE_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class VipCouponSettingsTest(APITestCase):
    def setUp(self):
        self.boss = get_or_create_account_for_legacy_user(make_user())
        self.tier = VipTier.objects.create(level=2, boss_type=make_boss_type(), recharge_threshold=100,
            monthly_coupon_count=2, monthly_discount=80, season_discount=80)
        self.boss.boss_type = self.tier.boss_type; self.boss.save(update_fields=['boss_type'])
        config()
        VipMonthlyCutover.objects.create(activated_at=timezone.now()-timedelta(days=1), platform_actor_id='test', operation_id='config')
        staff_user = make_console_user(['engagement:view', 'engagement:edit'])
        staff = get_or_create_account_for_legacy_user(staff_user)
        staff_user.admin_membership.account = staff; staff_user.admin_membership.save(update_fields=['account'])
        self.client = APIClient(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(staff)[0])

    def test_admin_update_affects_eligible_unused_terms_and_next_month_quantity(self):
        now = timezone.now()
        issue_monthly_coupons(self.boss, self.tier.pk, now=now, allow_new_period=True)
        grant_safety_box(self.boss, self.tier)
        monthly = list(UserCoupon.objects.filter(account=self.boss, coupon__grant_key__startswith='vip-month:').select_related('coupon').order_by('pk'))
        used, unused = monthly
        used.status = 'USED'; used.vip_terms_locked_by_downgrade = True
        used.save(update_fields=['status', 'vip_terms_locked_by_downgrade'])
        safe = UserCoupon.objects.select_related('coupon').get(account=self.boss, coupon__grant_key__startswith='vip-safe:')
        with patch('coupons.notifier.notify_coupon_catalog_changed') as notify:
            with self.captureOnCommitCallbacks(execute=True):
                result = self.client.patch(f'/api/admin/vip-tiers/{self.tier.pk}/',
                    {'monthly_discount':90, 'monthly_coupon_count':1, 'season_discount':70}, format='json')
            self.assertEqual(result.status_code, 200, result.data)
            notify.assert_called_once()
        self.assertTrue(self.client.get('/api/admin/club-config/').data['data']['vip_coupon_terms_config_enabled'])
        self.boss.refresh_from_db(); self.tier.refresh_from_db()
        self.assertEqual(effective_coupon(self.boss, unused, vip_enabled=True).discount_rate, 90)
        self.assertEqual(effective_coupon(self.boss, used, vip_enabled=True).discount_rate, 80)
        self.assertEqual(effective_coupon(self.boss, safe, vip_enabled=True).discount_rate, 70)
        issue_monthly_coupons(self.boss, self.tier.pk, now=now)
        unused.refresh_from_db(); unused.coupon.refresh_from_db(); used.refresh_from_db(); used.coupon.refresh_from_db()
        self.assertEqual((unused.coupon.discount_rate, used.coupon.discount_rate), (90, 80))
        self.assertEqual(VipMonthlyCouponGrant.objects.get(account=self.boss).issued_count, 2)
        later = (now.replace(day=28) + timedelta(days=4)).replace(day=1)
        next_grant = issue_monthly_coupons(self.boss, self.tier.pk, now=later, allow_new_period=True)
        self.assertEqual((next_grant.issued_count, next_grant.issued_discount), (1, 90))

    @override_settings(VIP_THRESHOLD_RECALC_ENABLED=True)
    def test_configured_birthday_and_monthly_cap_follow_store_terms(self):
        from engagement.models import MemberProfile
        initial = {'birthday': True, 'birthday_discount': 82, 'birthday_max_discount': 2400,
                   'monthly_max_discount': 3000}
        response = self.client.patch(f'/api/admin/vip-tiers/{self.tier.pk}/',
                                     {'benefits': {'issuance': initial}}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        MemberProfile.objects.update_or_create(account=self.boss, defaults={'birthday': timezone.localdate()})
        grant_periodic(self.boss)
        monthly = UserCoupon.objects.select_related('coupon').filter(account=self.boss,
            coupon__grant_key__startswith='vip-month:').first()
        birthday = UserCoupon.objects.select_related('coupon').get(account=self.boss,
            coupon__grant_key__startswith='vip-birthday:')
        self.assertEqual((monthly.coupon.max_discount, birthday.coupon.discount_rate,
                          birthday.coupon.max_discount), (3000, 82, 2400))
        changed = {**initial, 'monthly_max_discount': 1200, 'birthday_discount': 70,
                   'birthday_max_discount': 1000}
        response = self.client.patch(f'/api/admin/vip-tiers/{self.tier.pk}/',
                                     {'benefits': {'issuance': changed}}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.boss.refresh_from_db()
        self.assertEqual(effective_coupon(self.boss, monthly, vip_enabled=True).max_discount, 1200)
        grant_periodic(self.boss)
        monthly.coupon.refresh_from_db(); birthday.coupon.refresh_from_db()
        self.assertEqual((monthly.coupon.max_discount, birthday.coupon.discount_rate,
                          birthday.coupon.max_discount), (1200, 82, 2400))

    @skipUnless(os.environ.get('XA_VIP_MONTHLY_CUTOVER_FORMAL') == '1',
                '切换前 VIP 券设置控件不可用属 VIP 月度切换正式契约深水区（18-盘点 VIP 权益待联调）；最终联调启用')
    def test_setting_controls_are_unavailable_before_cutover(self):
        VipMonthlyCutover.objects.all().delete()
        self.assertFalse(self.client.get('/api/admin/club-config/').data['data']['vip_coupon_terms_config_enabled'])

    def test_invalid_terms_and_unauthorized_update_do_not_change_configuration(self):
        for payload in ({'monthly_discount':0}, {'season_discount':101}, {'monthly_coupon_count':-1}):
            response = self.client.patch(f'/api/admin/vip-tiers/{self.tier.pk}/', payload, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        user = make_console_user(['engagement:view'])
        staff = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = staff; user.admin_membership.save(update_fields=['account'])
        viewer = APIClient(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(staff)[0])
        response = viewer.patch(f'/api/admin/vip-tiers/{self.tier.pk}/', {'monthly_discount':90}, format='json')
        self.assertEqual(response.status_code, 403, response.data)
        self.tier.refresh_from_db()
        self.assertEqual((self.tier.monthly_coupon_count, self.tier.monthly_discount, self.tier.season_discount), (2, 80, 80))

    def test_existing_safe_coupon_updates_while_upgrade_history_is_under_review(self):
        grant_safety_box(self.boss, self.tier)
        coupon = UserCoupon.objects.select_related('coupon').get(account=self.boss, coupon__grant_key__startswith='vip-safe:')
        VipUpgradeReview.objects.create(account=self.boss, tier=self.tier, business_cycle=self.boss.business_cycle,
                                        epoch=1, evidence={'quota_floors':{}})
        response = self.client.patch(f'/api/admin/vip-tiers/{self.tier.pk}/', {'season_discount':70}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        grant_periodic(self.boss)
        coupon.coupon.refresh_from_db()
        self.assertEqual(coupon.coupon.discount_rate, 70)
        self.assertEqual(UserCoupon.objects.filter(account=self.boss, coupon__grant_key__startswith='vip-safe:').count(), 1)

    @override_settings(VIP_MONTHLY_DECISIONS_ENABLED=False)
    @skipUnless(os.environ.get('XA_VIP_MONTHLY_CUTOVER_FORMAL') == '1',
                '新规则禁用时 VIP 券设置控件不可用属 VIP 月度切换正式契约深水区（18-盘点 VIP 权益待联调）；最终联调启用')
    def test_setting_controls_are_unavailable_when_new_rules_disabled(self):
        self.assertFalse(self.client.get('/api/admin/club-config/').data['data']['vip_coupon_terms_config_enabled'])
