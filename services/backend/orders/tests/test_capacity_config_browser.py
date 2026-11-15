"""Real admin Vite screen against an isolated file-backed ASGI database."""
import json
import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog
from orders.capacity import current_capacity_rules
from orders.tests.factories import make_console_user
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_CAPACITY_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class CapacityConfigBrowserTest(LiveServerTestCase):
    def test_admin_updates_store_capacity_through_real_page(self):
        permissions = ['order:view', 'dictionary:edit']
        staff = make_console_user(permissions)
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account
        staff.admin_membership.save(update_fields=['account'])
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            process = subprocess.run(['node', str(root / 'scripts/capacity-config-browser.cjs')], cwd=root,
                input=json.dumps({'api': api, 'staff': {'id': account.pk, 'token': issue_account_tokens(account)[0],
                    'permissions': permissions}}), text=True, encoding='utf-8', capture_output=True, timeout=45)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(current_capacity_rules(), {'max_concurrent_orders': 2, 'version': 1})
        self.assertEqual(AdminAuditLog.objects.filter(resource='provider-capacity', operator_account=account).count(), 1)
