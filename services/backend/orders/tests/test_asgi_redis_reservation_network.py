"""Real cross-process Redis delivery for reservation state changes."""
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import redis
from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from users.models import EscortProfile


def unused_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class ReservationRedisAsgiNetworkTest(TransactionTestCase):
    def test_cross_process_create_confirm_cancel_updates_three_roles(self):
        node = shutil.which('node')
        database = os.environ.get('XA_ASGI_NETWORK_TEST_DB')
        if os.environ.get('XA_ASGI_REDIS_NETWORK') != '1' or not node or not database or connection.settings_dict['NAME'] != database:
            self.skipTest('requires explicitly isolated ASGI database and Redis network opt-in')

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
        ports = set()
        while len(ports) < 3:
            ports.add(unused_port())
        redis_port, http_port, socket_port = ports
        password = secrets.token_hex(16)
        prefix = 'xa_asgi_test_' + secrets.token_hex(8)
        repo = Path(settings.BASE_DIR).parent.parent
        env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.asgi_redis_network_test_settings',
               'XA_ASGI_TEST_REDIS_URL': f'redis://:{password}@127.0.0.1:{redis_port}/0',
               'XA_ASGI_TEST_REDIS_PREFIX': prefix, 'WECHAT_APPID': '', 'WECHAT_SECRET': '',
               'KOOK_ENABLED': 'false', 'WECOM_DISPATCH_ENABLED': 'false'}
        processes = []
        redis_client = redis.Redis(host='127.0.0.1', port=redis_port, password=password, socket_timeout=.2)
        creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        try:
            server = subprocess.Popen(
                ['wsl.exe', '-d', 'XA-Ubuntu-24.04', '--', 'env',
                 'LD_LIBRARY_PATH=/tmp/xa-report-redis-qa/root/usr/lib/x86_64-linux-gnu',
                 '/tmp/xa-report-redis-qa/root/usr/bin/redis-server', '--bind', '127.0.0.1',
                 '--port', str(redis_port), '--save', '', '--appendonly', 'no', '--requirepass', password],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creationflags,
            )
            processes.append(server)
            for _ in range(150):
                if server.poll() is not None:
                    self.fail('isolated Redis process exited before accepting connections')
                try:
                    if redis_client.ping():
                        break
                except redis.RedisError:
                    time.sleep(.1)
            else:
                self.fail('isolated Redis process did not start')
            for port in (http_port, socket_port):
                process = subprocess.Popen(
                    [sys.executable, '-m', 'daphne', '-b', '127.0.0.1', '-p', str(port), 'config.asgi:application'],
                    cwd=settings.BASE_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
                processes.append(process)
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
            self.assertNotEqual(processes[1].pid, processes[2].pid)
            result = subprocess.run(
                [node, str(repo / 'scripts/asgi-reservation-network.cjs')],
                input=json.dumps({'httpPort': http_port, 'socketPort': socket_port,
                                  'tokens': [issue_account_tokens(account)[0] for account in accounts],
                                  'provider': accounts[1].pk, 'service': service.pk,
                                  'start': start.isoformat(), 'end': (start + timedelta(hours=1)).isoformat()}),
                text=True, encoding='utf-8', capture_output=True, timeout=45, cwd=repo,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        finally:
            for process in processes[1:]:
                process.terminate()
            for process in processes[1:]:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            try:
                redis_client.shutdown(nosave=True)
            except redis.RedisError:
                pass
            if processes:
                try:
                    processes[0].wait(timeout=10)
                except subprocess.TimeoutExpired:
                    processes[0].terminate()
                    processes[0].wait(timeout=10)
