"""Ranking preview combines actual store facts and bounded receipt times."""
import os
from contextlib import contextmanager
from datetime import timedelta
from fractions import Fraction
from time import sleep
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user
from console.capacity_config import save_capacity
from console.grab_config import save_grab_window
from orders.grab_config import current_rules
from orders.grab_bound_ranking import preview
from orders.grab_pool_snapshot import create_initial_snapshot
from orders.grab_receipt_probe import record_intent
from orders.grab_team_draft import attach_pass_evidence, confirm, first_round, invite
from orders.models import (Evaluation, Order, OrderGrabReceiptProbe, OrderOriginalAttribution,
                           OrderParticipationSegment, OrderRefund)
from orders.paid_service_scope import capture, record
from orders.tests.factories import make_order, make_provider, make_service, make_user
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.tests_hosts import xinan_store
from wallet.models import SystemConfig


def provider(tier='', *, key=''):
    user = make_provider(deposit_mode='EARNINGS', pass_tier=tier,
                         pass_expires_at=timezone.now() + timedelta(hours=1) if tier else None)
    account = get_or_create_account_for_legacy_user(user)
    account.is_openid_bound, account.openid = True, f'bound-ranking-{key or account.pk}'
    account.save(update_fields=['is_openid_bound', 'openid'])
    profile = user.escort_profile
    profile.account, profile.wechat_avatar_legacy_exempt = account, True
    profile.save(update_fields=['account', 'wechat_avatar_legacy_exempt'])
    return user, account, profile


def pool_for(*, size=1, delays=None):
    delays = delays or {'BLACK': 0, 'GOLD': 0, 'SILVER': 0, 'BRONZE': 0, 'NONE': 0}
    for tier, seconds in delays.items():
        SystemConfig.objects.update_or_create(key=f'provider_pass_delay_seconds_{tier}', defaults={'value': str(seconds)})
    save_grab_window(30, current_rules()['version'], lambda *args: None)
    service = make_service(billing_provider_count=size, fulfillment_provider_count=size)
    order = make_order(make_user(), service=service, payment_status='PAID',
                       escort_mode='DOUBLE' if size == 2 else 'SINGLE')
    if size == 2:
        record(order, capture(service))
    with transaction.atomic():
        pool = create_initial_snapshot(order.pk)
        round_ = first_round(pool.pk)
    return order, round_


@contextmanager
def after_window(round_):
    """Advance the inspection clocks after recording the original receipts."""
    from orders.grab_receipt_probe import _db_clock

    _, precision, driver = _db_clock()
    now = round_.deadline + timedelta(seconds=3)
    with (patch('django.utils.timezone.now', return_value=now),
          patch('orders.grab_receipt_probe._db_clock', return_value=(now, precision, driver))):
        yield


def rank(round_, ids):
    with after_window(round_), transaction.atomic():
        return preview(round_.pk, ids)


@override_settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True)
class BoundSingleRankingTest(TransactionTestCase):
    def setUp(self):
        self.first_user, self.first, self.first_profile = provider('GOLD', key='first')
        self.second_user, self.second, self.second_profile = provider('SILVER', key='second')
        self.order, self.round = pool_for()

    def submit(self, account, key):
        return record_intent(self.round.pk, account.pk, 'SINGLE', key, {}).receipt_id

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_snapshot_tier_wins_before_evaluation_and_never_assigns(self):
        first, second = self.submit(self.first, 'first'), self.submit(self.second, 'second')
        boss = make_user()
        Evaluation.objects.create(order=make_order(boss), customer=boss, provider=self.second_user,
                                  provider_account=self.second, score=5)
        self.first_profile.pass_tier, self.first_profile.pass_expires_at = '', None
        self.first_profile.save(update_fields=['pass_tier', 'pass_expires_at'])
        outcome = rank(self.round, [second, first])
        self.assertEqual((outcome.status, outcome.winner_id, outcome.can_assign), ('POTENTIAL_WINNER', first, False))
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.provider_account_id), ('PENDING', None))

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_same_metrics_use_proven_intervals_and_overlap_needs_review(self):
        self.second_profile.pass_tier = 'GOLD'
        self.second_profile.save(update_fields=['pass_tier'])
        first = self.submit(self.first, 'first')
        sleep(0.02)
        second = self.submit(self.second, 'second')
        outcome = rank(self.round, [second, first])
        self.assertEqual((outcome.status, outcome.winner_id), ('POTENTIAL_WINNER', first))
        left = OrderGrabReceiptProbe.objects.get(pk=first)
        OrderGrabReceiptProbe.objects.filter(pk=second).update(lower_at=left.lower_at,
            upper_observed_at=left.upper_observed_at)
        outcome = rank(self.round, [second, first])
        self.assertEqual((outcome.status, outcome.winner_id, outcome.review_candidate_ids, outcome.can_assign),
                         ('REVIEW', None, (first, second), False))

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_actual_valid_rating_precedes_time_and_voided_rating_does_not(self):
        self.second_profile.pass_tier = 'GOLD'; self.second_profile.save(update_fields=['pass_tier'])
        first = self.submit(self.first, 'first')
        sleep(0.02)
        second = self.submit(self.second, 'second')
        boss = make_user()
        rating = Evaluation.objects.create(order=make_order(boss), customer=boss, provider=self.second_user,
                                           provider_account=self.second, score=5)
        self.assertEqual(rank(self.round, [first, second]).winner_id, second)
        Evaluation.objects.filter(pk=rating.pk).update(voided_at=timezone.now())
        self.assertEqual(rank(self.round, [first, second]).winner_id, first)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_review_only_includes_candidates_that_could_have_finished_first(self):
        self.second_profile.pass_tier = 'GOLD'; self.second_profile.save(update_fields=['pass_tier'])
        _, third_account, _ = provider('GOLD', key='third')
        first = self.submit(self.first, 'first')
        sleep(0.02)
        second = self.submit(self.second, 'second')
        sleep(0.02)
        third = self.submit(third_account, 'third')
        first_row = OrderGrabReceiptProbe.objects.get(pk=first)
        OrderGrabReceiptProbe.objects.filter(pk=second).update(lower_at=first_row.lower_at,
            upper_observed_at=first_row.upper_observed_at)
        result = rank(self.round, [third, first, second])
        self.assertEqual((result.status, result.review_candidate_ids), ('REVIEW', (first, second)))

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_unverified_or_duplicate_proofs_never_make_a_winner(self):
        first = self.submit(self.first, 'first')
        second = self.submit(self.first, 'same-person')
        with self.assertRaisesMessage(ValueError, '重复参加'):
            rank(self.round, [first, second])
        with transaction.atomic(), self.assertRaisesMessage(ValueError, '尚未结束'):
            preview(self.round.pk, [first])
        OrderGrabReceiptProbe.objects.filter(pk=first).update(fingerprint='0' * 64)
        with self.assertRaisesMessage(ValueError, '凭证待核对'):
            rank(self.round, [first])
        self.assertEqual((rank(self.round, []).status, rank(self.round, []).can_assign), ('NO_SELECTED_PROOFS', False))

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_registration_before_visibility_is_rejected_and_boundary_is_reviewed(self):
        order, round_ = pool_for(delays={'BLACK': 0, 'GOLD': 10, 'SILVER': 20, 'BRONZE': 30, 'NONE': 40})
        proof = record_intent(round_.pk, self.first.pk, 'SINGLE', 'not-visible-yet', {})
        with self.assertRaisesMessage(ValueError, '尚未向该陪玩开放'):
            rank(round_, [proof.receipt_id])
        visible_at = round_.opened_at + timedelta(seconds=10)
        OrderGrabReceiptProbe.objects.filter(pk=proof.receipt_id).update(
            lower_at=visible_at - timedelta(milliseconds=1), upper_observed_at=visible_at + timedelta(milliseconds=1))
        result = rank(round_, [proof.receipt_id])
        self.assertEqual((result.status, result.review_candidate_ids, result.can_assign),
                         ('REVIEW', (proof.receipt_id,), False))

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_historical_refund_without_attribution_requires_review(self):
        first = self.submit(self.first, 'first')
        boss = make_user()
        item = make_order(boss, amount=1000, original_amount=1000, payment_status='PAID', status='COMPLETED')
        now = timezone.now()
        Order.objects.filter(pk=item.pk).update(in_service_at=now, completed_at=now)
        attribution = OrderOriginalAttribution.objects.create(order=item, original_amount=1000, status='ACTIVE')
        OrderParticipationSegment.objects.create(attribution=attribution, provider_account=self.first,
            slot=1, original_share=1000, accepted_at=now, started_at=now)
        OrderRefund.objects.create(order=item, amount=100, reason='历史归属待核对')
        result = rank(self.round, [first])
        self.assertEqual((result.status, result.winner_id, result.can_assign), ('REVIEW', None, False))
        self.assertEqual(result.review_orders, ((self.first.pk, (item.pk,)),))


@override_settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
                   REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED=True)
class BoundTeamRankingTest(TransactionTestCase):
    def setUp(self):
        # Both invitees must be visible while the 30-second window is open.
        for tier in ('BLACK', 'GOLD', 'SILVER', 'BRONZE', 'NONE'):
            SystemConfig.objects.update_or_create(key=f'provider_pass_delay_seconds_{tier}', defaults={'value': '0'})
        save_capacity(3, 0, lambda *args: None)
        self.people = [provider(tier, key=str(i)) for i, tier in enumerate(('BLACK', 'GOLD', 'BLACK', 'BRONZE'))]
        self.order, self.round = pool_for(size=2)

    def team(self, captain, partner, key):
        a, b = self.people[captain][1], self.people[partner][1]
        invitation = record_intent(self.round.pk, a.pk, 'INVITE', key, {'partner_id': b.pk})
        with transaction.atomic():
            team = invite(self.round.pk, a.pk, b.pk, key)
            attach_pass_evidence(self.round.pk, team.pk, invitation.receipt_id)
        sleep(0.02)
        confirmed = record_intent(self.round.pk, b.pk, 'CONFIRM', key + '-confirm', {'team_id': team.pk})
        with transaction.atomic():
            confirm(self.round.pk, team.pk, b.pk)
            attach_pass_evidence(self.round.pk, team.pk, confirmed.receipt_id)
        return team

    def historical_started_order(self, index, amount):
        boss = make_user()
        item = make_order(boss, amount=amount, original_amount=amount, payment_status='PAID',
                          status='COMPLETED')
        now = timezone.now()
        Order.objects.filter(pk=item.pk).update(in_service_at=now, completed_at=now)
        attribution = OrderOriginalAttribution.objects.create(order=item, original_amount=amount, status='ACTIVE')
        OrderParticipationSegment.objects.create(attribution=attribution, provider_account=self.people[index][1],
            slot=1, original_share=amount, accepted_at=now, started_at=now)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_each_team_dimension_takes_its_own_member_maximum(self):
        for index, amount in ((0, 1000), (1, 10000), (2, 9000)):
            self.historical_started_order(index, amount)
        boss = make_user()
        for index, score in ((0, 5), (0, 5), (0, 4), (0, 4), (0, 3), (1, 5), (2, 5)):
            Evaluation.objects.create(order=make_order(boss), customer=boss, provider=self.people[index][0],
                                      provider_account=self.people[index][1], score=score)
        best, other = self.team(0, 1, 'team-a'), self.team(2, 3, 'team-b')
        # A teammate's later card change cannot change the earlier proof.
        self.people[1][2].pass_tier, self.people[1][2].pass_expires_at = '', None
        self.people[1][2].save(update_fields=['pass_tier', 'pass_expires_at'])
        result = rank(self.round, [other.pk, best.pk])
        self.assertEqual((result.status, result.winner_id, result.can_assign), ('POTENTIAL_WINNER', best.pk, False))
        snapshot = next(item for item in result.candidates if item.candidate_id == best.pk)
        self.assertEqual(snapshot.priority, (4, 10000, Fraction(1)))
        self.assertEqual([(item.submitted_pass_tier, item.net_original_amount) for item in snapshot.members],
                         [('BLACK', 1000), ('GOLD', 10000)])
        self.assertEqual([(item.good_evaluations, item.valid_evaluations) for item in snapshot.members],
                         [(4, 5), (1, 1)])

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_missing_or_inconsistent_team_evidence_blocks_preview(self):
        team = self.team(0, 1, 'team-corrupt')
        member = team.members.get(account=self.people[1][1])
        receipt = member.receipt_pass_evidence.receipt
        receipt.fingerprint = '0' * 64; receipt.save(update_fields=['fingerprint'])
        with self.assertRaisesMessage(ValueError, '凭证待核对'):
            rank(self.round, [team.pk])
        self.order.refresh_from_db(); self.assertEqual(self.order.status, Order.Status.PENDING)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_invitation_and_confirmation_with_overlapping_bounds_need_review(self):
        team = self.team(0, 1, 'team-overlap')
        first = team.members.get(account=self.people[0][1]).receipt_pass_evidence.receipt
        second = team.members.get(account=self.people[1][1]).receipt_pass_evidence.receipt
        OrderGrabReceiptProbe.objects.filter(pk=first.pk).update(lower_at=second.lower_at,
                                                                 upper_observed_at=second.upper_observed_at)
        result = rank(self.round, [team.pk])
        self.assertEqual((result.status, result.review_candidate_ids, result.can_assign), ('REVIEW', (team.pk,), False))

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_partner_may_be_invited_early_but_early_confirmation_cannot_rank(self):
        self.order, self.round = pool_for(size=2,
            delays={'BLACK': 0, 'GOLD': 3, 'SILVER': 4, 'BRONZE': 5, 'NONE': 6})
        a, b = self.people[0][1], self.people[1][1]
        invitation = record_intent(self.round.pk, a.pk, 'INVITE', 'early-team', {'partner_id': b.pk})
        with transaction.atomic():
            team = invite(self.round.pk, a.pk, b.pk, 'early-team')
            attach_pass_evidence(self.round.pk, team.pk, invitation.receipt_id)
        early = record_intent(self.round.pk, b.pk, 'CONFIRM', 'early-confirm', {'team_id': team.pk})
        with patch('orders.grab_admission.timezone') as admission_clock:
            admission_clock.now.return_value = self.round.opened_at + timedelta(milliseconds=500)
            with transaction.atomic(), self.assertRaisesMessage(ValueError, '可见时间'):
                confirm(self.round.pk, team.pk, b.pk)
        remaining = (self.round.opened_at + timedelta(seconds=3.1) - timezone.now()).total_seconds()
        if remaining > 0:
            sleep(remaining)
        valid = record_intent(self.round.pk, b.pk, 'CONFIRM', 'valid-confirm', {'team_id': team.pk})
        with transaction.atomic():
            confirm(self.round.pk, team.pk, b.pk)
            attach_pass_evidence(self.round.pk, team.pk, valid.receipt_id)
        self.assertEqual(rank(self.round, [team.pk]).winner_id, team.pk)
        member = team.members.get(account=b)
        member.receipt_pass_evidence_id = OrderGrabReceiptProbe.objects.get(pk=early.receipt_id).pass_evidence.pk
        member.save(update_fields=['receipt_pass_evidence'])
        with self.assertRaisesMessage(ValueError, '尚未向该陪玩开放'):
            rank(self.round, [team.pk])


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated stores')
@override_settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True)
class BoundRankingStoreTest(TransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    reset_sequences = True

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_same_receipt_ids_are_ranked_from_each_store(self):
        rows = []
        for store, tier in ((resolve_store(), 'GOLD'), (xinan_store(), 'BRONZE')):
            with store_context(store):
                _, account, _ = provider(tier, key=store.slug)
                order, round_ = pool_for()
                proof = record_intent(round_.pk, account.pk, 'SINGLE', 'same', {})
                rows.append((store, order.pk, round_.pk, proof.receipt_id, tier))
        self.assertEqual(rows[0][1:4], rows[1][1:4])
        for store, order_id, round_id, receipt_id, tier in rows:
            with store_context(store):
                from orders.models import OrderGrabRound
                round_ = OrderGrabRound.objects.get(pk=round_id)
                result = rank(round_, [receipt_id])
                self.assertEqual((result.status, result.winner_id, result.can_assign),
                                 ('POTENTIAL_WINNER', receipt_id, False))
                self.assertEqual(result.candidates[0].members[0].submitted_pass_tier, tier)
