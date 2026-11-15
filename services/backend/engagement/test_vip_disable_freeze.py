from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog
from coupons.benefits import grant_periodic
from coupons.models import Coupon, UserCoupon, VipMonthlyCutover
from engagement.models import VipTier
from engagement.services import config
from orders.tests.factories import make_boss_type, make_console_user, make_service, make_user


@override_settings(VIP_TERMS_FREEZE_ENABLED=True, VIP_MONTHLY_DECISIONS_ENABLED=True,
    DISCOUNT_OPTIONS_PREVIEW_ENABLED=True, DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True,
    LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class VipDisableFreezeTest(APITestCase):
    def setUp(self):
        user = make_user(); self.account = get_or_create_account_for_legacy_user(user)
        self.old = make_service(name='停用前原安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC')
        self.new = make_service(name='停用前新安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC')
        self.tier = VipTier.objects.create(level=6, boss_type=make_boss_type(discount_rate=85), recharge_threshold=1,
            monthly_coupon_count=1, monthly_discount=80, season_discount=0, safe_box_service_ids=[self.old.pk])
        self.account.boss_type = self.tier.boss_type; self.account.save(update_fields=['boss_type'])
        config()
        VipMonthlyCutover.objects.create(activated_at=timezone.now()-timedelta(minutes=1),
                                         platform_actor_id='test', operation_id='disable-freeze')
        grant_periodic(self.account)
        self.safe = UserCoupon.objects.select_related('coupon').get(account=self.account, coupon__scope='SAFE_BOX')
        self.monthly = UserCoupon.objects.select_related('coupon').get(account=self.account, coupon__grant_key__startswith='vip-month:')
        self.tier.monthly_discount = 90; self.tier.season_discount = 50; self.tier.safe_box_service_ids = [self.new.pk]
        self.tier.save(update_fields=['monthly_discount', 'season_discount', 'safe_box_service_ids'])
        staff = make_console_user(['engagement:view', 'engagement:edit'])
        self.staff = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = self.staff; staff.admin_membership.save(update_fields=['account'])
        self.admin = self.client_for(self.staff)
        self.boss = self.client_for(self.account)

    @staticmethod
    def client_for(account):
        return APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])

    def disable(self):
        return self.admin.put('/api/admin/club-config/', {'vip_enabled': False}, format='json')

    def test_disable_materializes_current_terms_and_reenable_uses_later_rules(self):
        result = self.disable()
        self.assertEqual(result.data['code'], 0, result.data)
        self.safe.coupon.refresh_from_db(); self.monthly.coupon.refresh_from_db()
        self.assertEqual((self.safe.coupon.discount_rate, self.safe.coupon.service_ids, self.monthly.coupon.discount_rate),
                         (50, [self.new.pk], 90))
        audit = AdminAuditLog.objects.get(resource='vip-terms-disable')
        self.assertEqual((audit.operator_account_id, audit.request_body['updated']), (self.staff.pk, 2))
        self.tier.monthly_discount = 70; self.tier.season_discount = 60
        self.tier.save(update_fields=['monthly_discount', 'season_discount'])
        options = self.boss.post('/api/orders/discount-options-preview/', {'service_id':self.new.pk}, format='json').data['data']['options']
        self.assertFalse(any(item['key'] == 'vip' for item in options))
        self.assertEqual(next(item['amount'] for item in options if item['coupon_id'] == self.safe.pk), 5000)
        self.assertEqual(next(item['amount'] for item in options if item['coupon_id'] == self.monthly.pk), 9000)
        enabled = self.admin.put('/api/admin/club-config/', {'vip_enabled': True}, format='json')
        self.assertEqual(enabled.data['code'], 0, enabled.data)
        options = self.boss.post('/api/orders/discount-options-preview/', {'service_id':self.new.pk}, format='json').data['data']['options']
        self.assertEqual(next(item['amount'] for item in options if item['coupon_id'] == self.safe.pk), 6000)
        self.assertEqual(next(item['amount'] for item in options if item['coupon_id'] == self.monthly.pk), 7000)

    def test_unverifiable_owner_rolls_back_the_switch_and_prior_updates(self):
        orphan_coupon = Coupon.objects.create(name='未核实安全箱', discount_type='RATE', discount_rate=0,
            amount=1, scope='SAFE_BOX', grant_key='vip-safe:999:1:0:0', is_public=False)
        UserCoupon.objects.create(user_id=self.account.legacy_mapping.legacy_user_id, coupon=orphan_coupon)
        result = self.disable()
        self.assertEqual(result.status_code, 400)
        self.assertTrue(config().vip_enabled)
        self.safe.coupon.refresh_from_db(); self.monthly.coupon.refresh_from_db()
        self.assertEqual((self.safe.coupon.discount_rate, self.monthly.coupon.discount_rate), (0, 80))
        self.assertFalse(AdminAuditLog.objects.filter(resource='vip-terms-disable').exists())

    def test_audit_failure_rolls_back_switch_and_coupon_changes(self):
        with patch('console.models.AdminAuditLog.objects.create', side_effect=DatabaseError('isolated audit failure')):
            with self.assertRaises(DatabaseError):
                self.disable()
        self.assertTrue(config().vip_enabled)
        self.safe.coupon.refresh_from_db(); self.monthly.coupon.refresh_from_db()
        self.assertEqual((self.safe.coupon.discount_rate, self.monthly.coupon.discount_rate), (0, 80))

    def test_read_only_staff_cannot_disable_vip(self):
        staff = make_console_user(['engagement:view']); account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account; staff.admin_membership.save(update_fields=['account'])
        denied = self.client_for(account).put('/api/admin/club-config/', {'vip_enabled':False}, format='json')
        self.assertEqual(denied.status_code, 403)
        self.assertTrue(config().vip_enabled)

    @override_settings(VIP_TERMS_FREEZE_ENABLED=False, VIP_MONTHLY_DECISIONS_ENABLED=False,
                       DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=False)
    def test_cutover_store_does_not_silently_fall_back_if_flags_are_disabled(self):
        result = self.disable()
        self.assertEqual(result.status_code, 400)
        self.assertTrue(config().vip_enabled)
        self.safe.coupon.refresh_from_db(); self.assertEqual(self.safe.coupon.discount_rate, 0)
