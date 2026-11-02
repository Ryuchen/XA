"""A provider's open wallet follows real staff reward and penalty writes."""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.test import override_settings
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider, make_service, make_service_category, make_user, make_wallet
from wallet.models import DisposeRecord, Transaction


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ProviderRewardLiveBrowserTest(APITransactionTestCase):
    def test_open_wallet_updates_reward_penalty_and_transactions(self):
        self.run_wallet_flow(gift=False)

    def test_open_wallet_updates_when_boss_sends_and_staff_refunds_gift(self):
        self.run_wallet_flow(gift=True)

    def run_wallet_flow(self, *, gift):
        node, database, root = shutil.which('node'), os.environ.get('XA_ASGI_NETWORK_TEST_DB'), os.environ.get('XA_PROVIDER_CHAT_H5_ROOT')
        if not node or not database or connection.settings_dict['NAME'] != database or not root:
            self.skipTest('Requires an isolated file database, ASGI and provider H5 build')
        provider, staff = make_provider(), make_console_user(['escort:dispose', 'order:view', 'order:refund'])
        account, staff_account = [get_or_create_account_for_legacy_user(user) for user in (provider, staff)]
        provider.escort_profile.account = account
        provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        wallet = make_wallet(provider, balance=0)
        wallet.account = account
        wallet.save(update_fields=['account'])
        gift_input = None
        if gift:
            boss = make_user()
            boss_account = get_or_create_account_for_legacy_user(boss)
            boss_wallet = make_wallet(boss, balance=50000)
            boss_wallet.account = boss_account
            boss_wallet.save(update_fields=['account'])
            service = make_service(name='隔离实时礼物', price=10000, commission_rate=20,
                                   service_category=make_service_category(is_gift=True))
            gift_input = {'bossToken': issue_account_tokens(boss_account)[0], 'serviceId': service.pk}
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.asgi_network_test_settings', 'KOOK_ENABLED': 'false', 'WECOM_DISPATCH_ENABLED': 'false'}
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
                self.fail('isolated ASGI did not start')
            repo = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run([node, str(repo / 'scripts/provider-reward-realtime-375-live.cjs')],
                input=json.dumps({'api': f'http://127.0.0.1:{port}', 'root': root,
                    'provider': {'token': issue_account_tokens(account)[0], 'id': str(account.pk)},
                    'staffToken': issue_account_tokens(staff_account)[0], 'profileId': provider.escort_profile.pk,
                    'gift': gift_input}),
                cwd=repo, text=True, encoding='utf-8', capture_output=True, timeout=70, env=env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            wallet.refresh_from_db()
            if gift:
                boss_wallet.refresh_from_db()
                self.assertEqual((wallet.balance, boss_wallet.balance), (0, 50000))
                self.assertEqual(list(Transaction.objects.filter(wallet=wallet).order_by('amount').values_list('amount', flat=True)), [-8000, 8000])
            else:
                self.assertEqual(wallet.balance, 2000)
                self.assertEqual(DisposeRecord.objects.filter(account=account).count(), 2)
                self.assertEqual(list(Transaction.objects.filter(wallet=wallet).order_by('amount').values_list('amount', flat=True)), [-1000, 3000])
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=10)
