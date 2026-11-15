"""An open provider pass page follows a real staff recharge."""
import base64
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from unittest import skipUnless
from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider
from orders.tests.test_release_flow import screenshot
from users.models import ProviderPassPurchase
from wallet.models import Wallet
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_PASS_PAGE_ROOT'), 'Requires isolated provider H5 build')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, REQUIRE_RECHARGE_PROOF=True)
class PassPageRechargeRealtimeTest(LiveServerTestCase):
    def test_recharge_with_confirmation_open_then_buy(self):
        media = TemporaryDirectory(prefix='xa-pass-page-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        provider, staff = make_provider(), make_console_user(['wallet:recharge'])
        account, admin = [get_or_create_account_for_legacy_user(user) for user in (provider, staff)]
        link_legacy_relations(provider, account); link_legacy_relations(staff, admin)
        provider.escort_profile.account = account; provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = admin; staff.admin_membership.save(update_fields=['account'])
        with single_asgi() as (base, _):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/pass-page-recharge-live.cjs')], cwd=root,
                input=json.dumps({'api':base, 'root':os.environ['XA_PASS_PAGE_ROOT'], 'account':account.pk,
                    'token':issue_account_tokens(account)[0], 'staffToken':issue_account_tokens(admin)[0],
                    'image':base64.b64encode(screenshot().read()).decode('ascii')}),
                text=True, encoding='utf-8', capture_output=True, timeout=55)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        purchase = ProviderPassPurchase.objects.get(account=account)
        self.assertEqual(Wallet.objects.get(account=account).balance, 150000 - purchase.paid_amount)
