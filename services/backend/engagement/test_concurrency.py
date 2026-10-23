from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import connections, close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from rest_framework.exceptions import ValidationError
from orders.tests.factories import make_user, make_wallet
from .tests import mapped
from .models import MemberProfile, RewardItem, RewardClaim, LotteryCampaign, LotteryPrize, PointLedger
from .services import redeem, draw


@skipUnlessDBFeature('has_select_for_update')
class EngagementConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.accounts=[]
        for _ in range(2):
            user=make_user(); account=mapped(user); make_wallet(user, balance=10000)
            self.accounts.append(account)
            MemberProfile.objects.create(account=account, points_balance=10000)

    def race(self, function):
        barrier=Barrier(2)
        def worker(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return function(index)
            except ValidationError:
                return None
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(worker, range(2)))

    def test_last_inventory_has_one_winner_and_loser_keeps_points(self):
        reward=RewardItem.objects.create(code='last-stock', name='最后一份', kind='PHYSICAL', point_cost=100, stock=1, is_active=True)
        results=self.race(lambda i:redeem(self.accounts[i], reward.pk, 'stock-request-'+str(i)).pk)
        self.assertEqual(sum(row is not None for row in results),1)
        reward.refresh_from_db();self.assertEqual(reward.stock,0)
        self.assertEqual(list(MemberProfile.objects.order_by('points_balance').values_list('points_balance',flat=True)),[0,10000])
        self.assertEqual(RewardClaim.objects.count(),1)

    def test_simultaneous_retry_returns_same_draw_once(self):
        campaign=LotteryCampaign.objects.create(name='抽奖并发',point_cost=100,enabled=True)
        reward=RewardItem.objects.create(code='points-prize',name='5 积分',kind='POINTS',point_value=5,in_shop=False,is_active=True)
        LotteryPrize.objects.create(campaign=campaign,reward=reward,probability=10000)
        results=self.race(lambda i:draw(self.accounts[0],campaign.pk,'same-draw-request',1).pk)
        self.assertIsNotNone(results[0]);self.assertEqual(results[0],results[1])
        self.assertEqual(RewardClaim.objects.count(),1)
        self.assertEqual(PointLedger.objects.count(),2)
        self.assertEqual(MemberProfile.objects.get(account=self.accounts[0]).points_balance,500)

    def test_same_wallet_cannot_overspend_across_different_rewards(self):
        rewards=[RewardItem.objects.create(code='reward-'+str(i),name='奖品',kind='PHYSICAL',point_cost=100,is_active=True) for i in range(2)]
        results=self.race(lambda i:redeem(self.accounts[0],rewards[i].pk,'different-reward-'+str(i)).pk)
        self.assertEqual(sum(row is not None for row in results),1)
        self.assertEqual(MemberProfile.objects.get(account=self.accounts[0]).points_balance,0)
        self.assertEqual(RewardClaim.objects.count(),1)
