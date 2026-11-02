"""Live ASGI updates the open workbench and filtered claim queue."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.test import override_settings
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import GiftBenefitRule, RewardItem
from orders.models import ServiceCategory
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from orders.tests.test_release_flow import screenshot


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ClaimQueueBrowserLiveTest(APITransactionTestCase):
    def test_gift_and_delivery_update_open_staff_workflow(self):
        database = os.environ.get('XA_ASGI_NETWORK_TEST_DB')
        if not shutil.which('node') or not database or connection.settings_dict['NAME'] != database:
            self.skipTest('Requires isolated file database and ASGI')
        boss, provider = make_user(), make_provider(is_verified=True, deposit_mode='CASH')
        staff = make_console_user(['wallet:recharge', 'dashboard:view', 'engagement:fulfill'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, staff)]
        provider.escort_profile.account = accounts[1]
        provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = accounts[2]
        staff.admin_membership.save(update_fields=['account'])
        category = ServiceCategory.objects.create(name='实时队列礼物', is_gift=True)
        gift = make_service(name='实时队列礼物', price=2100, service_category=category)
        reward = RewardItem.objects.create(code='claim-live-browser', name='实时队列权益', kind='SERVICE', in_shop=False)
        GiftBenefitRule.objects.create(name='实时队列规则', reward=reward, category=category,
            threshold=2100, inclusive=True, enabled=True)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[2])[0])
        result = client.post('/api/admin/wallets/recharge/', {'account_id': accounts[0].pk, 'amount': 10000,
            'gift_amount': 0, 'trade_no': 'claim-live-browser-recharge', 'proof_image': screenshot(),
            'client_request_id': 'claim-live-browser-recharge'}, format='multipart')
        self.assertEqual(result.data['code'], 0, result.data)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        temp = tempfile.TemporaryDirectory(prefix='xa-claim-live-')
        self.addCleanup(temp.cleanup)
        Path(temp.name, 'claim_asgi_settings.py').write_text(
            'from config.asgi_network_test_settings import *\nimport os\nMEDIA_ROOT = os.environ["XA_CLAIM_MEDIA"]\n', encoding='utf-8')
        env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'claim_asgi_settings', 'PYTHONPATH': temp.name, 'XA_CLAIM_MEDIA': temp.name}
        server = subprocess.Popen([sys.executable, '-m', 'daphne', '-b', '127.0.0.1', '-p', str(port), 'config.asgi:application'],
            cwd=settings.BASE_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            for _ in range(150):
                if server.poll() is not None:
                    self.fail('isolated ASGI exited')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2):
                        break
                except OSError:
                    time.sleep(.1)
            else:
                self.fail('isolated ASGI did not start')
            repo = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(repo / 'scripts/admin-claim-queue-realtime.cjs')], cwd=repo, env=env,
                input=json.dumps({'api': f'http://127.0.0.1:{port}', 'bossToken': issue_account_tokens(accounts[0])[0],
                    'staffToken': issue_account_tokens(accounts[2])[0], 'providerId': accounts[1].pk, 'giftId': gift.pk}),
                text=True, encoding='utf-8', capture_output=True, timeout=100)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill(); server.wait(timeout=10)
