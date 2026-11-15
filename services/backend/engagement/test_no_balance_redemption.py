"""商城不能把积分直兑余额，抽奖余额和已发生历史交易仍保持可追溯。"""
from django.test import override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient, APITestCase

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import (LotteryCampaign, LotteryDraw, LotteryPrize, MemberProfile,
    PointFundingLot, PointLedger, RewardClaim, RewardItem)
from engagement.services import create_claim, fingerprint
from orders.tests.factories import make_console_user
from wallet.models import Transaction
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class NoBalanceRedemptionTest(APITestCase):
    def setUp(self):
        self.boss = ClubAccount.objects.create_account(username='reward-test-boss', account_type='BOSS')
        self.provider = ClubAccount.objects.create_account(username='reward-test-provider', account_type='PROVIDER')
        staff = make_console_user(['engagement:view', 'engagement:edit'])
        self.staff = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = self.staff
        staff.admin_membership.save(update_fields=['account'])
        self.clients = []
        for account in (self.boss, self.provider, self.staff):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            self.clients.append(client)
        for account in (self.boss, self.provider):
            MemberProfile.objects.create(account=account, points_balance=10000)
        self.balance_reward = RewardItem.objects.create(code='legacy-balance-reward', name='历史余额',
            kind='BALANCE', audience='ALL', in_shop=True, is_active=True, point_cost=10,
            balance_amount=500, stock=5)
        self.goods = RewardItem.objects.create(code='current-goods', name='普通实物', kind='PHYSICAL',
            audience='ALL', in_shop=True, is_active=True, point_cost=10, stock=5)

    def test_both_member_roles_cannot_see_or_redeem_legacy_balance_item(self):
        for client, account in zip(self.clients[:2], (self.boss, self.provider)):
            with self.subTest(role=account.account_type):
                wallet = get_wallet(account=account)
                visible = client.get('/api/club/rewards/').data['data']
                self.assertEqual([row['id'] for row in visible], [self.goods.pk])
                response = client.post('/api/club/redeem/', {'reward_id': self.balance_reward.pk,
                    'client_request_id': 'blocked-balance-' + account.account_type}, format='json')
                self.assertEqual((response.status_code, response.data['errorCode']), (400, 'REWARD_UNAVAILABLE'))
                self.assertEqual(MemberProfile.objects.get(account=account).points_balance, 10000)
                wallet.refresh_from_db()
                self.assertEqual((wallet.balance, wallet.total_gift), (0, 0))
                self.assertFalse(PointLedger.objects.filter(account=account).exists())
                self.assertFalse(Transaction.objects.filter(wallet=wallet).exists())
                self.assertFalse(RewardClaim.objects.filter(account=account).exists())
                success = client.post('/api/club/redeem/', {'reward_id': self.goods.pk,
                    'client_request_id': 'goods-' + account.account_type}, format='json')
                self.assertEqual(success.data['code'], 0, success.data)
        self.balance_reward.refresh_from_db()
        self.assertEqual((self.balance_reward.stock, self.balance_reward.redeemed_count), (5, 0))

    def test_existing_balance_redemption_replay_returns_original_record(self):
        request_id = 'historical-balance-request'
        wallet = get_wallet(account=self.boss)
        wallet.balance = 500
        wallet.total_gift = 500
        wallet.save(update_fields=['balance', 'total_gift'])
        key = f'redeem:{self.boss.pk}:{self.boss.business_cycle}:{request_id}'
        old = RewardClaim.objects.create(account=self.boss, reward=self.balance_reward, event_key=key,
            request_fingerprint=fingerprint({'reward_id': self.balance_reward.pk, 'details': {}}),
            source='REDEMPTION', name_snapshot='历史余额', kind_snapshot='BALANCE',
            status='COMPLETED', points_spent=10, balance_amount=500)
        payload = {'reward_id': self.balance_reward.pk, 'client_request_id': request_id}
        response = self.clients[0].post('/api/club/redeem/', payload, format='json')
        self.assertEqual(response.data['data']['id'], old.pk)
        self.assertEqual(self.clients[0].get(f'/api/club/claims/{old.pk}/').data['data']['id'], old.pk)
        wallet.refresh_from_db()
        self.assertEqual((wallet.balance, wallet.total_gift), (500, 500))
        self.assertEqual(RewardClaim.objects.filter(account=self.boss).count(), 1)
        self.assertFalse(PointLedger.objects.filter(account=self.boss).exists())

    def test_balance_lottery_still_awards_once_and_keeps_gift_source(self):
        campaign = LotteryCampaign.objects.create(name='余额奖品测试', point_cost=10, enabled=True)
        LotteryPrize.objects.create(campaign=campaign, reward=self.balance_reward, probability=10000)
        payload = {'campaign_id': campaign.pk, 'version': campaign.version,
                   'client_request_id': 'balance-lottery-test', 'count': 1}
        first = self.clients[0].post('/api/club/lottery/', payload, format='json')
        self.assertEqual(first.data['code'], 0, first.data)
        replay = self.clients[0].post('/api/club/lottery/', payload, format='json')
        self.assertEqual(first.data['data']['id'], replay.data['data']['id'])
        draw = LotteryDraw.objects.get(account=self.boss)
        self.assertEqual(draw.claim.source, 'LOTTERY')
        wallet = get_wallet(account=self.boss)
        self.assertEqual((wallet.balance, wallet.total_gift), (500, 500))
        tx = Transaction.objects.get(wallet=wallet, tx_type='GIFT')
        self.assertIn('积分抽奖', tx.remark)
        lot = PointFundingLot.objects.get(account=self.boss, source_key=f'reward-claim:{draw.claim_id}')
        self.assertEqual((lot.kind, lot.remaining), ('GIFT', 500))
        self.assertEqual(MemberProfile.objects.get(account=self.boss).points_balance, 9000)
        self.assertEqual(PointLedger.objects.filter(account=self.boss, kind='LOTTERY_COST').count(), 1)

    def test_admin_can_create_lottery_only_balance_but_not_publish_to_shop(self):
        admin = self.clients[2]
        payload = {'code': 'new-balance', 'name': '抽奖余额', 'kind': 'BALANCE', 'audience': 'BOSS',
                   'balance_amount': 500, 'point_cost': 10, 'is_active': True, 'in_shop': True}
        bad = admin.post('/api/admin/club-rewards/', payload, format='json')
        self.assertEqual(bad.status_code, 400)
        self.assertFalse(RewardItem.objects.filter(code='new-balance').exists())
        good = admin.post('/api/admin/club-rewards/', {**payload, 'in_shop': False}, format='json')
        self.assertEqual(good.data['code'], 0, good.data)
        row = RewardItem.objects.get(code='new-balance')
        self.assertEqual(admin.patch(f'/api/admin/club-rewards/{row.pk}/', {'in_shop': True}, format='json').status_code, 400)
        self.assertEqual(admin.patch(f'/api/admin/club-rewards/{self.balance_reward.pk}/', {'in_shop': False}, format='json').data['code'], 0)

    def test_shared_claim_creation_also_rejects_redemption_before_mutation(self):
        with self.assertRaises(ValidationError):
            create_claim(self.boss, self.balance_reward, 'direct-balance-denied', source='REDEMPTION')
        self.balance_reward.refresh_from_db()
        self.assertEqual((self.balance_reward.stock, self.balance_reward.redeemed_count), (5, 0))
        self.assertFalse(RewardClaim.objects.exists())
