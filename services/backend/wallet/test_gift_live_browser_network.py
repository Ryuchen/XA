"""Gift delivery refreshes open provider pages via real ASGI and Redis."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import ServiceCategory
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from orders.tests.test_release_flow import screenshot


@skipUnless(os.environ.get('XA_GIFT_BROWSER_REALTIME') == '1', 'Requires isolated ASGI, Redis and browser')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, REQUIRE_RECHARGE_PROOF=True)
class GiftBrowserRealtimeTest(TransactionTestCase):
    def test_gift_refreshes_open_wallet_and_inbox(self):
        self.assertEqual(connection.settings_dict['NAME'], os.environ['XA_ASGI_NETWORK_TEST_DB'])
        with tempfile.TemporaryDirectory(prefix='xa-gift-live-') as media, override_settings(MEDIA_ROOT=media):
            boss, provider = make_user(), make_provider(display_name='实时收礼陪玩', is_verified=True)
            staff = make_console_user(['wallet:recharge', 'order:view', 'order:refund'])
            dashboard_staff = make_console_user(['dashboard:view'])
            users = [boss, provider, staff, dashboard_staff]
            accounts = [get_or_create_account_for_legacy_user(user) for user in users]
            for user, account in zip(users, accounts):
                link_legacy_relations(user, account)
            provider.escort_profile.account = accounts[1]
            provider.escort_profile.save(update_fields=['account'])
            staff.admin_membership.account = accounts[2]
            staff.admin_membership.save(update_fields=['account'])
            dashboard_staff.admin_membership.account = accounts[3]
            dashboard_staff.admin_membership.save(update_fields=['account'])
            category = ServiceCategory.objects.create(name='实时礼物', is_gift=True)
            gift = make_service(name='隔离实时礼物', price=2100, commission_rate=20, service_category=category)
            tokens = [issue_account_tokens(account)[0] for account in accounts]
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[2])
            result = client.post('/api/admin/wallets/recharge/', {'account_id': accounts[0].pk,
                'amount': 10000, 'gift_amount': 0, 'trade_no': 'gift-live-recharge',
                'proof_image': screenshot(), 'client_request_id': 'gift-live-recharge'}, format='multipart').data
            self.assertEqual(result['code'], 0, result)
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.team_progress_realtime_test_settings'}
            process = subprocess.Popen([sys.executable, '-m', 'daphne', '-b', '127.0.0.1', '-p', str(port), 'config.asgi:application'],
                cwd=settings.BASE_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                for _ in range(150):
                    if process.poll() is not None:
                        self.fail('isolated ASGI exited')
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=.2):
                            break
                    except OSError:
                        time.sleep(.1)
                else:
                    self.fail('isolated ASGI unavailable')
                repo = Path(settings.BASE_DIR).parent.parent
                browser = subprocess.run(['node', str(repo / 'scripts/gift-realtime-browser-375.cjs')], cwd=repo,
                    input=json.dumps({'api': f'http://127.0.0.1:{port}', 'giftId': gift.pk, 'bossToken': tokens[0],
                        'staffToken': tokens[2], 'dashboardToken': tokens[3],
                        'provider': {'token': tokens[1], 'id': accounts[1].pk}, 'providerRoot': os.environ['XA_GIFT_PROVIDER_ROOT']}),
                    text=True, encoding='utf-8', capture_output=True, timeout=90)
                self.assertEqual(browser.returncode, 0, browser.stdout + browser.stderr)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
