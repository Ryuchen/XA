import json
import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone

from console.models import AdminAuditLog
from coupons.models import VipMonthlyCutover
from stores.context import store_context
from stores.models import PlatformAccount, PlatformRoleGrant, Store
from stores.platform_auth import issue_tokens
from stores.test_platform_store_browser import setup_asgi


@skipUnless(os.environ.get('XA_MONTHLY_CUTOVER_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
@override_settings(PLATFORM_IDENTITY_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False)
class PlatformMonthlyCutoverBrowserTest(LiveServerTestCase):
    databases = {'default', 'store_new'}

    def test_platform_super_admin_confirms_cutover_in_store_setup(self):
        actor = PlatformAccount(username='monthly-ui-admin', phone='13800001111', is_phone_verified=True)
        actor.set_password('Synthetic-Platform-Password-2026!'); actor.save()
        PlatformRoleGrant.objects.create(account=actor, role='SUPER_ADMIN', scope_slug='*')
        token, refresh = issue_tokens(actor, 'SMS', actor.phone)
        store = Store.objects.create(slug='monthly-ui-store', name='月券页面隔离店', entity_prefix='MU',
            currency_name='测试币', database_alias='store_new', status=Store.Status.CONFIGURING, initialized_at=timezone.now())
        with setup_asgi() as api:
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/platform-monthly-cutover-browser.cjs')], cwd=root,
                input=json.dumps({'api':api, 'token':token, 'refresh':refresh, 'id':str(actor.pk), 'username':actor.username}),
                text=True, encoding='utf-8', capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with store_context(store):
            marker = VipMonthlyCutover.objects.get(pk=1)
            audit = AdminAuditLog.objects.get(resource='vip-monthly-cutover')
            self.assertEqual((marker.platform_actor_id, marker.operation_id), (str(actor.pk), audit.request_body['operation_id']))
