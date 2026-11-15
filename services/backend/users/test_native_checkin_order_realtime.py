"""An open native check-in page follows a real boss gift purchase."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from tempfile import TemporaryDirectory
from unittest import skipUnless
from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider, make_service, make_service_category, make_user
from orders.tests.test_release_flow import screenshot
from users.models import CheckinGift, CheckinRecord
from wallet.models import Wallet
from wallet.test_deposit_realtime_workflow import single_asgi


@contextmanager
def isolated_redis_asgi(*, include_env=False):
    """Two real ASGI processes share a disposable, loopback-only Redis container."""
    import redis
    ports = set()
    while len(ports) < 3:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0)); ports.add(probe.getsockname()[1])
    redis_port, api_port, ws_port = ports
    password, prefix = secrets.token_hex(16), 'xa_asgi_test_' + secrets.token_hex(8)
    name = 'xa-checkin-test-' + secrets.token_hex(6)
    docker = ['wsl.exe', '-d', 'XA-Ubuntu-24.04', '-u', 'root', '--', 'docker']
    env = {**os.environ, 'DJANGO_SETTINGS_MODULE':'config.asgi_redis_network_test_settings',
        'XA_ASGI_TEST_REDIS_URL':f'redis://:{password}@127.0.0.1:{redis_port}/0',
        'XA_ASGI_TEST_REDIS_PREFIX':prefix, 'XA_ASGI_TEST_MEDIA_ROOT':str(settings.MEDIA_ROOT),
        'KOOK_ENABLED':'false', 'WECOM_DISPATCH_ENABLED':'false'}
    processes = []
    client = redis.Redis(host='127.0.0.1', port=redis_port, password=password, socket_timeout=.2)
    try:
        subprocess.run(docker + ['run', '-d', '--rm', '--name', name, '-p', f'127.0.0.1:{redis_port}:6379',
            'redis:alpine', 'redis-server', '--save', '', '--appendonly', 'no', '--requirepass', password],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(150):
            try:
                if client.ping(): break
            except redis.RedisError: time.sleep(.1)
        else: raise RuntimeError('isolated Redis did not start')
        for port in (api_port, ws_port):
            process = subprocess.Popen([sys.executable, '-m', 'daphne', '-b', '127.0.0.1', '-p', str(port), 'config.asgi:application'],
                cwd=settings.BASE_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            processes.append(process)
            for _ in range(150):
                if process.poll() is not None: raise RuntimeError('isolated ASGI exited')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2): break
                except OSError: time.sleep(.1)
            else: raise RuntimeError('isolated ASGI did not start')
        values = (f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{ws_port}')
        yield (*values, env) if include_env else values
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
        client.close()
        subprocess.run(docker + ['stop', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@skipUnless(os.environ.get('XA_CHECKIN_ORDER_REALTIME') == '1', 'Requires isolated ASGI')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, REQUIRE_RECHARGE_PROOF=True)
class NativeCheckinOrderRealtimeTest(LiveServerTestCase):
    def test_open_checkin_follows_purchase_and_claims_reward(self):
        media = TemporaryDirectory(prefix='xa-native-checkin-'); self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss, provider, staff, other = make_user(), make_provider(), make_console_user(['wallet:recharge', 'order:view', 'order:refund', 'checkin:view', 'checkin:edit']), make_user()
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, staff, other)]
        for user, account in zip((boss, provider, staff, other), accounts): link_legacy_relations(user, account)
        provider.escort_profile.account = accounts[1]; provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = accounts[2]; staff.admin_membership.save(update_fields=['account'])
        admin = APIClient(); admin.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[2])[0])
        response = admin.post('/api/admin/wallets/recharge/', {'account_id':accounts[0].pk, 'amount':20000,
            'gift_amount':0, 'trade_no':'native-checkin-recharge', 'client_request_id':'native-checkin-recharge',
            'proof_image':screenshot()}, format='multipart')
        self.assertEqual(response.data['code'], 0, response.data)
        gift = make_service(name='隔离签到消费', price=18800, commission_rate=20, service_category=make_service_category(is_gift=True))
        response = admin.post('/api/admin/wallets/recharge/', {'account_id':accounts[3].pk, 'amount':20000,
            'gift_amount':0, 'trade_no':'native-checkin-other-recharge', 'client_request_id':'native-checkin-other-recharge',
            'proof_image':screenshot()}, format='multipart')
        self.assertEqual(response.data['code'], 0, response.data)
        other_client = APIClient(); other_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[3])[0])
        response = other_client.post('/api/orders/gifts/send/', {'gift_service_id':gift.pk, 'quantity':1,
            'provider_id':accounts[1].pk, 'expected_amount':18800, 'client_request_id':'native-checkin-other-gift'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        # LiveServerTestCase flushes rows between classes, while the migration
        # history remains applied. Configure this scenario's first reward
        # explicitly rather than relying on a migration seed surviving flush.
        first_gift, _ = CheckinGift.objects.update_or_create(checkin_day=1,
            defaults={'name': '隔离首日签到礼', 'reward_amount': 0, 'is_active': True})
        backend = isolated_redis_asgi if os.environ.get('XA_CHECKIN_REDIS_REALTIME') == '1' else single_asgi
        with backend() as (base, ws_base):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/native-checkin-order-realtime.cjs')], cwd=root,
                input=json.dumps({'base':base, 'wsBase':ws_base, 'bossId':accounts[0].pk,
                    'token':issue_account_tokens(accounts[0])[0], 'staffToken':issue_account_tokens(accounts[2])[0],
                    'providerId':accounts[1].pk, 'serviceId':gift.pk, 'giftConfigId':first_gift.pk,
                    'otherId':accounts[3].pk, 'otherToken':issue_account_tokens(accounts[3])[0]}),
                text=True, encoding='utf-8', capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        records = list(CheckinRecord.objects.filter(account=accounts[0]).order_by('seq_in_month'))
        self.assertEqual(len(records), 2 if timezone.localdate().day > 1 else 1)
        self.assertEqual(records[0].reward_amount, 50)
        self.assertEqual(sum(record.is_makeup for record in records), 1 if timezone.localdate().day > 1 else 0)
        self.assertEqual(Wallet.objects.get(account=accounts[0]).balance, 1200 + sum(record.reward_amount for record in records))
        self.assertFalse(CheckinRecord.objects.filter(account=accounts[3]).exists())
