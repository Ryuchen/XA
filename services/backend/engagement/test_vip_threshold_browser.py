import json
import os
from datetime import timedelta
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from coupons.benefits import grant_periodic
from coupons.models import UserCoupon, VipMonthlyCutover
from engagement.models import ConsumptionConfirmation, MemberBenefitGrant, VipTier
from engagement.services import config
from orders.tests.factories import make_boss_type, make_console_user, make_order, make_user
from wallet.test_deposit_realtime_workflow import single_asgi


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
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/admin-vip-threshold-browser.cjs')], cwd=root,
                input=json.dumps({'api':api, 'action':'configure', 'staff':{'id':staff.pk,
                    'token':issue_account_tokens(staff)[0], 'permissions':permissions}}),
                text=True, encoding='utf-8', capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
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
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/admin-vip-threshold-browser.cjs')], cwd=root,
                input=json.dumps({'api': api, 'staff': {'id':staff.pk, 'token':issue_account_tokens(staff)[0],
                                                       'permissions':permissions}}),
                text=True, encoding='utf-8', capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
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
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/admin-vip-threshold-browser.cjs')], cwd=root,
                input=json.dumps({'api': api, 'action': 'reactivate', 'staff': {'id':staff.pk,
                    'token':issue_account_tokens(staff)[0], 'permissions':permissions}}),
                text=True, encoding='utf-8', capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
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
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/admin-vip-threshold-browser.cjs')], cwd=root,
                input=json.dumps({'api': api, 'action': 'create', 'staff': {'id':staff.pk,
                    'token':issue_account_tokens(staff)[0], 'permissions':permissions}}),
                text=True, encoding='utf-8', capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        tier = VipTier.objects.get(boss_type=kind)
        boss.refresh_from_db()
        self.assertEqual((tier.level, tier.recharge_threshold, boss.boss_type_id), (3, 400, kind.pk))
        self.assertEqual(MemberBenefitGrant.objects.filter(account=boss, tier=tier).count(), 1)
        self.assertEqual(UserCoupon.objects.filter(account=boss, coupon__grant_key__startswith='vip-month:').count(), 1)
