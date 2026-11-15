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
from coupons.models import VipMonthlyCutover
from engagement.models import ConsumptionConfirmation, MemberBenefitGrant, VipTier, VipUpgradeReview
from engagement.services import config
from orders.tests.factories import make_boss_type, make_console_user, make_order, make_user
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_VIP_THRESHOLD_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
class VipResumeBrowserTest(LiveServerTestCase):
    def test_unknown_history_reenable_and_manual_verification(self):
        user = make_user(); boss = get_or_create_account_for_legacy_user(user)
        low = VipTier.objects.create(level=1, boss_type=make_boss_type(name='核对初级'), recharge_threshold=100)
        high = VipTier.objects.create(level=2, boss_type=make_boss_type(name='核对高级'), recharge_threshold=400)
        boss.boss_type = low.boss_type; boss.save(update_fields=['boss_type'])
        order = make_order(user, customer_account=boss, amount=500, payment_status='PAID')
        ConsumptionConfirmation.objects.create(order=order, account=boss, business_cycle=boss.business_cycle, amount=500)
        operation = config(); operation.vip_enabled = False; operation.save(update_fields=['vip_enabled'])
        VipMonthlyCutover.objects.create(activated_at=timezone.now()-timedelta(minutes=1), platform_actor_id='test', operation_id='resume-ui')
        permissions = ['engagement:view', 'engagement:edit', 'boss_type:view', 'service:view']
        user = make_console_user(permissions); staff = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = staff; user.admin_membership.save(update_fields=['account'])
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/admin-vip-resume-browser.cjs')], cwd=root,
                input=json.dumps({'api':api, 'staff':{'id':staff.pk, 'token':issue_account_tokens(staff)[0], 'permissions':permissions}}),
                text=True, encoding='utf-8', capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        boss.refresh_from_db()
        self.assertEqual(boss.boss_type_id, high.boss_type_id)
        self.assertTrue(VipUpgradeReview.objects.filter(account=boss, tier=high, status='RESOLVED', resolution='ALREADY_ISSUED').exists())
        self.assertFalse(MemberBenefitGrant.objects.filter(account=boss, tier=high).exists())
