"""Real provider H5 receives and reads staff replies over an isolated ASGI server."""
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
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ProviderChatRealtimeBrowserTest(APITransactionTestCase):
    def test_staff_reply_visible_and_read_then_recovered_after_leaving(self):
        node, database, build = shutil.which('node'), os.environ.get('XA_ASGI_NETWORK_TEST_DB'), os.environ.get('XA_PROVIDER_CHAT_H5_ROOT')
        if not node or not database or connection.settings_dict['NAME'] != database or not build:
            self.skipTest('requires isolated ASGI database and provider H5 build')
        provider, staff = make_provider(), make_console_user(['chat:view', 'chat:reply'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (provider, staff)]
        for user, account in zip((provider, staff), accounts):
            link_legacy_relations(user, account)
        staff.admin_membership.account = accounts[1]
        staff.admin_membership.save(update_fields=['account'])
        tokens = [issue_account_tokens(account)[0] for account in accounts]
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
        session = client.get('/api/chat/session/').data
        self.assertEqual(session['code'], 0, session)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
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
            result = subprocess.run([node, str(repo / 'scripts/provider-chat-realtime-375-live.cjs')],
                input=json.dumps({'api': f'http://127.0.0.1:{port}', 'token': tokens[0], 'id': accounts[0].pk,
                                  'staffToken': tokens[1], 'sessionId': session['data']['session']['id']}),
                text=True, encoding='utf-8', capture_output=True, timeout=75, cwd=repo, env=env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=10)
