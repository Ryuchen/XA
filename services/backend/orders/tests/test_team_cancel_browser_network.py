"""375px provider pages receive an actual order cancellation through ASGI."""
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

from club_accounts.tokens import issue_account_tokens
from orders.tests.test_team_invitation_detail import TeamInvitationDetailTest


@skipUnless(os.environ.get('XA_TEAM_CANCEL_BROWSER_NETWORK') == '1', 'Requires isolated ASGI and H5 build')
@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_RECHARGE_PROOF=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class TeamCancelBrowserNetworkTest(APITransactionTestCase):
    setUp = TeamInvitationDetailTest.setUp
    login = TeamInvitationDetailTest.login
    ok = TeamInvitationDetailTest.ok
    recharge = TeamInvitationDetailTest.recharge
    invite = TeamInvitationDetailTest.invite

    def test_open_team_pages_update_after_boss_cancels(self):
        self.run_scenario('confirm_cancel')

    def test_declined_invitation_updates_applicant_and_boss(self):
        self.run_scenario('decline')

    def run_scenario(self, scenario):
        database = os.environ.get('XA_ASGI_NETWORK_TEST_DB')
        node = shutil.which('node')
        if not database or connection.settings_dict['NAME'] != database or not node:
            self.fail('An isolated file-backed test database and Node are required')
        self.invite()
        users = (self.boss, self.provider, self.partner)
        tokens = [issue_account_tokens(self.accounts[user.pk])[0] for user in users]
        process = subprocess.Popen([sys.executable, '-m', 'daphne', '-b', '127.0.0.1', '-p', '19166', 'config.asgi:application'],
            cwd=settings.BASE_DIR, env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            for _ in range(150):
                if process.poll() is not None:
                    self.fail('isolated ASGI server exited')
                try:
                    with socket.create_connection(('127.0.0.1', 19166), timeout=.2):
                        break
                except OSError:
                    time.sleep(.1)
            else:
                self.fail('isolated ASGI server did not start')
            repo = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run([node, str(repo / 'scripts/provider-team-cancel-live-375.cjs')],
                input=json.dumps({'bossToken': tokens[0], 'tokens': tokens[1:],
                                  'ids': [str(self.accounts[user.pk].pk) for user in users[1:]],
                                  'orderId': self.order.pk, 'applicationId': self.application.pk,
                                  'scenario': scenario}),
                cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=10)
