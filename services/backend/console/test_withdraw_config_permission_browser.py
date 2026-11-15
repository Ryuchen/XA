import json
import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_WITHDRAW_CONFIG_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class WithdrawConfigPermissionBrowserTest(LiveServerTestCase):
    def test_staff_ui_respects_separate_config_permission(self):
        identities = []
        for permissions in (['withdraw:view', 'withdraw:audit'], ['withdraw:view', 'withdraw:config']):
            user = make_console_user(permissions)
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            user.admin_membership.account = account
            user.admin_membership.save(update_fields=['account'])
            identities.append({'id': account.pk, 'token': issue_account_tokens(account)[0],
                               'permissions': permissions, 'canEdit': 'withdraw:config' in permissions})
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            process = subprocess.run(['node', str(root / 'scripts/withdraw-config-permission-browser.cjs')], cwd=root,
                input=json.dumps({'api': api, 'reviewer': identities[0], 'editor': identities[1]}),
                text=True, encoding='utf-8', capture_output=True, timeout=45)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
