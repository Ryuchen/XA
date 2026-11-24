"""VIP binding options exercised by the real admin API."""
import os
from unittest import skipUnless
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_boss_type, make_console_user, make_service
from .models import VipTier


@skipUnless(os.environ.get('XA_VIP_BINDING_UI') == '1', 'Requires built admin UI')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class VipBindingUITest(LiveServerTestCase):
    def test_admin_selects_only_eligible_services(self):
        staff = make_console_user(['engagement:view', 'engagement:edit'])
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account
        staff.admin_membership.save(update_fields=['account'])
        safe = make_service(name='适用安全箱', benefit_kind='SAFEBOX_33', platform='PC', billing_provider_count=1)
        old = make_service(name='双陪安全箱', benefit_kind='SAFEBOX_33', platform='PC', billing_provider_count=2)
        from orders.models import ServiceItem
        ServiceItem.objects.bulk_create([ServiceItem(name=f'安全箱可选套餐{i}', price=10000, benefit_kind='SAFEBOX_33',
            platform='PC', billing_provider_count=1) for i in range(500)])
        target = make_service(name='远端安全箱', benefit_kind='SAFEBOX_33', platform='PC', billing_provider_count=1)
        tier = VipTier.objects.create(boss_type=make_boss_type(name='绑定验收会员'), level=6,
                                      recharge_threshold=100, safe_box_service_ids=[old.pk])
        make_service(name='适用小时陪玩', benefit_kind='COMPANION_HOUR', platform='PC', price_unit='小时')
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        def options(**params):
            response = client.get('/api/admin/vip-tiers/service-options/', params)
            self.assertEqual((response.status_code, response.data['code']), (200, 0), response.data)
            return {row['name'] for row in response.data['data']}
        safe_names = options(benefit_kind='SAFEBOX_33')
        self.assertIn('适用安全箱', safe_names)
        self.assertNotIn('双陪安全箱', safe_names)
        self.assertNotIn('适用小时陪玩', safe_names)
        self.assertIn('远端安全箱', options(benefit_kind='SAFEBOX_33', keyword='远端安全箱'))
        hour_names = options(benefit_kind='COMPANION_HOUR')
        self.assertIn('适用小时陪玩', hour_names)
        self.assertNotIn('适用安全箱', hour_names)
        stale = client.patch(f'/api/admin/vip-tiers/{tier.pk}/', {'safe_box_service_ids': [old.pk]}, format='json')
        self.assertNotEqual(stale.data.get('code', 400), 0)
        saved = client.patch(f'/api/admin/vip-tiers/{tier.pk}/', {'safe_box_service_ids': [target.pk]}, format='json')
        self.assertEqual((saved.status_code, saved.data['code']), (200, 0), saved.data)
        tier.refresh_from_db()
        self.assertEqual(tier.safe_box_service_ids, [target.pk])
