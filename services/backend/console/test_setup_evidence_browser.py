"""Real admin page against a disposable SQLite-backed ASGI process."""
import json
import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user
from wallet.test_deposit_realtime_workflow import single_asgi
from wallet.models import SystemConfig


@skipUnless(os.environ.get('XA_SETUP_EVIDENCE_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class SetupEvidenceBrowserTest(LiveServerTestCase):
    def test_real_screen_shows_evidence_and_respects_view_permissions(self):
        identities = []
        SystemConfig.objects.update_or_create(key='provider_deposit_income_percent', defaults={'value': '0'})
        for permissions in (['dictionary:view', 'dictionary:edit', 'order:view', 'deposit_rule:view', 'deposit_rule:edit'],
                            ['dictionary:view']):
            user = make_console_user(permissions)
            account = get_or_create_account_for_legacy_user(user)
            user.admin_membership.account = account
            user.admin_membership.save(update_fields=['account'])
            identities.append({'id': account.pk, 'token': issue_account_tokens(account)[0], 'permissions': permissions})
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            process = subprocess.run(['node', str(root / 'scripts/setup-evidence-browser.cjs')], cwd=root,
                input=json.dumps({'api': api, 'editor': identities[0], 'reader': identities[1]}),
                text=True, encoding='utf-8', capture_output=True, timeout=45)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
