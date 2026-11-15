"""Real provider deposit and staff refund invalidate the open wallet."""
import json
import os
from pathlib import Path
import subprocess
import socket
import sys
import time
from contextlib import contextmanager
from tempfile import TemporaryDirectory
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider
from wallet.models import Wallet, Transaction
from wallet.services import get_wallet


@contextmanager
def single_asgi():
    """Fallback when local Redis is unavailable: real HTTP and socket in one ASGI process."""
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen([sys.executable, '-m', 'daphne', '-b', '127.0.0.1', '-p', str(port), 'config.asgi:application'],
        cwd=settings.BASE_DIR, env={**os.environ, 'DJANGO_SETTINGS_MODULE':'config.asgi_network_test_settings'},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        for _ in range(150):
            if process.poll() is not None:
                raise RuntimeError('isolated ASGI exited')
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.2): break
            except OSError: time.sleep(.1)
        else: raise RuntimeError('isolated ASGI did not start')
        yield f'http://127.0.0.1:{port}', f'http://127.0.0.1:{port}'
    finally:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)


@skipUnless(os.environ.get('XA_DEPOSIT_REALTIME') == '1', 'Requires isolated ASGI and Redis')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, REQUIRE_RECHARGE_PROOF=True)
class DepositRealtimeWorkflowTest(LiveServerTestCase):
    def test_deposit_and_admin_refund_update_provider_wallet(self):
        media = TemporaryDirectory(prefix='xa-deposit-live-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        provider = make_provider(deposit_mode='CASH', deposit_required=3000, deposit_paid=0)
        staff = make_console_user(['wallet:adjust', 'escort:view', 'escort:deposit_refund'])
        account = get_or_create_account_for_legacy_user(provider)
        link_legacy_relations(provider, account)
        provider.escort_profile.account = account
        provider.escort_profile.save(update_fields=['account'])
        admin = get_or_create_account_for_legacy_user(staff)
        link_legacy_relations(staff, admin)
        staff.admin_membership.account = admin
        staff.admin_membership.save(update_fields=['account'])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(admin)[0])
        wallet = get_wallet(account=account)
        result = client.post(f'/api/admin/wallets/{wallet.pk}/adjust/', {'amount': 5000,
            'remark': '核对后调账', 'client_request_id': 'deposit-live-adjust'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        from orders.tests.test_provider_vue_team_progress_http import isolated_realtime
        backend = single_asgi if os.environ.get('XA_DEPOSIT_SINGLE_ASGI') == '1' else isolated_realtime
        with backend() as (base, ws_base):
            repo = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(repo / 'scripts/deposit-realtime-workflow.cjs')], cwd=repo,
                input=json.dumps({'base': base, 'wsBase': ws_base, 'providerId': account.pk,
                    'profileId': provider.escort_profile.pk, 'providerToken': issue_account_tokens(account)[0],
                    'staffToken': issue_account_tokens(admin)[0]}),
                text=True, encoding='utf-8', capture_output=True, timeout=35)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        wallet = Wallet.objects.get(account=account)
        provider.escort_profile.refresh_from_db()
        self.assertEqual((wallet.balance, provider.escort_profile.deposit_paid), (4000, 1000))
        self.assertEqual(list(wallet.transactions.filter(tx_type=Transaction.TxType.DEPOSIT).order_by('id').values_list('amount', flat=True)), [-2000, 1000])
