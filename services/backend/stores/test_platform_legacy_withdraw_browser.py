import json
import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.tests.factories import make_provider
from stores.context import resolve_store
from stores.models import PlatformAccount, PlatformRoleGrant
from stores.platform_auth import issue_tokens
from wallet.models import Transaction, WithdrawRequest
from wallet.services import get_wallet
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_LEGACY_WITHDRAW_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, PLATFORM_IDENTITY_ENABLED=True)
class PlatformLegacyWithdrawalBrowserTest(LiveServerTestCase):
    def test_finance_can_inspect_real_legacy_record_without_changing_money(self):
        store = resolve_store('shenglang')
        store.initialized_at = timezone.now()
        store.save(update_fields=['initialized_at'])
        finance = PlatformAccount(username='isolated-finance', phone='13900001111', is_phone_verified=True)
        finance.set_password('isolated-test-password'); finance.save()
        PlatformRoleGrant.objects.create(account=finance, role='FINANCE', scope_slug=store.slug)
        token, refresh = issue_tokens(finance, 'SMS', finance.phone)
        provider = make_provider()
        account = get_or_create_account_for_legacy_user(provider)
        wallet = get_wallet(account=account, user=provider)
        wallet.balance, wallet.frozen_amount = 80000, 20000
        wallet.save(update_fields=['balance', 'frozen_amount'])
        tx = Transaction.objects.create(wallet=wallet, tx_type='WITHDRAW', status='PENDING', amount=-20000,
                                        balance_before=100000, balance_after=80000)
        row = WithdrawRequest.objects.create(user=provider, account=account, amount=20000, tax_amount=1000,
            payee_method='WECHAT', payee_name='隔离历史申请', payee_account='qa-only',
            workflow_version=1, transaction=tx)
        session = {'token': token, 'refresh': refresh, 'sessionId': 'isolated-finance-test',
                   'profile': {'id': str(finance.pk), 'username': finance.username, 'identity_type': 'platform',
                               'grants': [{'role': 'FINANCE', 'scope_slug': store.slug}]}}
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            process = subprocess.run(['node', str(root / 'scripts/platform-legacy-withdraw-browser.cjs')], cwd=root,
                input=json.dumps({'api': api, 'session': session}), text=True, encoding='utf-8',
                capture_output=True, timeout=45)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        row.refresh_from_db(); wallet.refresh_from_db(); tx.refresh_from_db()
        self.assertEqual((row.workflow_version, row.status, wallet.balance, wallet.frozen_amount, tx.status),
                         (1, 'PENDING', 80000, 20000, 'PENDING'))
