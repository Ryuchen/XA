"""Balance lottery awards refresh the boss wallet; shop redemption stays blocked."""
import json
import os
from pathlib import Path
import subprocess
from unittest import skipUnless
from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from engagement.models import LotteryCampaign, LotteryPrize, MemberProfile, RewardClaim, RewardItem
from orders.tests.factories import make_provider, make_user
from wallet.models import Transaction, Wallet
from wallet.services import get_wallet
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_REWARD_WALLET_REALTIME') == '1', 'Requires isolated ASGI')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class BalanceRewardWalletRealtimeTest(LiveServerTestCase):
    def test_shop_redemption_is_blocked_and_lottery_updates_boss_wallet(self):
        boss, provider = make_user(), make_provider()
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, provider)]
        for user, account in zip((boss, provider), accounts):
            link_legacy_relations(user, account)
            MemberProfile.objects.create(account=account, points_balance=5000)
            get_wallet(account=account)
        provider.escort_profile.account = accounts[1]
        provider.escort_profile.save(update_fields=['account'])
        reward = RewardItem.objects.create(code='wallet-live-reward', name='隔离余额权益', kind='BALANCE',
            balance_amount=250, point_cost=10, audience='ALL', stock=2, in_shop=True, is_active=True)
        campaign = LotteryCampaign.objects.create(name='隔离余额抽奖', point_cost=10, enabled=True)
        LotteryPrize.objects.create(campaign=campaign, reward=reward, probability=10000)
        with single_asgi() as (base, ws_base):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/balance-reward-wallet-realtime.cjs')], cwd=root,
                input=json.dumps({'base':base, 'wsBase':ws_base, 'rewardId':reward.pk,
                    'campaignId':campaign.pk, 'version':campaign.version,
                    'accounts':[{'id':account.pk, 'token':issue_account_tokens(account)[0]} for account in accounts]}),
                text=True, encoding='utf-8', capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for index, account in enumerate(accounts):
            wallet = Wallet.objects.get(account=account)
            self.assertEqual(wallet.balance, 250 if index == 0 else 0)
            self.assertEqual(wallet.transactions.filter(tx_type=Transaction.TxType.GIFT, amount=250).count(), int(index == 0))
            self.assertEqual(MemberProfile.objects.get(account=account).points_balance, 4000 if index == 0 else 5000)
            self.assertEqual(RewardClaim.objects.filter(account=account, reward=reward, status='COMPLETED', source='LOTTERY').count(), int(index == 0))
