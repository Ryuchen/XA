"""Independent financial and pity-boundary regression for single/ten draws.

All fixtures live in Django's isolated test database.  A 68 experience reward
is a pending delivery claim, never a discount coupon for catalog services.
"""
from unittest.mock import patch

from django.db.models import Sum
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from coupons.models import UserCoupon
from orders.tests.factories import make_user, make_wallet
from wallet.models import Transaction, Wallet

from . import services
from .models import (
    Broadcast, LotteryBatch, LotteryCampaign, LotteryDraw, LotteryPityState,
    LotteryPrize, MemberProfile, PointLedger, RewardClaim, RewardItem,
)


def lottery_member(points=1_000_000):
    user = make_user()
    account = get_or_create_account_for_legacy_user(user)
    make_wallet(user, balance=10000)
    link_legacy_relations(user, account)
    MemberProfile.objects.create(account=account, points_balance=points)
    return user, account


def lottery_pool(*, rare_probability=1, point_value=5, daily_limit=0, target=None):
    campaign = LotteryCampaign.objects.create(
        name='独立测试十连奖池', point_cost=100, enabled=True, daily_limit=daily_limit)
    target = target or RewardItem.objects.create(
        code='lottery-experience-68', name='68 体验单', kind='SERVICE',
        is_active=True, in_shop=False)
    common = RewardItem.objects.create(
        code=f'gacha-common-{campaign.pk}', name=f'{point_value} 积分', kind='POINTS',
        point_value=point_value, is_active=True, in_shop=False)
    rare = LotteryPrize.objects.create(campaign=campaign, reward=target, probability=rare_probability, sort_order=0)
    sr = RewardItem.objects.create(code=f'gacha-sr-{campaign.pk}', name='专属 TAG',
        kind='TAG', is_active=True, in_shop=False)
    LotteryPrize.objects.create(campaign=campaign, reward=sr, probability=99, sort_order=1)
    normal = LotteryPrize.objects.create(
        campaign=campaign, reward=common, probability=9901-rare_probability, sort_order=2)
    return campaign, rare, normal


def legacy_draws(account, campaign, prize, count, *, prefix='legacy'):
    """Historical rows intentionally have no batch/pity snapshot."""
    claim_keys = [f'{prefix}-claim-{account.pk}-{campaign.pk}-{index}' for index in range(count)]
    RewardClaim.objects.bulk_create([
        RewardClaim(account=account, reward=prize.reward,
                    event_key=key,
                    source='LOTTERY', name_snapshot=prize.reward.name,
                    kind_snapshot=prize.reward.kind)
        for key in claim_keys
    ])
    # MySQL does not return auto PKs from bulk_create. Query committed fixture
    # IDs by their unique keys rather than relying on SQLite's RETURNING.
    claim_ids = dict(RewardClaim.objects.filter(event_key__in=claim_keys).values_list('event_key', 'pk'))
    draw_keys = [f'{prefix}-draw-{account.pk}-{campaign.pk}-{index}' for index in range(count)]
    LotteryDraw.objects.bulk_create([
        LotteryDraw(account=account, business_cycle=account.business_cycle,
                    campaign=campaign, prize=prize, claim_id=claim_ids[claim_key],
                    event_key=draw_key,
                    point_cost=100, campaign_version=campaign.version,
                    random_ticket=9999, probability_snapshot=[])
        for draw_key, claim_key in zip(draw_keys, claim_keys)
    ])
    rows = {row.event_key: row for row in LotteryDraw.objects.filter(event_key__in=draw_keys)}
    return [rows[key] for key in draw_keys]


def lottery_snapshot():
    models = (LotteryBatch, LotteryDraw, LotteryPityState, MemberProfile,
              RewardClaim, PointLedger, RewardItem, Broadcast, UserCoupon,
              Wallet, Transaction)
    return {model._meta.label: list(model.objects.order_by('pk').values()) for model in models}


class LotteryGachaTests(APITestCase):
    def setUp(self):
        self.user, self.account = lottery_member()
        self.client.force_authenticate(self.user)
        self.campaign, self.rare, self.normal = lottery_pool()
        self.sr = self.campaign.prizes.get(reward__kind='TAG')

    def batch(self, key='gacha-ten-0001', *, count=10, ticket=9999, campaign=None):
        campaign = campaign or self.campaign
        with patch('engagement.services.secrets.randbelow', side_effect=lambda upper: min(ticket, upper-1)):
            return services.draw_batch(self.account, campaign.pk, key, campaign.version, count=count)

    def state(self):
        return LotteryPityState.objects.get(account=self.account,
            business_cycle=self.account.business_cycle, campaign=self.campaign)

    def test_ten_costs_and_awards_each_draw_once_with_ordered_claims(self):
        batch = self.batch()
        rows = list(batch.draws.order_by('draw_index'))
        self.assertEqual(len(rows), 10)
        self.assertEqual([row.draw_index for row in rows], list(range(1, 11)))
        self.assertEqual([row.pity_before for row in rows], list(range(10)))
        self.assertEqual([row.pity_after for row in rows], list(range(1, 11)))
        self.assertEqual([row.prize_id for row in rows], [self.normal.pk]*9 + [self.sr.pk])
        self.assertFalse(any(row.is_pity for row in rows))
        self.assertEqual([row.draw_index for row in rows if row.is_ten_guarantee], [10])
        self.assertEqual(RewardClaim.objects.count(), 10)
        self.assertEqual(PointLedger.objects.filter(kind='LOTTERY_COST').count(), 10)
        self.assertEqual(PointLedger.objects.filter(kind='LOTTERY_COST').aggregate(v=Sum('amount'))['v'], -100000)
        self.assertEqual(PointLedger.objects.filter(kind='LOTTERY_AWARD').aggregate(v=Sum('amount'))['v'], 4500)
        self.assertEqual(MemberProfile.objects.get(account=self.account).points_balance, 904500)
        self.assertEqual((batch.count, batch.total_point_cost, batch.points_balance), (10, 1000, 904500))
        self.assertEqual(batch.pity_snapshot['count'], 10)
        self.assertEqual(batch.pity_snapshot['remaining'], 230)
        self.assertEqual(self.state().count, 10)
        self.assertEqual(Broadcast.objects.count(), 10)

    def test_ten_requires_upfront_total_without_borrowing_its_point_prizes(self):
        MemberProfile.objects.filter(account=self.account).update(points_balance=99999)
        RewardItem.objects.filter(pk=self.normal.reward_id).update(point_value=10000)
        before = lottery_snapshot()
        with self.assertRaises(ValidationError):
            self.batch()
        self.assertEqual(lottery_snapshot(), before)

    def test_single_on_240_forces_target_then_ten_continues_counting_in_order(self):
        legacy_draws(self.account, self.campaign, self.normal, 239)
        batch = self.batch()
        rows = list(batch.draws.order_by('draw_index'))
        self.assertEqual((rows[0].prize_id, rows[0].pity_before, rows[0].pity_after, rows[0].is_pity),
                         (self.rare.pk, 239, 0, True))
        self.assertEqual([row.prize_id for row in rows[1:]], [self.normal.pk] * 9)
        self.assertEqual([row.pity_after for row in rows[1:]], list(range(1, 10)))
        self.assertEqual(self.state().count, 9)
        self.assertEqual(rows[0].claim.status, RewardClaim.Status.PENDING)
        self.assertEqual(UserCoupon.objects.count(), 0)
        self.assertFalse(any(row.is_ten_guarantee for row in rows))

    def test_ten_crosses_hard_pity_in_its_middle_without_extra_charge(self):
        legacy_draws(self.account, self.campaign, self.normal, 235)
        rows = list(self.batch().draws.order_by('draw_index'))
        self.assertEqual([row.draw_index for row in rows if row.is_pity], [5])
        self.assertEqual([row.pity_after for row in rows], [236, 237, 238, 239, 0, 1, 2, 3, 4, 5])
        self.assertEqual(self.state().count, 5)
        self.assertFalse(any(row.is_ten_guarantee for row in rows))
        self.assertEqual(PointLedger.objects.filter(kind='LOTTERY_COST').aggregate(v=Sum('amount'))['v'], -100000)

    def test_early_natural_target_resets_without_falsely_marking_hard_pity(self):
        legacy_draws(self.account, self.campaign, self.normal, 237)
        with patch('engagement.services.secrets.randbelow', side_effect=[0] + [9999]*9):
            rows = list(services.draw_batch(self.account, self.campaign.pk,
                'gacha-early-0001', 1, count=10).draws.order_by('draw_index'))
        self.assertEqual((rows[0].prize_id, rows[0].is_pity, rows[0].pity_before, rows[0].pity_after),
                         (self.rare.pk, False, 237, 0))
        self.assertFalse(any(row.is_pity for row in rows))
        self.assertEqual(self.state().count, 9)

    def test_probability_zero_target_is_still_obtained_on_hard_pity(self):
        self.rare.probability = 0; self.rare.save(update_fields=['probability'])
        self.normal.probability = 9901; self.normal.save(update_fields=['probability'])
        legacy_draws(self.account, self.campaign, self.normal, 239)
        row = self.batch(count=1).draws.get()
        self.assertTrue(row.is_pity)
        self.assertEqual(row.prize_id, self.rare.pk)
        self.assertEqual(self.state().count, 0)

    def test_batch_replay_uses_original_snapshot_and_rejects_other_count_or_campaign(self):
        first = self.batch()
        self.batch('gacha-next-0001', count=1)
        self.campaign.enabled = False; self.campaign.version = 2; self.campaign.save()
        before = lottery_snapshot()
        replay = services.draw_batch(self.account, self.campaign.pk, 'gacha-ten-0001', 1, count=10)
        self.assertEqual((replay.pk, replay.points_balance, replay.pity_snapshot),
                         (first.pk, first.points_balance, first.pity_snapshot))
        with self.assertRaises(ValidationError):
            services.draw_batch(self.account, self.campaign.pk, 'gacha-ten-0001', 2, count=1)
        other, _, _ = lottery_pool(target=self.rare.reward)
        unchanged = lottery_snapshot()
        with self.assertRaises(ValidationError):
            services.draw_batch(self.account, other.pk, 'gacha-ten-0001', 1, count=10)
        self.assertEqual(lottery_snapshot(), unchanged)
        self.assertEqual(LotteryBatch.objects.count(), 2)
        self.assertEqual(before['engagement.LotteryDraw'], unchanged['engagement.LotteryDraw'])

    def test_sixth_claim_failure_rolls_back_every_draw_cost_stock_and_pity(self):
        legacy_draws(self.account, self.campaign, self.normal, 235)
        self.normal.reward.stock = 20; self.normal.reward.save(update_fields=['stock'])
        before = lottery_snapshot()
        real_claim = services.create_claim
        calls = 0
        def fail_sixth(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 6:
                raise RuntimeError('synthetic sixth delivery failure')
            return real_claim(*args, **kwargs)
        with patch('engagement.services.create_claim', side_effect=fail_sixth):
            with self.assertRaisesRegex(RuntimeError, 'sixth delivery'):
                self.batch()
        self.assertEqual(calls, 6)
        self.assertEqual(lottery_snapshot(), before)

    def test_selected_inventory_exhaustion_mid_batch_rolls_back_entire_batch(self):
        self.normal.reward.stock = 8; self.normal.reward.save(update_fields=['stock'])
        before = lottery_snapshot()
        with self.assertRaises(ValidationError):
            self.batch()
        self.assertEqual(lottery_snapshot(), before)

    def test_exhausted_hard_pity_reward_blocks_even_zero_probability_pool(self):
        self.rare.probability = 0; self.rare.save(update_fields=['probability'])
        self.normal.probability = 9901; self.normal.save(update_fields=['probability'])
        self.rare.reward.stock = 0; self.rare.reward.save(update_fields=['stock'])
        before = lottery_snapshot()
        with self.assertRaises(ValidationError):
            self.batch()
        self.assertEqual(lottery_snapshot(), before)

    def test_daily_remaining_less_than_ten_rejects_all_ten(self):
        self.campaign.daily_limit = 12; self.campaign.save(update_fields=['daily_limit'])
        legacy_draws(self.account, self.campaign, self.normal, 3)
        before = lottery_snapshot()
        with self.assertRaises(ValidationError):
            self.batch()
        self.assertEqual(lottery_snapshot(), before)
        self.batch('gacha-single-0001', count=1)
        self.assertEqual(LotteryDraw.objects.count(), 4)

    def test_legacy_history_counts_only_after_last_target_and_survives_version_change(self):
        legacy_draws(self.account, self.campaign, self.normal, 12, prefix='before-win')
        legacy_draws(self.account, self.campaign, self.rare, 1, prefix='natural-win')
        legacy_draws(self.account, self.campaign, self.normal, 7, prefix='after-win')
        first = self.batch(count=1).draws.get()
        self.assertEqual((first.pity_before, first.pity_after), (7, 8))
        self.campaign.version = 2; self.campaign.save(update_fields=['version'])
        second = self.batch('gacha-version-0001', count=1).draws.get()
        self.assertEqual((second.pity_before, second.pity_after, second.campaign_version), (8, 9, 2))

    def test_account_business_cycle_and_pool_have_independent_pity(self):
        legacy_draws(self.account, self.campaign, self.normal, 239)
        _, other_account = lottery_member()
        with patch('engagement.services.secrets.randbelow', return_value=9999):
            foreign = services.draw_batch(other_account, self.campaign.pk, 'gacha-other-0001', 1, count=1)
        self.assertEqual(foreign.draws.get().pity_before, 0)
        other_pool, _, _ = lottery_pool(target=self.rare.reward)
        self.assertEqual(self.batch('gacha-pool-0001', count=1, campaign=other_pool).draws.get().pity_before, 0)
        self.account.business_cycle += 1; self.account.save(update_fields=['business_cycle'])
        self.assertEqual(self.batch('gacha-cycle-0001', count=1).draws.get().pity_before, 0)
        self.assertEqual(self.state().count, 1)
        self.assertEqual(LotteryDraw.objects.filter(account=self.account, business_cycle=1, campaign=self.campaign).count(), 239)

    def test_legacy_single_replay_remains_compatible_but_cannot_be_reused_as_ten(self):
        existing = legacy_draws(self.account, self.campaign, self.normal, 1)[0]
        existing.event_key = f'draw:{self.account.pk}:{self.account.business_cycle}:gacha-legacy-0001'
        existing.save(update_fields=['event_key'])
        before = lottery_snapshot()
        self.assertEqual(services.draw(self.account, self.campaign.pk, 'gacha-legacy-0001', 1).pk, existing.pk)
        with self.assertRaises(ValidationError):
            services.draw_batch(self.account, self.campaign.pk, 'gacha-legacy-0001', 1, count=10)
        self.assertEqual(lottery_snapshot(), before)

    def test_legacy_no_target_pool_keeps_weighted_single_and_ten_behavior(self):
        self.rare.delete()
        self.normal.probability = 9901; self.normal.save(update_fields=['probability'])
        batch = self.batch()
        self.assertEqual(batch.draws.count(), 10)
        self.assertFalse(batch.pity_snapshot['enabled'])
        self.assertFalse(batch.draws.filter(is_pity=True).exists())
        with patch('engagement.services.secrets.randbelow', return_value=9999):
            self.assertEqual(services.draw(self.account, self.campaign.pk, 'gacha-old-api-0001', 1).prize_id, self.normal.pk)

    def test_http_single_keeps_flat_response_and_ten_returns_immutable_batch(self):
        with patch('engagement.services.secrets.randbelow', side_effect=lambda upper: upper-1):
            single = self.client.post('/api/club/lottery/', {'campaign_id': self.campaign.pk,
                'version': 1, 'client_request_id': 'gacha-api-single'}, format='json')
            ten = self.client.post('/api/club/lottery/', {'campaign_id': self.campaign.pk,
                'version': 1, 'client_request_id': 'gacha-api-ten-01', 'count': 10}, format='json')
        self.assertEqual(single.status_code, 200, single.data)
        self.assertEqual(single.data['data']['claim']['source'], 'LOTTERY')
        self.assertEqual(single.data['data']['prize_id'], self.normal.pk)
        self.assertEqual(single.data['data']['count'], 1)
        self.assertEqual(ten.status_code, 200, ten.data)
        result = ten.data['data']
        self.assertEqual((result['count'], result['total_point_cost'], len(result['draws'])), (10, 1000, 10))
        self.assertEqual(result['pity']['count'], 11)
        self.assertEqual([row['rarity'] for row in result['draws']], ['R']*9 + ['SR'])
        self.assertEqual([row['is_ten_guarantee'] for row in result['draws']], [False]*9 + [True])
        self.assertTrue(result['ten_guarantee']['available'])
        replay = self.client.post('/api/club/lottery/', {'campaign_id': self.campaign.pk,
            'version': 1, 'client_request_id': 'gacha-api-ten-01', 'count': 10}, format='json')
        self.assertEqual(replay.data, ten.data)
        get = self.client.get('/api/club/lottery/')
        self.assertEqual(get.data['data']['pity']['count'], 11)
        self.assertEqual(get.data['data']['pity']['limit'], 240)

    def test_http_invalid_count_never_writes_financial_or_pity_data(self):
        for count in (0, 2, 9, 11, -1, True, 'ten', None):
            with self.subTest(count=count):
                before = lottery_snapshot()
                response = self.client.post('/api/club/lottery/', {'campaign_id': self.campaign.pk,
                    'version': 1, 'client_request_id': 'gacha-invalid-01', 'count': count}, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(lottery_snapshot(), before)

    def test_http_daily_remaining_is_current_while_replay_results_are_snapshotted(self):
        self.campaign.daily_limit = 25; self.campaign.save(update_fields=['daily_limit'])
        body = {'campaign_id': self.campaign.pk, 'version': 1,
                'client_request_id': 'gacha-daily-ten01', 'count': 10}
        with patch('engagement.services.secrets.randbelow', side_effect=lambda upper: upper-1):
            original = self.client.post('/api/club/lottery/', body, format='json').data['data']
            self.assertEqual(original['daily_remaining'], 15)
            later = self.client.post('/api/club/lottery/', {
                **body, 'client_request_id': 'gacha-daily-one01', 'count': 1}, format='json').data['data']
        self.assertEqual(later['daily_remaining'], 14)
        replay = self.client.post('/api/club/lottery/', body, format='json').data['data']
        self.assertEqual(replay['daily_remaining'], 14)
        self.assertEqual(replay['points_balance'], original['points_balance'])
        self.assertEqual(replay['pity'], original['pity'])
        self.assertEqual(replay['draws'], original['draws'])
        self.assertEqual(self.client.get('/api/club/lottery/').data['data']['daily_remaining'], 14)

    def test_natural_sr_in_first_nine_satisfies_ten_without_forcing_last_draw(self):
        with patch('engagement.services.secrets.randbelow', side_effect=[1]+[9999]*9):
            batch = services.draw_batch(self.account, self.campaign.pk, 'ten-natural-sr01', 1, count=10)
        rows = list(batch.draws.order_by('draw_index'))
        self.assertEqual([row.prize_id for row in rows], [self.sr.pk]+[self.normal.pk]*9)
        self.assertFalse(any(row.is_ten_guarantee for row in rows))
        self.assertEqual(rows[-1].random_ticket, 9999)
        self.assertEqual(sum(item['weight'] for item in rows[-1].selection_pool), 10000)

    def test_tenth_hard_pity_wins_priority_and_has_a_deterministic_audit_pool(self):
        legacy_draws(self.account, self.campaign, self.normal, 230)
        with patch('engagement.services.secrets.randbelow', side_effect=lambda upper: upper-1) as random:
            batch = services.draw_batch(self.account, self.campaign.pk, 'ten-hard-tenth01', 1, count=10)
        rows = list(batch.draws.order_by('draw_index'))
        final = rows[-1]
        self.assertEqual([row.prize_id for row in rows], [self.normal.pk]*9+[self.rare.pk])
        self.assertEqual((final.is_pity, final.is_ten_guarantee, final.pity_before, final.pity_after),
                         (True, False, 239, 0))
        self.assertEqual(final.random_ticket, 0)
        self.assertEqual(final.selection_pool, [{'id':self.rare.pk, 'reward':self.rare.reward_id, 'weight':1}])
        self.assertEqual(random.call_count, 9)

    def test_ten_candidate_weight_boundaries_use_an_independent_normalized_oracle(self):
        extra = RewardItem.objects.create(code='ten-weight-naming', name='额外冠名',
            kind='NAMING', is_active=True, in_shop=False)
        third = LotteryPrize.objects.create(campaign=self.campaign, reward=extra, probability=900, sort_order=2)
        self.normal.sort_order = 3; self.normal.probability = 9000
        self.normal.save(update_fields=['sort_order','probability'])
        oracle = [(0, self.rare.pk), (1, self.sr.pk), (99, self.sr.pk),
                  (100, third.pk), (999, third.pk)]
        pool = [{'id':self.rare.pk, 'reward':self.rare.reward_id, 'weight':1},
                {'id':self.sr.pk, 'reward':self.sr.reward_id, 'weight':99},
                {'id':third.pk, 'reward':third.reward_id, 'weight':900}]
        for index, (ticket, expected) in enumerate(oracle):
            with self.subTest(ticket=ticket):
                calls = []
                def random(upper):
                    calls.append(upper)
                    return ticket if upper == 1000 else upper-1
                with patch('engagement.services.secrets.randbelow', side_effect=random):
                    batch = services.draw_batch(self.account, self.campaign.pk,
                        f'ten-weight-case-{index}', 1, count=10)
                rows = list(batch.draws.order_by('draw_index'))
                self.assertEqual([row.prize_id for row in rows[:9]], [self.normal.pk]*9)
                self.assertEqual(rows[-1].prize_id, expected)
                self.assertEqual(rows[-1].random_ticket, ticket)
                self.assertEqual(rows[-1].selection_pool, pool)
                self.assertTrue(rows[-1].is_ten_guarantee)
                self.assertFalse(rows[-1].is_pity)
                self.assertEqual(calls, [10000]*9+[1000])

    def test_zero_probability_ssr_is_not_a_ten_candidate(self):
        self.rare.probability = 0; self.rare.save(update_fields=['probability'])
        self.normal.probability = 9901; self.normal.save(update_fields=['probability'])
        row = self.batch().draws.get(draw_index=10)
        self.assertEqual(row.prize_id, self.sr.pk)
        self.assertTrue(row.is_ten_guarantee)
        self.assertEqual(row.selection_pool, [{'id':self.sr.pk,'reward':self.sr.reward_id,'weight':99}])
        info = self.client.get('/api/club/lottery/').data['data']['ten_guarantee']
        self.assertEqual(info['eligible_prize_ids'], [self.sr.pk])
        self.assertTrue(info['available'])

    def test_no_positive_non_points_candidate_rejects_ten_but_preserves_single(self):
        self.rare.probability = 0; self.rare.save(update_fields=['probability'])
        self.sr.probability = 0; self.sr.save(update_fields=['probability'])
        self.normal.probability = 10000; self.normal.save(update_fields=['probability'])
        before = lottery_snapshot()
        with self.assertRaises(ValidationError):
            self.batch()
        self.assertEqual(lottery_snapshot(), before)
        info = self.client.get('/api/club/lottery/').data['data']['ten_guarantee']
        self.assertFalse(info['available'])
        self.assertEqual(info['eligible_prize_ids'], [])
        self.assertTrue(info['unavailable_reason'])
        row = self.batch('ten-disabled-single', count=1).draws.get()
        self.assertEqual(row.prize_id, self.normal.pk)
        self.assertFalse(row.is_ten_guarantee)
        self.assertFalse(row.is_pity)

    def test_tenth_guaranteed_claim_failure_rolls_back_first_nine_awards_and_pity(self):
        before = lottery_snapshot()
        real_claim = services.create_claim
        calls = []
        def fail_guaranteed(account, reward, *args, **kwargs):
            calls.append(reward.pk)
            if reward.pk == self.sr.reward_id:
                raise RuntimeError('synthetic guaranteed delivery failure')
            return real_claim(account, reward, *args, **kwargs)
        with patch('engagement.services.create_claim', side_effect=fail_guaranteed):
            with self.assertRaisesRegex(RuntimeError, 'guaranteed delivery'):
                self.batch()
        self.assertEqual(calls, [self.normal.reward_id]*9+[self.sr.reward_id])
        self.assertEqual(lottery_snapshot(), before)

    def test_exhausted_ten_reward_blocks_next_ten_without_partial_deductions(self):
        self.sr.reward.stock = 1; self.sr.reward.save(update_fields=['stock'])
        self.batch()
        self.sr.reward.refresh_from_db()
        self.assertEqual(self.sr.reward.stock, 0)
        before = lottery_snapshot()
        with self.assertRaises(ValidationError):
            self.batch('ten-out-of-stock')
        self.assertEqual(lottery_snapshot(), before)

    def test_ten_fallback_ssr_resets_personal_counter_without_hard_pity_marker(self):
        legacy_draws(self.account, self.campaign, self.normal, 219)
        with patch('engagement.services.secrets.randbelow', side_effect=lambda upper: 0 if upper == 100 else upper-1):
            batch = services.draw_batch(self.account, self.campaign.pk, 'ten-fallback-ssr', 1, count=10)
        final = batch.draws.get(draw_index=10)
        self.assertEqual((final.prize_id, final.is_ten_guarantee, final.is_pity), (self.rare.pk, True, False))
        self.assertEqual((final.pity_before, final.pity_after, self.state().count), (228,0,0))
        self.assertEqual(final.claim.status, 'PENDING')
        self.assertEqual(UserCoupon.objects.count(), 0)

    def test_ten_guarantee_is_per_batch_and_never_triggered_by_ten_singles(self):
        for index in range(10):
            self.batch(f'ten-separate-single-{index}', count=1)
        self.assertEqual(LotteryDraw.objects.filter(prize=self.normal).count(), 10)
        self.assertFalse(LotteryDraw.objects.filter(is_ten_guarantee=True).exists())
        self.batch('ten-independent-first')
        self.batch('ten-independent-second')
        self.assertEqual(list(LotteryDraw.objects.filter(is_ten_guarantee=True)
            .values_list('draw_index', flat=True)), [10,10])
        self.assertEqual(self.state().count, 30)
