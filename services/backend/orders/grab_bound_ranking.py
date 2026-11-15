"""Conditional ranking of stored round proofs without inventing a commit time.

This preview does not establish admission, command completeness or a cutoff
watermark, and never assigns an order. The caller supplies a subset of local
receipt IDs (single) or team IDs (double), not scores or card tiers.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction

from django.conf import settings
from stores.db import transaction

from .grab_original_facts import original_amount_facts
from .grab_ranking import PASS_RANK, evaluation_facts
from .grab_receipt_probe import _effective_state, _lock_round
from .grab_team_draft import _seats
from .models import OrderGrabReceiptProbe, OrderGrabTeamDraft, Reservation


@dataclass(frozen=True)
class MemberRankSnapshot:
    account_id: int
    submitted_pass_tier: str
    net_original_amount: int
    good_evaluations: int
    valid_evaluations: int


@dataclass(frozen=True)
class CandidateRankSnapshot:
    candidate_id: int
    members: tuple[MemberRankSnapshot, ...]
    priority: tuple[int, int, Fraction]
    completion_lower: datetime
    completion_upper: datetime


@dataclass(frozen=True)
class BoundRankingPreview:
    status: str
    winner_id: int | None = None
    review_candidate_ids: tuple[int, ...] = ()
    review_orders: tuple[tuple[int, tuple[int, ...]], ...] = ()
    candidates: tuple[CandidateRankSnapshot, ...] = ()
    # Previewing a subset cannot prove that the round has been processed.
    can_assign: bool = False
    metrics_as_of: datetime | None = None
    database_identity: str = ''


def snapshot_data(result):
    if not result.metrics_as_of:
        return {}
    return {'as_of': result.metrics_as_of.isoformat(), 'database_identity': result.database_identity,
            'winner_id': result.winner_id,
            'candidates': [{'id': candidate.candidate_id,
                'completion_lower': candidate.completion_lower.isoformat(),
                'completion_upper': candidate.completion_upper.isoformat(),
                'members': [{'account_id': member.account_id, 'submitted_pass_tier': member.submitted_pass_tier,
                    'net_original_amount': member.net_original_amount,
                    'good_evaluations': member.good_evaluations, 'valid_evaluations': member.valid_evaluations}
                    for member in candidate.members]} for candidate in result.candidates]}


def _receipt(receipt, *, round_id, kind, actor_id):
    if (not receipt or receipt.round_id != round_id or receipt.kind != kind or receipt.actor_id != actor_id):
        raise ValueError('抢单成员与登记凭证不匹配')
    from .models import OrderGrabReceiptPassEvidence, OrderGrabSingleAdmission
    evidence = (OrderGrabReceiptPassEvidence.objects.select_for_update()
                .filter(receipt_id=receipt.pk).first())
    if receipt.kind == 'SINGLE':
        admission = OrderGrabSingleAdmission.objects.select_for_update().filter(receipt_id=receipt.pk).first()
        receipt._state.fields_cache['single_admission'] = admission
    if _effective_state(receipt, evidence) != 'BEFORE':
        raise ValueError('抢单登记时间、身份或通行证凭证待核对')
    quantum = timedelta(microseconds=receipt.precision_us)
    bounds = (receipt.lower_at - quantum, receipt.upper_observed_at + quantum)
    from .grab_pool_visibility import initial_visibility
    if receipt.round.simultaneous_visibility:
        from .grab_clock_contract import production_enabled
        if production_enabled():
            from .grab_round_opening import opening_bounds
            visible_lower, visible_upper = opening_bounds(receipt.round)
        else:
            visible_lower = visible_upper = receipt.round.opened_at
        zero_delay = True
    else:
        visibility = initial_visibility(receipt.round.pool_id, actor_id)
        visible_lower = visibility.visible_lower_at or visibility.visible_at
        visible_upper = visibility.visible_at
        zero_delay = visibility.delay_seconds == 0
    if bounds[1] < visible_lower:
        raise ValueError('登记时订单尚未向该陪玩开放')
    crossed = bounds[0] < visible_upper
    if zero_delay and receipt.opening_prepared_observed:
        crossed = False  # Registration saw the committed PREPARED state.
    return evidence.submitted_tier, bounds, crossed


def _single(round_, receipt_id):
    receipt = OrderGrabReceiptProbe.objects.select_for_update().filter(pk=receipt_id, round=round_).first()
    if not receipt:
        raise ValueError('本店抢单登记不存在')
    tier, bounds, uncertain = _receipt(receipt, round_id=round_.pk, kind='SINGLE', actor_id=receipt.actor_id)
    return ((receipt.actor_id, tier),), bounds, uncertain


def _team(round_, team_id):
    team = OrderGrabTeamDraft.objects.select_for_update().filter(pk=team_id, round=round_).first()
    if not team or team.status not in ('UNVERIFIED', 'WITHDRAWN'):
        raise ValueError('双陪队伍不完整或已退出')
    members = _seats(team)
    # _seats locks the current member rows; load their evidence with a current
    # read too, rather than an older transaction-wide consistent snapshot.
    from .models import OrderGrabReceiptPassEvidence
    evidence_ids = [member.receipt_pass_evidence_id for member in members.values()
                    if member.receipt_pass_evidence_id]
    current = {evidence.pk: evidence for evidence in
               OrderGrabReceiptPassEvidence.objects.select_for_update().select_related('receipt')
               .filter(pk__in=evidence_ids).order_by('pk')}
    for member in members.values():
        if member.receipt_pass_evidence_id:
            evidence = current.get(member.receipt_pass_evidence_id)
            if not evidence:
                raise ValueError('双陪成员登记凭证待核对')
            member.receipt_pass_evidence = evidence
    if team.status == 'WITHDRAWN':
        from .grab_team_draft import verified_withdrawal
        withdrawal = (OrderGrabReceiptProbe.objects.select_for_update()
                      .filter(pk=team.withdrawal_receipt_id).first())
        if (not withdrawal or withdrawal.round_phase != 'PARKED'
                or not verified_withdrawal(team, members, withdrawal, withdrawal.actor_id)):
            raise ValueError('首轮队伍退出凭证待核对')
    result, bounds, visibility_uncertain = [], {}, False
    for account_id, kind in ((team.captain_id, 'INVITE'), (team.partner_id, 'CONFIRM')):
        member = members[account_id]
        evidence = member.receipt_pass_evidence
        if not evidence:
            raise ValueError('双陪成员缺少登记凭证')
        receipt = evidence.receipt
        tier, bounds[account_id], uncertain = _receipt(receipt, round_id=round_.pk, kind=kind, actor_id=account_id)
        visibility_uncertain |= uncertain
        if (kind == 'INVITE' and (receipt.request_key != team.request_key
                                  or receipt.payload != {'partner_id': team.partner_id})):
            raise ValueError('双陪邀请凭证与队伍不匹配')
        if kind == 'CONFIRM' and receipt.payload != {'team_id': team.pk}:
            raise ValueError('双陪确认凭证与队伍不匹配')
        result.append((account_id, tier))
    # The confirmation transaction takes this order's row lock and reads the
    # already persisted invitation and member evidence before inserting its
    # own receipt. Their MySQL-generated IDs therefore prove this specific
    # causal dependency even when post-commit clock intervals overlap. This
    # is not a tie-break between different candidates.
    invite = members[team.captain_id].receipt_pass_evidence.receipt
    confirmation = members[team.partner_id].receipt_pass_evidence.receipt
    from stores.db import connection
    causal_order = (connection.vendor == 'mysql' and invite.pk < confirmation.pk
                    and invite.round_id == confirmation.round_id == team.round_id)
    uncertain = (visibility_uncertain or
                 (bounds[team.captain_id][1] >= bounds[team.partner_id][0] and not causal_order))
    return tuple(result), bounds[team.partner_id], uncertain


def preview(round_id, candidate_ids):
    """Compare selected stored proofs with real store-local 30-day metrics."""
    from .grab_clock_contract import production_enabled
    if not (getattr(settings, 'GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY', False) or production_enabled()):
        raise ValueError('登记时钟尚未验证')
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('抢单排序核对须在门店事务内进行')
    if (type(round_id) is not int or round_id <= 0 or not isinstance(candidate_ids, (list, tuple))
            or any(type(pk) is not int or pk <= 0 for pk in candidate_ids)
            or len(set(candidate_ids)) != len(candidate_ids)):
        raise ValueError('抢单候选编号无效或重复')
    order, round_ = _lock_round(round_id)
    pool = round_.pool
    from .grab_pool_visibility import _validated_delays
    from .team_rules import required_team_size
    _validated_delays(pool)
    from .grab_round_facts import current_round
    round_ = current_round(pool, round_.pk, lock=True)
    if (pool.required_provider_count not in (1, 2)
            or pool.required_provider_count != required_team_size(order)
            or order.status != order.Status.PENDING or order.payment_status != order.PaymentStatus.PAID
            or order.provider_id or order.provider_account_id or order.providers.exists()
            or order.requires_boss_selection or order.is_gift_snapshot or order.source_order_id
            or Reservation.objects.filter(order=order).exists()):
        raise ValueError('订单或抢单轮次事实待核对')
    from .direct_payment_models import DirectPaymentIntent
    if DirectPaymentIntent.objects.filter(order_id=order.pk).exists():
        raise ValueError('网页直付订单尚未接入自动抢单')
    from .grab_receipt_probe import _db_clock
    database_now, precision_us, _ = _db_clock()
    if production_enabled():
        from .grab_round_opening import cutoff_bounds
        cutoff_lower, cutoff = cutoff_bounds(round_, 'OPEN')
    else:
        cutoff_lower = cutoff = round_.deadline
    if database_now - timedelta(microseconds=precision_us) <= cutoff:
        raise ValueError('抢单窗口尚未结束')
    parsed, seen, uncertain = [], set(), []
    for candidate_id in candidate_ids:
        people, bounds, ambiguous = (_single(round_, candidate_id) if pool.required_provider_count == 1
                                      else _team(round_, candidate_id))
        ids = {account_id for account_id, _ in people}
        if len(ids) != pool.required_provider_count or seen.intersection(ids):
            raise ValueError('同一陪玩不能重复参加本轮候选')
        seen.update(ids)
        parsed.append((candidate_id, people, bounds))
        if ambiguous:
            uncertain.append(candidate_id)
    if uncertain:
        return BoundRankingPreview('REVIEW', review_candidate_ids=tuple(sorted(uncertain)))
    if not parsed:
        return BoundRankingPreview('NO_SELECTED_PROOFS')
    if production_enabled():
        from .grab_metric_snapshot import read_metrics
        metrics_as_of, identity, original, ratings = read_metrics(seen, order.pk)
    else:
        metrics_as_of, identity = round_.deadline, ''
        original = original_amount_facts(seen, as_of=metrics_as_of)
        ratings = evaluation_facts(seen, as_of=metrics_as_of)
    review = tuple(sorted((account_id, fact.review_order_ids) for account_id, fact in original.items()
                          if fact.amount is None))
    if review:
        return BoundRankingPreview('REVIEW', review_orders=review)
    ranked = []
    for candidate_id, people, bounds in parsed:
        members = tuple(MemberRankSnapshot(account_id, tier, original[account_id].amount,
            ratings[account_id]['good_evaluations'], ratings[account_id]['valid_evaluations'])
            for account_id, tier in people)
        key = (max(PASS_RANK[member.submitted_pass_tier] for member in members),
               max(member.net_original_amount for member in members),
               max((Fraction(member.good_evaluations, member.valid_evaluations)
                    if member.valid_evaluations else Fraction(0)) for member in members))
        ranked.append(CandidateRankSnapshot(candidate_id, members, key, bounds[0], bounds[1]))
    snapshots = tuple(sorted(ranked, key=lambda item: item.candidate_id))
    maximum = max(item.priority for item in ranked)
    leaders = [item for item in ranked if item.priority == maximum]
    if len(leaders) == 1:
        return BoundRankingPreview('POTENTIAL_WINNER', winner_id=leaders[0].candidate_id, candidates=snapshots,
                                   metrics_as_of=metrics_as_of, database_identity=identity)
    possible = [item for item in leaders if not any(item.candidate_id != other.candidate_id
                and other.completion_upper < item.completion_lower for other in leaders)]
    if len(possible) == 1:
        return BoundRankingPreview('POTENTIAL_WINNER', winner_id=possible[0].candidate_id, candidates=snapshots,
                                   metrics_as_of=metrics_as_of, database_identity=identity)
    return BoundRankingPreview('REVIEW', review_candidate_ids=tuple(sorted(item.candidate_id for item in possible)),
                               candidates=snapshots)
