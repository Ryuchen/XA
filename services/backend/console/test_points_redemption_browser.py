import json
import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog
from orders.points_redemption_config import current_rules
from orders.tests.factories import make_console_user
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_POINTS_CONFIG_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class PointsConfigBrowserTest(LiveServerTestCase):
    def test_editor_saves_decimal_terms_and_viewer_cannot_edit(self):
        identities = []
        for permissions in (['dictionary:view'], ['dictionary:view', 'dictionary:edit']):
            user = make_console_user(permissions)
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            user.admin_membership.account = account
            user.admin_membership.save(update_fields=['account'])
            identities.append({'id': account.pk, 'token': issue_account_tokens(account)[0],
                               'permissions': permissions, 'canEdit': 'dictionary:edit' in permissions})
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            process = subprocess.run(['node', str(root / 'scripts/points-redemption-config-browser.cjs')], cwd=root,
                input=json.dumps({'api': api, 'viewer': identities[0], 'editor': identities[1]}),
                text=True, encoding='utf-8', capture_output=True, timeout=45)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(current_rules(), {'group_point_hundredths': 1234, 'group_coin_units': 30,
                                           'max_ratio_basis_points': 4555, 'version': 1})
        self.assertEqual(AdminAuditLog.objects.filter(resource='points-redemption-terms').count(), 1)
