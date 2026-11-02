"""Real team applications refresh an open mobile admin order detail."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from orders.tests.test_release_flow import screenshot
from wallet.models import SystemConfig


@skipUnless(os.environ.get('XA_ADMIN_APPLICATION_REALTIME') == '1', 'Requires isolated ASGI, Redis and browser')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, REQUIRE_RECHARGE_PROOF=True)
class AdminApplicationRealtimeTest(TransactionTestCase):
    def test_team_application_refreshes_open_drawer(self):
        self.assertEqual(connection.settings_dict['NAME'], os.environ.get('XA_ASGI_NETWORK_TEST_DB'))
        with tempfile.TemporaryDirectory(prefix='xa-application-live-') as media, override_settings(MEDIA_ROOT=media):
            boss = make_user()
            providers = [make_provider(display_name=name, deposit_mode='EARNINGS', pass_tier='BLACK',
                         pass_expires_at=timezone.now()+timedelta(days=1)) for name in ('报名陪玩', '确认队友')]
            staff = make_console_user(['order:view', 'wallet:recharge'])
            users = [boss, *providers, staff]
            accounts = [get_or_create_account_for_legacy_user(user) for user in users]
            for user, account in zip(users, accounts):
                link_legacy_relations(user, account)
            staff.admin_membership.account = accounts[3]
            staff.admin_membership.save(update_fields=['account'])
            service = make_service(price=10000, billing_provider_count=2)
            for provider, account in zip(providers, accounts[1:3]):
                provider.escort_profile.account = account
                provider.escort_profile.save(update_fields=['account'])
                provider.escort_profile.service_items.add(service)
            SystemConfig.objects.update_or_create(key='provider_pass_delay_seconds_BLACK', defaults={'value': '0'})
            tokens = [issue_account_tokens(account)[0] for account in accounts]
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[3])
            recharge = client.post('/api/admin/wallets/recharge/', {'account_id': accounts[0].pk, 'amount': 50000,
                'gift_amount': 0, 'trade_no': 'application-live-recharge', 'proof_image': screenshot(),
                'client_request_id': 'application-live-recharge'}, format='multipart').data
            self.assertEqual(recharge['code'], 0, recharge)
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
            result = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC',
                'dispatch_mode': 'BOSS_SELECT', 'client_request_id': 'application-live-order'}, format='json').data
            self.assertEqual(result['code'], 0, result)
            order = Order.objects.get(pk=result['data']['id'])
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
            env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.team_progress_realtime_test_settings'}
            process = subprocess.Popen([sys.executable, '-m', 'daphne', '-b', '127.0.0.1', '-p', str(port), 'config.asgi:application'],
                cwd=settings.BASE_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                for _ in range(150):
                    if process.poll() is not None: self.fail('isolated ASGI exited')
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=.2): break
                    except OSError: time.sleep(.1)
                else: self.fail('isolated ASGI unavailable')
                repo = Path(settings.BASE_DIR).parent.parent
                result = subprocess.run(['node', str(repo / 'scripts/admin-application-realtime-375.cjs')],
                    cwd=repo, input=json.dumps({'api': f'http://127.0.0.1:{port}', 'orderId': order.pk, 'orderNo': order.order_no,
                        'bossToken': tokens[0], 'providerToken': tokens[1], 'partnerToken': tokens[2],
                        'partnerId': accounts[2].pk, 'staffToken': tokens[3]}),
                    text=True, encoding='utf-8', capture_output=True, timeout=75)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                order.refresh_from_db()
                self.assertEqual(order.status, 'GRABBED')
                self.assertEqual(set(order.providers.values_list('provider_account_id', flat=True)), {accounts[1].pk, accounts[2].pk})
            finally:
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
