"""A real spending-qualified boss check-in refreshes the open wallet."""
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
from orders.tests.factories import make_console_user, make_provider, make_service, make_service_category, make_user
from orders.tests.test_release_flow import screenshot
from users.models import CheckinRecord
from wallet.models import Transaction, Wallet
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_CHECKIN_WALLET_REALTIME') == '1', 'Requires isolated ASGI')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, REQUIRE_RECHARGE_PROOF=True)
class CheckinWalletRealtimeTest(LiveServerTestCase):
    def test_real_spending_then_checkin_updates_open_wallet(self):
        media = TemporaryDirectory(prefix='xa-checkin-live-'); self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss, provider, staff = make_user(), make_provider(), make_console_user(['wallet:recharge'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, staff)]
        for user, account in zip((boss, provider, staff), accounts): link_legacy_relations(user, account)
        provider.escort_profile.account = accounts[1]; provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = accounts[2]; staff.admin_membership.save(update_fields=['account'])
        clients = [APIClient() for _ in accounts]
        for client, account in zip(clients, accounts): client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        response = clients[2].post('/api/admin/wallets/recharge/', {'account_id':accounts[0].pk, 'amount':20000,
            'gift_amount':0, 'trade_no':'checkin-wallet-recharge', 'client_request_id':'checkin-wallet-recharge',
            'proof_image':screenshot()}, format='multipart')
        self.assertEqual(response.data['code'], 0, response.data)
        gift = make_service(name='隔离签到消费', price=18800, commission_rate=20, service_category=make_service_category(is_gift=True))
        response = clients[0].post('/api/orders/gifts/send/', {'gift_service_id':gift.pk, 'quantity':1,
            'provider_id':accounts[1].pk, 'expected_amount':18800, 'client_request_id':'checkin-wallet-gift'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        with single_asgi() as (base, ws_base):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/checkin-wallet-realtime.cjs')], cwd=root,
                input=json.dumps({'base':base, 'wsBase':ws_base, 'id':accounts[0].pk, 'token':issue_account_tokens(accounts[0])[0]}),
                text=True, encoding='utf-8', capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        record = CheckinRecord.objects.get(account=accounts[0])
        wallet = Wallet.objects.get(account=accounts[0])
        self.assertGreater(record.reward_amount, 0)
        self.assertEqual(wallet.balance, 1200 + record.reward_amount)
        self.assertEqual(wallet.transactions.filter(tx_type=Transaction.TxType.REWARD, amount=record.reward_amount).count(), 1)
