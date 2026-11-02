"""An open payout confirmation follows real changes made by another operator."""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from unittest import skipUnless
from django.conf import settings
from django.db import connection
from django.test import override_settings
from rest_framework.test import APITransactionTestCase
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user
from wallet.test_withdraw_feedback_real import WithdrawalFeedbackRealTest as fixtures


@skipUnless(os.environ.get('XA_WITHDRAW_CONFLICT_BROWSER') == '1', 'Requires isolated browser and ASGI')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class WithdrawClaimConflictBrowserTest(APITransactionTestCase):
    setUp = fixtures.setUp
    client_for = staticmethod(fixtures.client_for)
    ok = fixtures.ok
    bind = fixtures.bind
    payload = fixtures.payload

    def test_open_payout_confirmation_invalidated_when_claim_is_released(self):
        database, node = os.environ.get('XA_ASGI_NETWORK_TEST_DB'), shutil.which('node')
        if not database or connection.settings_dict['NAME'] != database or not node:
            self.fail('An isolated file-backed database and Node are required')
        payee = self.bind('isolated-conflict-account')
        withdrawal = self.ok(self.provider_client.post('/api/wallet/withdraw/',
            self.payload(payee, 'withdraw-conflict-browser'), format='json'))['data']['request']
        claimed = self.ok(self.staff_client.post(f"/api/admin/withdrawals/{withdrawal['id']}/claim/"))['data']
        manager = make_console_user(['withdraw:view', 'withdraw:release'])
        manager_account = get_or_create_account_for_legacy_user(manager)
        manager.admin_membership.account = manager_account
        manager.admin_membership.save(update_fields=['account'])
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
        process = subprocess.Popen([sys.executable, '-m', 'daphne', '-b', '127.0.0.1', '-p', str(port), 'config.asgi:application'],
            cwd=settings.BASE_DIR, env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            for _ in range(150):
                if process.poll() is not None: self.fail('isolated ASGI server exited')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2): break
                except OSError: time.sleep(.1)
            else: self.fail('isolated ASGI server did not start')
            repo = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run([node, str(repo / 'scripts/admin-withdraw-claim-conflict-live.cjs')],
                input=json.dumps({'api': f'http://127.0.0.1:{port}', 'withdrawId': withdrawal['id'], 'claimed': claimed,
                    'staffToken': issue_account_tokens(self.admin.admin_membership.account)[0],
                    'managerToken': issue_account_tokens(manager_account)[0]}),
                cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=70)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.wallet.refresh_from_db()
            self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (60000, 0))
        finally:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
