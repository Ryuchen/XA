"""Provider wallet labels use actual business transactions, not sample rows."""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import override_settings
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from engagement.models import MemberProfile, RewardItem
from orders.tests.factories import make_console_user, make_provider, make_service, make_service_category, make_user
from orders.tests.test_release_flow import screenshot
from wallet.models import Transaction
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class GiftSourceWorkflowTest(APITestCase):
    def test_gift_order_and_staff_recharge_are_distinct_in_provider_wallet(self):
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss, provider = make_user(), make_provider(display_name='隔离收礼陪玩')
        staff = make_console_user(['wallet:recharge', 'order:view', 'order:refund'])
        accounts = []
        for user in (boss, provider, staff):
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            accounts.append(account)
        provider.escort_profile.account = accounts[1]
        provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = accounts[2]
        staff.admin_membership.save(update_fields=['account'])
        wallet = get_wallet(account=accounts[0], user=boss)
        wallet.balance = 10000
        wallet.save(update_fields=['balance'])
        gift = make_service(name='隔离礼物', price=2100, service_category=make_service_category(is_gift=True))

        def login(account):
            self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])

        login(accounts[0])
        sent = self.client.post('/api/orders/gifts/send/', {'gift_service_id': gift.pk, 'provider_id': accounts[1].pk,
            'quantity': 1, 'expected_amount': 2100, 'client_request_id': 'wallet-gift-source-order'}, format='json')
        self.assertEqual(sent.data['code'], 0, sent.data)
        login(accounts[2])
        recharge = self.client.post('/api/admin/wallets/recharge/', {'account_id': accounts[1].pk, 'amount': 1000,
            'gift_amount': 500, 'trade_no': 'wallet-gift-source-receipt', 'proof_image': screenshot(),
            'client_request_id': 'wallet-gift-source-recharge'}, format='multipart')
        self.assertEqual(recharge.data['code'], 0, recharge.data)
        login(accounts[1])
        MemberProfile.objects.update_or_create(account=accounts[1], defaults={'points_balance': 1000})
        reward = RewardItem.objects.create(code='isolated-wallet-source', name='隔离积分兑换',
            kind='BALANCE', audience='PROVIDER', point_cost=1, balance_amount=300, is_active=True)
        redeemed = self.client.post('/api/club/redeem/', {'reward_id': reward.pk,
            'client_request_id': 'wallet-gift-source-redeem'}, format='json')
        self.assertEqual(redeemed.data['code'], 0, redeemed.data)
        response = self.client.get('/api/wallet/transactions/', {'page_size': 50})
        self.assertEqual(response.data['code'], 0, response.data)
        data = response.data['data']
        rows = [row for row in data['transactions'] if row['tx_type'] == 'GIFT']
        self.assertEqual(len(rows), 3)
        original = Transaction.objects.get(wallet__account=accounts[1], order__is_gift_snapshot=True, tx_type='GIFT')
        self.assertEqual(original.amount, 1680)
        self.assertEqual(next(row for row in rows if row['id'] == original.pk)['gift_source'], 'ORDER_GIFT')
        self.assertEqual(next(row for row in rows if row['amount'] == 500)['gift_source'], 'RECHARGE_GIFT')
        self.assertIsNone(next(row for row in rows if row['amount'] == 300)['gift_source'])

        login(accounts[2])
        preview = self.client.get(f'/api/admin/orders/{original.order_id}/refund-preview/')
        self.assertEqual(preview.data['code'], 0, preview.data)
        refunded = self.client.post(f'/api/admin/orders/{original.order_id}/refund/', {
            'reason': '隔离验收礼物退款', 'plan_token': preview.data['data']['plan_token']}, format='json')
        self.assertEqual(refunded.data['code'], 0, refunded.data)
        login(accounts[1])
        data = self.client.get('/api/wallet/transactions/', {'page_size': 50}).data['data']
        reverse = next(row for row in data['transactions'] if row['reversal_of'] == original.pk)
        self.assertEqual(reverse['gift_source'], 'ORDER_GIFT')

        node = shutil.which('node')
        if node:
            repo = Path(settings.BASE_DIR).parent.parent
            env = {**os.environ, 'XA_REAL_GIFT_WALLET_JSON': json.dumps(data, ensure_ascii=False)}
            result = subprocess.run([node, '--test', '--test-name-pattern', 'renders actual gift source wallet response',
                str(repo / 'apps/provider-miniapp/tests/wallet-realtime.cjs')], cwd=repo, env=env,
                text=True, encoding='utf-8', capture_output=True, timeout=45)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
