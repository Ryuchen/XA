"""Real expiration scan and WebSocket delivery through separate ASGI/Redis processes."""
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
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership
from orders.models import Reservation, ReservationStatusLog
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from site_messages.models import Message
from users.models import EscortProfile


def unused_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ReservationExpiryRedisNetworkTest(TransactionTestCase):
    def test_expiration_updates_three_roles_and_workbench(self):
        node = shutil.which('node')
        database = os.environ.get('XA_ASGI_NETWORK_TEST_DB')
        if os.environ.get('XA_ASGI_REDIS_NETWORK') != '1' or not node or not database or connection.settings_dict['NAME'] != database:
            self.skipTest('requires explicitly isolated ASGI database and Redis network opt-in')
        boss, provider, unrelated = make_user(), make_provider(), make_user()
        staff = make_console_user(['dashboard:view', 'order:view'])
        accounts = []
        for user in (boss, provider, staff, unrelated):
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            accounts.append(account)
        EscortProfile.objects.filter(user=provider).update(account=accounts[1])
        AdminMembership.objects.filter(user=staff).update(account=accounts[2])
        service = make_service(price=1000)
        tokens = [issue_account_tokens(account)[0] for account in accounts]
        boss_client, provider_client = APIClient(), APIClient()
        boss_client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
        provider_client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[1])
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        ids = []
        for offset in (0, 2):
            begin = start + timedelta(hours=offset)
            result = boss_client.post('/api/orders/reservations/', {'provider_account_id': accounts[1].pk,
                'service_id': service.pk, 'start_time': begin.isoformat(), 'end_time': (begin + timedelta(hours=1)).isoformat()}, format='json').data
            self.assertEqual(result['code'], 0, result)
            ids.append(result['data']['id'])
        result = provider_client.post(f'/api/orders/reservations/{ids[1]}/confirm/', {}, format='json').data
        self.assertEqual(result['code'], 0, result)
        # Test fixture moves the already-created windows near their end; all expiration
        # and event operations themselves run through the production service.
        deadline = timezone.now() + timedelta(seconds=15)
        Reservation.objects.filter(pk__in=ids).update(start_time=deadline - timedelta(hours=1), end_time=deadline)
        ports = set()
        while len(ports) < 3:
            ports.add(unused_port())
        redis_port, http_port, socket_port = ports
        password, prefix = secrets.token_hex(16), 'xa_asgi_test_' + secrets.token_hex(8)
        repo = Path(settings.BASE_DIR).parent.parent
        env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.asgi_redis_network_test_settings',
               'XA_ASGI_TEST_REDIS_URL': f'redis://:{password}@127.0.0.1:{redis_port}/0',
               'XA_ASGI_TEST_REDIS_PREFIX': prefix, 'WECHAT_APPID': '', 'WECHAT_SECRET': '',
               'KOOK_ENABLED': 'false', 'WECOM_DISPATCH_ENABLED': 'false'}
        processes = []
        redis_client = redis.Redis(host='127.0.0.1', port=redis_port, password=password, socket_timeout=.2)
        creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        try:
            server = subprocess.Popen(['wsl.exe', '-d', 'XA-Ubuntu-24.04', '--', 'env',
                'LD_LIBRARY_PATH=/tmp/xa-report-redis-qa/root/usr/lib/x86_64-linux-gnu',
                '/tmp/xa-report-redis-qa/root/usr/bin/redis-server', '--bind', '127.0.0.1',
                '--port', str(redis_port), '--save', '', '--appendonly', 'no', '--requirepass', password],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creationflags)
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
                process = subprocess.Popen([sys.executable, '-m', 'daphne', '-b', '127.0.0.1', '-p', str(port), 'config.asgi:application'],
                    cwd=settings.BASE_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creationflags)
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
            result = subprocess.run([node, str(repo / 'scripts/asgi-expired-reservation-network.cjs')],
                input=json.dumps({'httpPort': http_port, 'socketPort': socket_port, 'tokens': tokens, 'ids': ids,
                    'deadline': deadline.isoformat(), 'python': sys.executable, 'backend': str(settings.BASE_DIR)}),
                text=True, encoding='utf-8', capture_output=True, timeout=60, cwd=repo, env=env)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(Reservation.objects.filter(pk__in=ids, status='EXPIRED').count(), 2)
            self.assertEqual(ReservationStatusLog.objects.filter(reservation_id__in=ids, action='EXPIRE').count(), 2)
            self.assertEqual(Message.objects.filter(title='预约已过期', recipient_account_id__in=[accounts[0].pk, accounts[1].pk]).count(), 4)
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
