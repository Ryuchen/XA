"""Real worker and beat automatically expire isolated bookings."""
import os
import secrets
import socket
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless

import redis
from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Reservation, ReservationStatusLog
from orders.tests.factories import make_provider, make_service, make_user
from site_messages.models import Message


@skipUnless(os.environ.get('XA_RESERVATION_BEAT_TEST') == '1', 'Requires isolated Redis and real worker/beat opt-in')
class ReservationBeatNetworkTest(TransactionTestCase):
    def test_beat_expires_pending_and_confirmed_without_a_read(self):
        database = os.environ.get('XA_ASGI_NETWORK_TEST_DB')
        if not database or connection.settings_dict['NAME'] != database:
            self.fail('Test requires its explicitly isolated file-backed SQLite database')
        boss, provider = make_user(), make_provider()
        boss_account, provider_account = (get_or_create_account_for_legacy_user(user) for user in (boss, provider))
        for user, account in ((boss, boss_account), (provider, provider_account)):
            link_legacy_relations(user, account)
        provider.escort_profile.account = provider_account
        provider.escort_profile.save(update_fields=['account'])
        service = make_service(price=1000)
        clients = []
        for account in (boss_account, provider_account):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            clients.append(client)
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        ids = []
        for offset in (0, 2):
            result = clients[0].post('/api/orders/reservations/', {
                'provider_account_id': provider_account.pk, 'service_id': service.pk,
                'start_time': (start + timedelta(hours=offset)).isoformat(),
                'end_time': (start + timedelta(hours=offset + 1)).isoformat(), 'game_rounds': 1,
            }, format='json').data
            self.assertEqual(result['code'], 0, result)
            ids.append(result['data']['id'])
        self.assertEqual(clients[1].post(f'/api/orders/reservations/{ids[1]}/confirm/', {}, format='json').data['code'], 0)
        deadline = timezone.now() + timedelta(seconds=4)
        Reservation.objects.filter(pk__in=ids).update(start_time=deadline - timedelta(hours=1), end_time=deadline)

        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        password = secrets.token_hex(16)
        token = secrets.token_hex(10)
        repo = Path(settings.BASE_DIR).parent.parent
        broker = f'redis://:{password}@127.0.0.1:{port}/0'
        env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.reservation_beat_test_settings',
               'XA_RESERVATION_TEST_BROKER': broker, 'KOOK_ENABLED': 'false', 'WECOM_DISPATCH_ENABLED': 'false',
               'WECHAT_APPID': '', 'WECHAT_SECRET': ''}
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        processes = []
        redis_client = redis.Redis(host='127.0.0.1', port=port, password=password, socket_timeout=.2)
        try:
            server = subprocess.Popen(['wsl.exe', '-d', 'XA-Ubuntu-24.04', '--', 'env',
                'LD_LIBRARY_PATH=/tmp/xa-report-redis-qa/root/usr/lib/x86_64-linux-gnu',
                '/tmp/xa-report-redis-qa/root/usr/bin/redis-server', '--bind', '127.0.0.1', '--port', str(port),
                '--save', '', '--appendonly', 'no', '--requirepass', password],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
            processes.append(server)
            for _ in range(150):
                if server.poll() is not None:
                    self.fail('isolated Redis exited')
                try:
                    if redis_client.ping():
                        break
                except redis.RedisError:
                    time.sleep(.1)
            else:
                self.fail('isolated Redis did not start')
            commands = [
                [sys.executable, '-m', 'celery', '-A', 'config', 'worker', '-P', 'solo', '-l', 'WARNING'],
                [sys.executable, '-m', 'celery', '-A', 'config', 'beat', '-l', 'WARNING',
                 '--schedule', str(repo / '.tools' / ('reservation-beat-' + token))],
            ]
            for command in commands:
                processes.append(subprocess.Popen(command, cwd=settings.BASE_DIR, env=env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags))
            deadline = time.monotonic() + 65
            while time.monotonic() < deadline:
                if any(process.poll() is not None for process in processes):
                    self.fail('isolated Redis, Celery worker, or beat exited early')
                states = list(Reservation.objects.filter(pk__in=ids).values_list('status', flat=True))
                if states == ['EXPIRED', 'EXPIRED'] and Message.objects.filter(title='预约已过期').count() == 4:
                    break
                time.sleep(.25)
            else:
                self.fail(f'beat did not expire both reservations and notify both roles: {states}')
            self.assertEqual(ReservationStatusLog.objects.filter(reservation_id__in=ids, action='EXPIRE').count(), 2)
            self.assertEqual(Message.objects.filter(title='预约已过期', recipient_account=boss_account).count(), 2)
            self.assertEqual(Message.objects.filter(title='预约已过期', recipient_account=provider_account).count(), 2)
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
