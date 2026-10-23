"""Run on a dedicated MySQL test DB: real locks, separate DB connections."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.db import close_old_connections, connections
from django.db.models import Sum
from django.test import TransactionTestCase, skipUnlessDBFeature
from rest_framework.exceptions import ValidationError

from .models import (
    LotteryBatch, LotteryCampaign, LotteryDraw, LotteryPityState, LotteryPrize,
    MemberProfile, PointLedger, RewardClaim, RewardItem,
)
from .services import draw_batch
from .test_lottery_gacha import legacy_draws, lottery_member, lottery_pool


@skipUnlessDBFeature('has_select_for_update')
class LotteryGachaConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user, self.account = lottery_member()
        self.campaign, self.rare, self.normal = lottery_pool(point_value=1)

    def race(self, requests):
        barrier = Barrier(len(requests))
        def worker(request):
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                campaign_id = request[2] if len(request) > 2 else self.campaign.pk
                batch = draw_batch(self.account, campaign_id, request[0], 1, count=request[1])
                return ('ok', batch.pk, batch.count)
            except ValidationError as exc:
                return ('validation', str(exc))
            finally:
                connections.close_all()
        with patch('engagement.services.secrets.randbelow', side_effect=lambda upper: upper-1):
            with ThreadPoolExecutor(max_workers=len(requests)) as pool:
                return list(pool.map(worker, requests))

    def test_same_ten_request_in_parallel_has_one_batch_ten_claims_and_one_charge(self):
        result = self.race([('parallel-same-0001', 10)] * 2)
        self.assertEqual([row[0] for row in result], ['ok', 'ok'], result)
        self.assertEqual(result[0][1], result[1][1])
        self.assertEqual((LotteryBatch.objects.count(), LotteryDraw.objects.count(), RewardClaim.objects.count()), (1, 10, 10))
        self.assertEqual(PointLedger.objects.filter(kind='LOTTERY_COST').aggregate(v=Sum('amount'))['v'], -100000)
        self.assertEqual(MemberProfile.objects.get(account=self.account).points_balance, 900900)
        self.assertEqual(LotteryDraw.objects.filter(is_ten_guarantee=True).count(), 1)
        self.assertEqual(LotteryPityState.objects.get(account=self.account).count, 10)

    def test_single_and_ten_compete_for_upfront_balance_without_overspending(self):
        MemberProfile.objects.filter(account=self.account).update(points_balance=100000)
        result = self.race([('parallel-one-0001', 1), ('parallel-ten-0001', 10)])
        self.assertEqual(sum(row[0] == 'ok' for row in result), 1, result)
        count = next(row[2] for row in result if row[0] == 'ok')
        self.assertIn(count, (1, 10))
        self.assertEqual(LotteryDraw.objects.count(), count)
        self.assertEqual(RewardClaim.objects.count(), count)
        awards = count - int(count == 10)
        self.assertEqual(MemberProfile.objects.get(account=self.account).points_balance, 100000 - count*10000 + awards*100)
        self.assertEqual(LotteryPityState.objects.get(account=self.account).count, count)

    def test_concurrent_single_and_ten_at_239_share_one_atomic_hard_pity_counter(self):
        legacy_draws(self.account, self.campaign, self.normal, 239)
        result = self.race([('parallel-pity-one', 1), ('parallel-pity-ten', 10)])
        self.assertEqual([row[0] for row in result], ['ok', 'ok'], result)
        new = LotteryDraw.objects.filter(batch__isnull=False).order_by('id')
        self.assertEqual(new.count(), 11)
        self.assertEqual(new.filter(prize=self.rare, is_pity=True).count(), 1)
        ten_id = next(row[1] for row in result if row[2] == 10)
        hard_inside_ten = new.filter(batch_id=ten_id, is_pity=True).exists()
        common_count = 10 if hard_inside_ten else 9
        self.assertEqual(new.filter(prize=self.normal).count(), common_count)
        self.assertEqual(new.filter(is_ten_guarantee=True).count(), int(not hard_inside_ten))
        self.assertEqual(LotteryPityState.objects.get(account=self.account).count, 10)
        self.assertEqual(PointLedger.objects.filter(kind='LOTTERY_COST').aggregate(v=Sum('amount'))['v'], -110000)
        self.assertEqual(MemberProfile.objects.get(account=self.account).points_balance, 890000 + common_count*100)

    def test_same_request_with_different_counts_has_one_success_and_one_conflict(self):
        result = self.race([('parallel-conflict-01', 1), ('parallel-conflict-01', 10)])
        self.assertEqual(sum(row[0] == 'ok' for row in result), 1, result)
        self.assertEqual(sum(row[0] == 'validation' for row in result), 1, result)
        count = next(row[2] for row in result if row[0] == 'ok')
        self.assertEqual((LotteryBatch.objects.count(), LotteryDraw.objects.count()), (1, count))
        awards = count - int(count == 10)
        self.assertEqual(MemberProfile.objects.get(account=self.account).points_balance, 1_000_000-count*10000+awards*100)

    def test_disjoint_campaign_locks_still_recheck_shared_request_after_points_lock(self):
        # Distinct rewards/campaigns ensure the request is protected by the
        # shared member points lock, rather than an accidentally shared prize.
        other = LotteryCampaign.objects.create(name='不同奖品的旧式奖池', point_cost=100, enabled=True)
        reward = RewardItem.objects.create(code='parallel-disjoint-points', name='1 积分',
            kind='POINTS', point_value=1, is_active=True, in_shop=False)
        sr = RewardItem.objects.create(code='parallel-disjoint-sr', name='另一份 TAG',
            kind='TAG', is_active=True, in_shop=False)
        LotteryPrize.objects.create(campaign=other, reward=sr, probability=99, sort_order=0)
        LotteryPrize.objects.create(campaign=other, reward=reward, probability=9901, sort_order=1)
        result = self.race([('parallel-crosspool-01', 10), ('parallel-crosspool-01', 10, other.pk)])
        self.assertEqual(sum(row[0] == 'ok' for row in result), 1, result)
        self.assertEqual(sum(row[0] == 'validation' for row in result), 1, result)
        self.assertEqual((LotteryBatch.objects.count(), LotteryDraw.objects.count(), RewardClaim.objects.count()), (1, 10, 10))
        self.assertEqual(MemberProfile.objects.get(account=self.account).points_balance, 900900)
