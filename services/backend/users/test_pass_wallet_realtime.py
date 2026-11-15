"""Provider pass purchase updates other open views of the same wallet."""
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
from orders.tests.factories import make_console_user, make_provider
from users.models import ProviderPassPurchase
from wallet.models import Transaction, Wallet
from wallet.services import get_wallet
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_PASS_WALLET_REALTIME') == '1', 'Requires isolated ASGI')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, REQUIRE_RECHARGE_PROOF=True)
class PassWalletRealtimeTest(LiveServerTestCase):
    def test_adjust_then_purchase_notifies_wallet(self):
        media = TemporaryDirectory(prefix='xa-pass-live-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        provider, staff = make_provider(), make_console_user(['wallet:adjust'])
        account, admin = [get_or_create_account_for_legacy_user(user) for user in (provider, staff)]
        link_legacy_relations(provider, account); link_legacy_relations(staff, admin)
        provider.escort_profile.account = account; provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = admin; staff.admin_membership.save(update_fields=['account'])
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(admin)[0])
        wallet = get_wallet(account=account)
        response = client.post(f'/api/admin/wallets/{wallet.pk}/adjust/', {'amount': 100000,
            'remark': '核对后调账', 'client_request_id': 'pass-wallet-adjust'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        with single_asgi() as (base, ws_base):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/pass-wallet-realtime.cjs')], cwd=root,
                input=json.dumps({'base':base, 'wsBase':ws_base, 'id':account.pk, 'token':issue_account_tokens(account)[0]}),
                text=True, encoding='utf-8', capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        purchase = ProviderPassPurchase.objects.get(account=account)
        wallet = Wallet.objects.get(account=account)
        self.assertEqual(wallet.balance, 100000 - purchase.paid_amount)
        self.assertEqual(wallet.transactions.filter(tx_type=Transaction.TxType.PASS_PURCHASE).count(), 1)
