import os
from datetime import timedelta
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog
from coupons.benefits import grant_periodic
from coupons.models import UserCoupon, VipMonthlyCutover
from engagement.models import VipTier
from engagement.services import config
from orders.tests.factories import make_boss_type, make_console_user, make_service, make_user


@skipUnless(os.environ.get('XA_VIP_TERMS_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class VipDisableBrowserTest(LiveServerTestCase):
    def test_admin_sees_effect_and_disable_keeps_coupon_terms(self):
        user = make_user(); account = get_or_create_account_for_legacy_user(user)
        service = make_service(name='停用隔离安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC')
        tier = VipTier.objects.create(level=6, boss_type=make_boss_type(), recharge_threshold=1,
            monthly_coupon_count=0, monthly_discount=90, season_discount=0, safe_box_service_ids=[service.pk])
        account.boss_type = tier.boss_type; account.save(update_fields=['boss_type']); config()
        VipMonthlyCutover.objects.create(activated_at=timezone.now()-timedelta(minutes=1),
                                         platform_actor_id='test', operation_id='disable-ui')
        grant_periodic(account)
        voucher = UserCoupon.objects.get(account=account, coupon__scope='SAFE_BOX')
        tier.season_discount = 50; tier.save(update_fields=['season_discount'])
        permissions = ['engagement:view', 'engagement:edit']
        staff = make_console_user(permissions); operator = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = operator; staff.admin_membership.save(update_fields=['account'])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(operator)[0])
        saved = client.put('/api/admin/club-config/', {'vip_enabled': False}, format='json')
        self.assertEqual((saved.status_code, saved.data['code']), (200, 0), saved.data)
        voucher.refresh_from_db(); voucher.coupon.refresh_from_db()
        self.assertEqual((config().vip_enabled, voucher.status, voucher.coupon.discount_rate), (False, 'UNUSED', 50))
        self.assertEqual(AdminAuditLog.objects.get(resource='vip-terms-disable').operator_account_id, operator.pk)
