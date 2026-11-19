import os
from unittest import skipUnless

from django.test import override_settings
from unittest.mock import patch
from django.db import DatabaseError

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from console.models import AdminAuditLog
from coupons.models import VipMonthlyCutover
from engagement.models import ConsumptionConfirmation, VipTier
from engagement.test_vip_disable_freeze import VipDisableFreezeTest
from orders.tests.factories import make_boss_type, make_console_user, make_order
from users.models import CustomUser


@override_settings(VIP_TERMS_FREEZE_ENABLED=True, VIP_MONTHLY_DECISIONS_ENABLED=True,
                   DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True)
class TierRetirementTest(VipDisableFreezeTest):
    def setUp(self):
        super().setUp()
        self.lower = VipTier.objects.create(level=2, boss_type=make_boss_type(), recharge_threshold=100)
        self.order = make_order(CustomUser.objects.get(pk=self.account.legacy_mapping.legacy_user_id), payment_status='PAID')
        ConsumptionConfirmation.objects.create(order=self.order, account=self.account,
            business_cycle=self.account.business_cycle, amount=500, refunded_amount=50)

    def preview(self):
        return self.admin.get(f'/api/admin/vip-tiers/{self.tier.pk}/retirement-preview/')

    @skipUnless(os.environ.get('XA_VIP_MONTHLY_CUTOVER_FORMAL') == '1',
                '档位退休保留条款＋重算净消费审计属 VIP 月度切换正式契约深水区（18-盘点 VIP 权益待联调）；最终联调启用')
    def test_retirement_preserves_terms_and_recalculates_net_consumption(self):
        preview = self.preview()
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data['data']['account_count'], 1)
        result = self.admin.patch(f'/api/admin/vip-tiers/{self.tier.pk}/',
            {'active': False, 'retirement_token': preview.data['data']['token']}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.tier.refresh_from_db(); self.account.refresh_from_db()
        self.safe.refresh_from_db(); self.monthly.refresh_from_db()
        self.safe.coupon.refresh_from_db(); self.monthly.coupon.refresh_from_db()
        self.assertFalse(self.tier.active)
        self.assertEqual(self.account.boss_type_id, self.lower.boss_type_id)
        self.assertTrue(self.safe.vip_terms_locked_by_downgrade)
        self.assertTrue(self.monthly.vip_terms_locked_by_downgrade)
        self.assertEqual((self.safe.coupon.discount_rate, self.safe.coupon.service_ids,
                          self.monthly.coupon.discount_rate), (50, [self.new.pk], 90))
        self.assertTrue(AdminAuditLog.objects.filter(resource='vip-tier-recalculate', object_id=str(self.account.pk)).exists())
        self.assertTrue(AdminAuditLog.objects.filter(resource='vip-tier-retirement', object_id=str(self.tier.pk)).exists())

    def test_stale_preview_cannot_deactivate(self):
        token = self.preview().data['data']['token']
        self.tier.monthly_discount = 77; self.tier.save(update_fields=['monthly_discount'])
        response = self.admin.patch(f'/api/admin/vip-tiers/{self.tier.pk}/',
            {'active': False, 'retirement_token': token}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.tier.refresh_from_db(); self.account.refresh_from_db()
        self.assertTrue(self.tier.active)
        self.assertEqual(self.account.boss_type_id, self.tier.boss_type_id)

    def test_deletion_directs_to_retirement_and_keeps_historical_tier(self):
        url = f'/api/admin/vip-tiers/{self.tier.pk}/'
        self.assertEqual(self.admin.delete(url, format='json').status_code, 400)
        token = self.preview().data['data']['token']
        response = self.admin.patch(url, {'active': False, 'retirement_token': token}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.admin.delete(url, format='json').status_code, 400)
        self.assertTrue(VipTier.objects.filter(pk=self.tier.pk, active=False).exists())

    def test_tier_with_members_cannot_rebind_to_another_boss_type(self):
        other = make_boss_type()
        result = self.admin.patch(f'/api/admin/vip-tiers/{self.tier.pk}/', {'boss_type': other.pk}, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        self.tier.refresh_from_db(); self.account.refresh_from_db()
        self.assertEqual(self.account.boss_type_id, self.tier.boss_type_id)

    @override_settings(VIP_TERMS_FREEZE_ENABLED=False, VIP_MONTHLY_DECISIONS_ENABLED=False,
                       DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=False)
    def test_unavailable_protection_prevents_tier_retirement(self):
        result = self.admin.patch(f'/api/admin/vip-tiers/{self.tier.pk}/', {'active': False}, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        self.tier.refresh_from_db()
        self.assertTrue(self.tier.active)
        VipMonthlyCutover.objects.all().delete()
        result = self.admin.patch(f'/api/admin/vip-tiers/{self.tier.pk}/', {'active': False}, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        self.tier.refresh_from_db()
        self.assertTrue(self.tier.active)

    def test_inactive_boss_type_cannot_be_bound_to_active_vip(self):
        inactive = make_boss_type(is_active=False)
        result = self.admin.patch(f'/api/admin/vip-tiers/{self.tier.pk}/', {'boss_type': inactive.pk}, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        self.assertIn('启用', str(result.data))
        self.tier.refresh_from_db()
        self.assertNotEqual(self.tier.boss_type_id, inactive.pk)

    def test_audit_failure_rolls_back_tier_coupons_and_membership(self):
        token = self.preview().data['data']['token']
        with patch('console.models.AdminAuditLog.objects.create', side_effect=DatabaseError('audit failure')):
            with self.assertRaises(DatabaseError):
                self.admin.patch(f'/api/admin/vip-tiers/{self.tier.pk}/',
                    {'active': False, 'retirement_token': token}, format='json')
        self.tier.refresh_from_db(); self.account.refresh_from_db(); self.monthly.refresh_from_db()
        self.monthly.coupon.refresh_from_db()
        self.assertTrue(self.tier.active)
        self.assertEqual(self.account.boss_type_id, self.tier.boss_type_id)
        self.assertFalse(self.monthly.vip_terms_locked_by_downgrade)
        self.assertEqual(self.monthly.coupon.discount_rate, 80)

    @skipUnless(os.environ.get('XA_VIP_MONTHLY_CUTOVER_FORMAL') == '1',
                'VIP 停用期间档位退休保留停用快照属 VIP 月度切换正式契约深水区（18-盘点 VIP 权益待联调）；最终联调启用')
    def test_tier_retired_while_vip_disabled_keeps_disabled_snapshot(self):
        self.assertEqual(self.disable().status_code, 200)
        self.tier.monthly_discount = 70
        self.tier.save(update_fields=['monthly_discount'])
        token = self.preview().data['data']['token']
        response = self.admin.patch(f'/api/admin/vip-tiers/{self.tier.pk}/',
            {'active': False, 'retirement_token': token}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.monthly.refresh_from_db(); self.monthly.coupon.refresh_from_db()
        self.assertEqual(self.monthly.coupon.discount_rate, 90)
        self.assertTrue(self.monthly.vip_terms_locked_by_downgrade)

    def test_boss_type_cannot_bypass_tier_retirement(self):
        staff = make_console_user(['boss_type:view', 'boss_type:edit', 'boss_type:delete'])
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account; staff.admin_membership.save(update_fields=['account'])
        client = self.client_for(account)
        url = f'/api/admin/boss-types/{self.tier.boss_type_id}/'
        result = client.patch(url, {'is_active': False}, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        self.assertIn('VIP', str(result.data))
        self.assertEqual(client.delete(url, format='json').status_code, 400)
        self.tier.boss_type.refresh_from_db(); self.tier.refresh_from_db(); self.account.refresh_from_db()
        self.assertTrue(self.tier.boss_type.is_active)
        self.assertTrue(self.tier.active)
        self.assertEqual(self.account.boss_type_id, self.tier.boss_type_id)
        token = self.preview().data['data']['token']
        response = self.admin.patch(f'/api/admin/vip-tiers/{self.tier.pk}/',
            {'active': False, 'retirement_token': token}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        result = client.patch(url, {'is_active': False}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(client.delete(url, format='json').status_code, 400)

    def test_editing_profile_cannot_reassign_vip_and_retains_grade(self):
        staff = make_console_user(['user:view', 'user:edit'])
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account; staff.admin_membership.save(update_fields=['account'])
        client = self.client_for(account)
        url = f'/api/admin/users/{self.account.pk}/'
        response = client.patch(url, {'nickname': '已修改昵称'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.account.refresh_from_db()
        self.assertEqual((self.account.nickname, self.account.boss_type_id), ('已修改昵称', self.tier.boss_type_id))
        response = client.patch(url, {'boss_type': self.lower.boss_type_id}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.account.refresh_from_db()
        self.assertEqual(self.account.boss_type_id, self.tier.boss_type_id)
