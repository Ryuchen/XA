"""Real Redis, Celery beat and worker settle an isolated historical order."""
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
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from engagement.models import ConsumptionConfirmation
from orders.models import Order
from orders.tests import test_release_flow as flow
from wallet.models import ProviderReport, Wallet


@skipUnless(os.environ.get('XA_SETTLEMENT_BEAT_TEST') == '1', 'Requires an isolated file database and broker')
@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False)
class SettlementBeatNetworkTest(TransactionTestCase):
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    finish = flow.ReleaseFlowTest.finish

    def setUp(self):
        self.client = APIClient()
        flow.ReleaseFlowTest.setUp(self)

    def test_scheduled_worker_settles_without_manual_task_invocation(self):
        database = os.environ.get('XA_ASGI_NETWORK_TEST_DB')
        self.assertTrue(database and connection.settings_dict['NAME'] == database)
        order = self.new_order()
        report = self.finish(order)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
            {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        completed = timezone.now() - timedelta(hours=74)
        submitted = completed + timedelta(minutes=10)
        Order.objects.filter(pk=order.pk).update(completed_at=completed, report_ready_at=submitted,
            consumption_ready_at=submitted + timedelta(hours=72))
        ProviderReport.objects.filter(pk=report.pk).update(submitted_at=submitted)

        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        password, token = secrets.token_hex(16), secrets.token_hex(10)
        name = 'xa-settlement-beat-' + token
        docker = ['wsl.exe', '-d', 'XA-Ubuntu-24.04', '-u', 'root', '--', 'docker']
        repo = Path(settings.BASE_DIR).parent.parent
        env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.settlement_beat_test_settings',
               'XA_RESERVATION_TEST_BROKER': f'redis://:{password}@127.0.0.1:{port}/0',
               'KOOK_ENABLED': 'false', 'WECOM_DISPATCH_ENABLED': 'false'}
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        processes = []
        broker = redis.Redis(host='127.0.0.1', port=port, password=password, socket_timeout=.2)
        try:
            subprocess.run(docker + ['run', '-d', '--rm', '--name', name, '-p', f'127.0.0.1:{port}:6379',
                'redis:alpine', 'redis-server', '--save', '', '--appendonly', 'no', '--requirepass', password],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
            for _ in range(150):
                try:
                    if broker.ping():
                        break
                except redis.RedisError:
                    time.sleep(.1)
            else:
                self.fail('isolated Redis did not start')
            for command in ([sys.executable, '-m', 'celery', '-A', 'config', 'worker', '-P', 'solo', '-l', 'WARNING'],
                            [sys.executable, '-m', 'celery', '-A', 'config', 'beat', '-l', 'WARNING',
                             '--schedule', str(repo / '.tools' / ('settlement-beat-' + token))]):
                processes.append(subprocess.Popen(command, cwd=settings.BASE_DIR, env=env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags))
            deadline = time.monotonic() + 100
            while time.monotonic() < deadline:
                if any(process.poll() is not None for process in processes):
                    self.fail('isolated worker or beat exited early')
                order.refresh_from_db()
                if order.settled_at and order.consumption_confirmed_at:
                    break
                time.sleep(.25)
            else:
                self.fail('scheduled task did not settle the due order')
            self.assertEqual(Wallet.objects.get(user=self.provider).balance, 8000)
            self.assertEqual(ConsumptionConfirmation.objects.filter(order=order).count(), 1)
            self.assertEqual(order.transactions.filter(tx_type='INCOME', amount__gt=0).count(), 1)
            self.assertEqual(sum(order.transactions.values_list('amount', flat=True)), 0)
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            try:
                broker.shutdown(nosave=True)
            except redis.RedisError:
                pass
            broker.close()
            subprocess.run(docker + ['stop', name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, creationflags=flags)
