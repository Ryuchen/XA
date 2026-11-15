"""VIP binding options exercised by the real admin UI and API."""
import json
import os
import subprocess
from pathlib import Path
from unittest import skipUnless
from django.conf import settings
from django.test import LiveServerTestCase, override_settings
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
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/admin-vip-binding-live.cjs')], cwd=repo,
            input=json.dumps({'base': self.live_server_url, 'root': os.environ['XA_ADMIN_VUE_ROOT'],
                              'token': issue_account_tokens(account)[0], 'staleId': old.pk}),
            text=True, encoding='utf-8', capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        tier.refresh_from_db()
        self.assertEqual(tier.safe_box_service_ids, [target.pk])
