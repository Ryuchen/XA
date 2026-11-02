"""Actual two-provider handoff reaches both Vue pages and all order parties."""
import json
import os
import shutil
import secrets
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
import redis
from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_provider


@contextmanager
def isolated_realtime():
    """Two Daphne workers communicate only through a disposable Redis instance."""
    def port():
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            return sock.getsockname()[1]
    ports = set()
    while len(ports) < 3:
        ports.add(port())
    redis_port, api_port, ws_port = ports
    password, prefix = secrets.token_hex(16), 'xa_asgi_test_' + secrets.token_hex(8)
    env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.asgi_redis_network_test_settings',
           'XA_ASGI_TEST_REDIS_URL': f'redis://:{password}@127.0.0.1:{redis_port}/0',
           'XA_ASGI_TEST_REDIS_PREFIX': prefix, 'XA_ASGI_TEST_MEDIA_ROOT': str(settings.MEDIA_ROOT),
           'KOOK_ENABLED': 'false', 'WECOM_DISPATCH_ENABLED': 'false'}
    processes = []
    client = None
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        processes.append(subprocess.Popen(['wsl.exe', '-d', 'XA-Ubuntu-24.04', '--', 'env',
            'LD_LIBRARY_PATH=/tmp/xa-report-redis-qa/root/usr/lib/x86_64-linux-gnu',
            '/tmp/xa-report-redis-qa/root/usr/bin/redis-server', '--bind', '127.0.0.1', '--port', str(redis_port),
            '--save', '', '--appendonly', 'no', '--requirepass', password], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags))
        client = redis.Redis(host='127.0.0.1', port=redis_port, password=password, socket_timeout=.2)
        for _ in range(150):
            if processes[0].poll() is not None:
                raise RuntimeError('isolated Redis exited')
            try:
                if client.ping(): break
            except redis.RedisError:
                time.sleep(.1)
        else: raise RuntimeError('isolated Redis did not start')
        for selected in (api_port, ws_port):
            process = subprocess.Popen([sys.executable, '-m', 'daphne', '-b', '127.0.0.1', '-p', str(selected), 'config.asgi:application'],
                cwd=settings.BASE_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
            processes.append(process)
            for _ in range(150):
                if process.poll() is not None: raise RuntimeError('isolated Daphne exited')
                try:
                    with socket.create_connection(('127.0.0.1', selected), timeout=.2): break
                except OSError: time.sleep(.1)
            else: raise RuntimeError('isolated Daphne did not start')
        yield f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{ws_port}'
    finally:
        if client is not None:
            try: client.shutdown(nosave=True)
            except redis.RedisError: pass
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ProviderVueTeamProgressHTTPTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        flow.ReleaseFlowTest.setUp(self)

    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge

    def test_actual_handoff(self):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root or not shutil.which('node'):
            self.skipTest('Requires current Vue build and Node')
        second = make_provider(display_name='第二陪玩')
        self.accounts[second.pk] = get_or_create_account_for_legacy_user(second)
        second.escort_profile.account = self.accounts[second.pk]
        second.escort_profile.save(update_fields=['account'])
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        self.ok(self.recharge())
        self.login(self.boss)
        order = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            'provider_ids': [self.accounts[self.provider.pk].pk, self.accounts[second.pk].pk],
            'client_request_id': 'vue-team-progress-real'}, format='json'))
        self.login(self.provider)
        self.ok(self.client.post(f"/api/orders/orders/{order['id']}/start/", {'entry_image': flow.screenshot()}, format='multipart'))
        tokens = {name: issue_account_tokens(self.accounts[user.pk])[0] for name, user in
                  (('boss', self.boss), ('staff', self.admin), ('first', self.provider), ('second', second))}
        repo = Path(settings.BASE_DIR).parent.parent
        def exercise(base, ws_base=None):
            result = subprocess.run(['node', str(repo / 'scripts/provider-vue-team-progress-375.cjs')],
                input=json.dumps({'root': root, 'base': base, 'wsBase': ws_base, 'realtime': bool(ws_base), 'orderId': order['id'], 'tokens': tokens,
                    'providers': [{'token': tokens['first'], 'id': self.accounts[self.provider.pk].pk},
                                  {'token': tokens['second'], 'id': self.accounts[second.pk].pk}],
                    'image': str(repo / 'apps/boss-miniapp/native/assets/icons/chat.png')}),
                text=True, encoding='utf-8', capture_output=True, timeout=60, cwd=repo)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        if os.environ.get('XA_TEAM_PROGRESS_REALTIME') == '1':
            with isolated_realtime() as (base, ws_base): exercise(base, ws_base)
        else:
            exercise(self.live_server_url)
