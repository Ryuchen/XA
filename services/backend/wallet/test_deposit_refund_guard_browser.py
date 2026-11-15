import json
import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_order, make_provider, make_user
from wallet.services import get_wallet
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_DEPOSIT_REFUND_GUARD_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class DepositRefundGuardBrowserTest(LiveServerTestCase):
    def test_staff_sees_active_order_and_cannot_submit_refund(self):
        permissions = ['escort:view', 'escort:deposit_refund']
        staff = make_console_user(permissions)
        admin = get_or_create_account_for_legacy_user(staff); link_legacy_relations(staff, admin)
        staff.admin_membership.account = admin; staff.admin_membership.save(update_fields=['account'])
        provider = make_provider(display_name='隔离退押金陪玩', deposit_paid=3000)
        account = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = account; provider.escort_profile.save(update_fields=['account'])
        wallet = get_wallet(account=account, user=provider); wallet.balance=1000; wallet.save(update_fields=['balance'])
        make_order(make_user(), provider=provider, provider_account=account, status='IN_SERVICE')
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            process = subprocess.run(['node', str(root / 'scripts/deposit-refund-guard-browser.cjs')], cwd=root,
                input=json.dumps({'api':api, 'staff':{'id':admin.pk, 'token':issue_account_tokens(admin)[0],
                    'permissions':permissions}}), text=True, encoding='utf-8', capture_output=True, timeout=45)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        wallet.refresh_from_db(); provider.escort_profile.refresh_from_db()
        self.assertEqual((wallet.balance, provider.escort_profile.deposit_paid), (1000, 3000))
