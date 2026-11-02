"""Exercise authenticated HTTP and WebSockets over a real ASGI server."""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from users.models import EscortProfile


class ReservationAsgiNetworkTest(TransactionTestCase):
    def test_create_confirm_cancel_updates_three_roles_over_network(self):
        node = shutil.which('node')
        database = os.environ.get('XA_ASGI_NETWORK_TEST_DB')
        if not node or not database or connection.settings_dict['NAME'] != database:
            self.skipTest('requires Node and the explicitly isolated file-backed ASGI test database')

        boss, provider, unrelated = make_user(), make_provider(), make_user()
        staff = make_console_user(['order:view'])
        accounts = []
        for user in (boss, provider, staff, unrelated):
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            accounts.append(account)
        EscortProfile.objects.filter(user=provider).update(account=accounts[1])
        AdminMembership.objects.filter(user=staff).update(account=accounts[2])
        service = make_service(price=1000)
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        repo = Path(settings.BASE_DIR).parent.parent
        env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.asgi_network_test_settings',
               'WECHAT_APPID': '', 'WECHAT_SECRET': '', 'KOOK_ENABLED': 'false',
               'WECOM_DISPATCH_ENABLED': 'false'}
        process = subprocess.Popen(
            [sys.executable, '-m', 'daphne', '-b', '127.0.0.1', '-p', str(port), 'config.asgi:application'],
            cwd=settings.BASE_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        try:
            for _ in range(150):
                if process.poll() is not None:
                    self.fail('isolated ASGI process exited before accepting connections')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2):
                        break
                except OSError:
                    time.sleep(.1)
            else:
                self.fail('isolated ASGI process did not start')
            result = subprocess.run(
                [node, str(repo / 'scripts/asgi-reservation-network.cjs')],
                input=json.dumps({'port': port, 'tokens': [issue_account_tokens(account)[0] for account in accounts],
                                  'provider': accounts[1].pk, 'service': service.pk,
                                  'start': start.isoformat(), 'end': (start + timedelta(hours=1)).isoformat()}),
                text=True, encoding='utf-8', capture_output=True, timeout=45, cwd=repo,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
