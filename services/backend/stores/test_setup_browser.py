"""A setup-only session in the real admin app against disposable SQLite."""
import json
import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from club_accounts.tokens import issue_account_tokens
from stores.context import store_context
from stores.models import Store, StoreProvisioningClaim
from stores.tests_foundation import admin
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_STORE_SETUP_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class SetupBrowserTest(LiveServerTestCase):
    def test_first_admin_sees_only_setup_and_can_save_capacity(self):
        store = Store.objects.get(slug='shenglang')
        store.status = Store.Status.CONFIGURING
        store.save(update_fields=['status'])
        with store_context(store):
            account = admin('setup-browser-owner', super_admin=True)
            account.phone = '13800001111'; account.is_phone_verified = True
            account.save(update_fields=['phone', 'is_phone_verified'])
            token = issue_account_tokens(account, auth_method='password_sms')[0]
        StoreProvisioningClaim.objects.create(store=store, admin_username=account.username, database_fingerprint='a' * 64)
        from console.permissions import get_account_permissions
        with store_context(store):
            permissions = sorted(get_account_permissions(account))
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/store-setup-browser.cjs')], cwd=root,
                input=json.dumps({'api': api, 'id': account.pk, 'token': token, 'permissions': permissions}),
                text=True, encoding='utf-8', capture_output=True, timeout=45)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
