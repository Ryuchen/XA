"""Provider H5 reflects real staff withdrawal actions without a page refresh."""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.test import override_settings
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user
from wallet.test_withdraw_feedback_real import WithdrawalFeedbackRealTest as fixtures


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class WithdrawProviderRealtimeBrowserTest(APITransactionTestCase):
    setUp = fixtures.setUp
    client_for = staticmethod(fixtures.client_for)
    ok = fixtures.ok
    bind = fixtures.bind
    payload = fixtures.payload

    def test_claim_release_and_payout_refresh_visible_wallet(self):
        node, database, build = shutil.which('node'), os.environ.get('XA_ASGI_NETWORK_TEST_DB'), os.environ.get('XA_WITHDRAW_H5_ROOT')
        if not node or not database or connection.settings_dict['NAME'] != database or not build:
            self.skipTest('requires isolated ASGI database and provider H5 build')
        payee = self.bind('realtime-wallet-1234')
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'dashboard:view']
        role.save(update_fields=['permissions'])
        withdrawal = self.ok(self.provider_client.post('/api/wallet/withdraw/',
            self.payload(payee, 'provider-realtime-progress'), format='json'))['data']['request']
        manager = make_console_user(['withdraw:view', 'withdraw:release'])
        manager_account = get_or_create_account_for_legacy_user(manager)
        manager.admin_membership.account = manager_account
        manager.admin_membership.save(update_fields=['account'])
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.asgi_network_test_settings',
               'WECHAT_APPID': '', 'WECHAT_SECRET': '', 'KOOK_ENABLED': 'false', 'WECOM_DISPATCH_ENABLED': 'false'}
        process = subprocess.Popen([sys.executable, '-m', 'daphne', '-b', '127.0.0.1', '-p', str(port), 'config.asgi:application'],
            cwd=settings.BASE_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            for _ in range(150):
                if process.poll() is not None:
                    self.fail('isolated ASGI process exited')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2):
                        break
                except OSError:
                    time.sleep(.1)
            else:
                self.fail('isolated ASGI process did not start')
            repo = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run([node, str(repo / 'scripts/provider-withdraw-progress-realtime-live.cjs')],
                input=json.dumps({'api': f'http://127.0.0.1:{port}', 'token': issue_account_tokens(self.account)[0],
                    'id': self.account.pk, 'withdrawId': withdrawal['id'],
                    'secondPayload': self.payload(payee, 'provider-realtime-rejected'),
                    'staffToken': issue_account_tokens(self.admin.admin_membership.account)[0],
                    'managerToken': issue_account_tokens(manager_account)[0]}),
                text=True, encoding='utf-8', capture_output=True, timeout=90, cwd=repo, env=env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            final = next(row for row in self.ok(self.provider_client.get('/api/wallet/withdraw/'))['data']['requests']
                         if row['id'] == withdrawal['id'])
            self.assertEqual((final['status'], final['processing'], final['payout_reference']),
                             ('APPROVED', False, 'ISOLATED-REALTIME'))
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=10)
