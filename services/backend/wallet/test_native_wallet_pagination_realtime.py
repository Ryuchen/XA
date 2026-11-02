"""Real recharge keeps the boss's expanded wallet history visible."""
import base64
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_user
from orders.tests.test_release_flow import screenshot
from wallet.models import Wallet


@skipUnless(os.environ.get('XA_WALLET_PAGINATION_REALTIME') == '1', 'Requires isolated ASGI and Redis')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, REQUIRE_RECHARGE_PROOF=True)
class NativeWalletPaginationRealtimeTest(LiveServerTestCase):
    def test_recharge_preserves_expanded_filtered_history(self):
        media = TemporaryDirectory(prefix='xa-wallet-pages-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss, staff = make_user(), make_console_user(['wallet:view', 'wallet:recharge'])
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        admin = get_or_create_account_for_legacy_user(staff)
        link_legacy_relations(staff, admin)
        staff.admin_membership.account = admin
        staff.admin_membership.save(update_fields=['account'])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(admin)[0])
        for number in range(21):
            response = client.post('/api/admin/wallets/recharge/', {'account_id': account.pk, 'amount': 100,
                'gift_amount': 0, 'trade_no': f'wallet-page-{number}', 'client_request_id': f'wallet-page-{number}',
                'proof_image': screenshot()}, format='multipart')
            self.assertEqual(response.data['code'], 0, response.data)
        from orders.tests.test_provider_vue_team_progress_http import isolated_realtime
        with isolated_realtime() as (base, ws_base):
            repo = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(repo / 'scripts/native-wallet-pagination-realtime.cjs')], cwd=repo,
                input=json.dumps({'base': base, 'wsBase': ws_base, 'bossId': account.pk,
                    'bossToken': issue_account_tokens(account)[0], 'staffToken': issue_account_tokens(admin)[0],
                    'image': base64.b64encode(screenshot().read()).decode('ascii')}),
                text=True, encoding='utf-8', capture_output=True, timeout=40)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        wallet = Wallet.objects.get(account=account)
        self.assertEqual(wallet.balance, 2300)
        self.assertEqual(wallet.transactions.filter(tx_type='TOPUP').count(), 23)
        boss_client = APIClient()
        boss_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        history = boss_client.get('/api/wallet/transactions/?page=2&page_size=20&tx_type=TOPUP')
        self.assertEqual(history.data['code'], 0, history.data)
        self.assertEqual((history.data['data']['balance'], history.data['data']['total']), (2300, 23))
        self.assertEqual(len(history.data['data']['transactions']), 3)
