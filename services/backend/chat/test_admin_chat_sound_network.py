"""Customer consultation reaches the admin workbench with optional sound."""
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
from orders.tests.factories import make_console_user, make_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class AdminChatSoundNetworkTest(APITransactionTestCase):
    def test_customer_message_notifies_staff_with_independent_mute(self):
        database, node = os.environ.get('XA_PROVIDER_CHAT_QA_DB'), shutil.which('node')
        if not node or not database or connection.settings_dict['NAME'] != database:
            self.skipTest('requires explicitly isolated file-backed database and Node')
        boss, second, staff = make_user(), make_user(), make_console_user(['dashboard:view', 'chat:view', 'chat:reply', 'order:view', 'order:dispatch'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, second, staff)]
        for user, account in zip((boss, second, staff), accounts):
            link_legacy_relations(user, account)
        staff.admin_membership.account = accounts[2]; staff.admin_membership.save(update_fields=['account'])
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[0])[0])
        for index in range(16):
            response = client.post('/api/chat/send/', {'content': f'隔离历史内容 {index} ' + '消息' * 35}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
        process = subprocess.Popen([sys.executable, '-m', 'daphne', '-b', '127.0.0.1', '-p', str(port), 'config.asgi:application'],
            cwd=settings.BASE_DIR, env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            for _ in range(150):
                if process.poll() is not None:
                    self.fail('isolated ASGI server exited')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2):
                        break
                except OSError:
                    time.sleep(.1)
            else:
                self.fail('isolated ASGI server did not start')
            repo = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run([node, str(repo / 'scripts/admin-chat-sound-network-live.cjs')],
                input=json.dumps({'api': f'http://127.0.0.1:{port}', 'staffToken': issue_account_tokens(accounts[2])[0],
                                  'bossToken': issue_account_tokens(accounts[0])[0],
                                  'secondBossToken': issue_account_tokens(accounts[1])[0]}),
                text=True, encoding='utf-8', capture_output=True, timeout=70, cwd=repo)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=10)
