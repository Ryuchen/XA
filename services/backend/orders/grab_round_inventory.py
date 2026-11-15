"""Collect all observed round intents without treating them as applied commands.

Every receipt writer locks the order before the round. The caller must hold the
same locks for this whole read. This experimental inventory does not establish
a trusted production clock, a durable cutoff watermark, admission at receipt,
command application, or authority to assign or refund an order.
"""
from dataclasses import dataclass

from django.conf import settings
from django.utils import timezone
from stores.db import transaction

from .grab_bound_ranking import BoundRankingPreview, preview
from .grab_receipt_probe import _effective_state, _lock_round
from .grab_round_facts import current_round
from .grab_team_draft import _seats, verified_withdrawal
from .models import OrderGrabReceiptProbe, OrderGrabTeamDraft, Reservation
from .team_rules import required_team_size


@dataclass(frozen=True)
class RoundInventory:
    status: str
    before_receipt_ids: tuple[int, ...]
    after_receipt_ids: tuple[int, ...]
    pending_receipt_ids: tuple[int, ...]
    review_receipt_ids: tuple[int, ...]
    cancellation_receipt_ids: tuple[int, ...]
    unapplied_receipt_ids: tuple[int, ...]
    unresolved_team_ids: tuple[int, ...]
    potential_candidate_ids: tuple[int, ...]
    ranking: BoundRankingPreview | None = None
    review_detail: str = ''
    can_assign: bool = False
    can_cancel: bool = False


def inspect_round(round_id):
    """Read all current-store receipts and teams under the order/round lock.

    Candidate IDs identify a single receipt or a double team, according to
    the purchased team size. They are potential participants only: the
    provisional receipts did not record admission, and team withdrawal has no
    durable command link. A verified cancel intent takes precedence over a
    possible ranking, but does not itself perform or authorize a refund.
    """
    from .grab_clock_contract import production_enabled
    if not (getattr(settings, 'GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY', False) or production_enabled()):
        raise ValueError('登记时钟尚未验证')
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('抢单轮次核对须在门店事务内进行')
    if type(round_id) is not int or round_id <= 0:
        raise ValueError('抢单轮次无效')
    order, round_ = _lock_round(round_id)
    pool = round_.pool
    round_ = current_round(pool, round_.pk, lock=True)
    from .grab_pool_visibility import _validated_delays
    _validated_delays(pool)
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
    if timezone.now() <= round_.deadline:
        raise ValueError('抢单窗口尚未结束')

    # Lock the receipt rows themselves. Joining nullable pass evidence into a
    # locking query is not portable and may lock unrelated account rows too.
    rows = list(OrderGrabReceiptProbe.objects.select_for_update().filter(round=round_).order_by('pk'))
    before, after, pending, review, cancels, unapplied = set(), set(), set(), set(), set(), set()
    for row in rows:
        state = _effective_state(row, getattr(row, 'pass_evidence', None))
        if state == 'BEFORE':
            before.add(row.pk)
            if row.kind == 'BOSS_CANCEL':
                cancels.add(row.pk)
        elif state == 'AFTER':
            after.add(row.pk)
        elif state == 'PENDING':
            pending.add(row.pk)
        else:
            review.add(row.pk)

    potential, team_issues = [], set()
    if pool.required_provider_count == 1:
        seen = set()
        for row in rows:
            if row.pk not in before or row.kind == 'BOSS_CANCEL':
                continue
            if row.kind != 'SINGLE' or row.actor_id in seen:
                review.add(row.pk)
                continue
            seen.add(row.actor_id)
            potential.append(row.pk)
    else:
        # Only a linked, ordered receipt can prove a team's withdrawal.
        teams = list(OrderGrabTeamDraft.objects.select_for_update().filter(round=round_).order_by('pk'))
        attached = set()
        by_id = {row.pk: row for row in rows}
        for team in teams:
            try:
                members = _seats(team)
            except ValueError:
                team_issues.add(team.pk)
                continue
            member_receipts = []
            for member in members.values():
                if not member.receipt_pass_evidence_id:
                    continue
                evidence = member.receipt_pass_evidence
                row = by_id.get(evidence.receipt_id)
                captain = member.account_id == team.captain_id
                expected_kind = 'INVITE' if captain else 'CONFIRM'
                expected_payload = {'partner_id': team.partner_id} if captain else {'team_id': team.pk}
                if (not row or not member.joined or row.actor_id != member.account_id
                        or row.kind != expected_kind or row.payload != expected_payload
                        or (captain and row.request_key != team.request_key)
                        or row.pk not in before or getattr(row, 'pass_evidence', None) != evidence):
                    team_issues.add(team.pk)
                    continue
                member_receipts.append(row.pk)
                attached.add(row.pk)
            if team.status == 'WITHDRAWN':
                withdrawal = by_id.get(team.withdrawal_receipt_id)
                if (withdrawal and withdrawal.pk in before
                        and verified_withdrawal(team, members, withdrawal, team.withdrawn_by_id)):
                    attached.add(withdrawal.pk)
                else:
                    team_issues.add(team.pk)
            elif team.status == 'UNVERIFIED':
                if len(member_receipts) == 2 and all(pk in before for pk in member_receipts):
                    potential.append(team.pk)
                else:
                    team_issues.add(team.pk)
            elif not member_receipts or any(pk not in before for pk in member_receipts):
                team_issues.add(team.pk)
        for row in rows:
            if row.pk not in before or row.kind == 'BOSS_CANCEL':
                continue
            if row.kind in ('INVITE', 'CONFIRM') and row.pk not in attached:
                unapplied.add(row.pk)
            elif row.kind == 'WITHDRAW' and row.pk not in attached:
                unapplied.add(row.pk)
            elif row.kind == 'SINGLE':
                review.add(row.pk)

    ranking, review_detail = None, ''
    if cancels:
        status = 'CANCEL_INTENT'
    elif review:
        status = 'REVIEW'
    elif pending:
        status = 'PENDING'
    elif unapplied or team_issues:
        status = 'WAIT_APPLY'
    elif not potential:
        status = 'WAIT_WATERMARK'
    else:
        try:
            ranking = preview(round_.pk, potential)
        except ValueError as exc:
            # Malformed, premature, duplicated or mismatched proofs are not
            # a reason to select a different person or pretend no one applied.
            status = 'REVIEW'
            review_detail = str(exc)
        else:
            status = 'REVIEW' if ranking.status == 'REVIEW' else 'WAIT_ADMISSION'
    return RoundInventory(status, tuple(sorted(before)), tuple(sorted(after)), tuple(sorted(pending)),
        tuple(sorted(review)), tuple(sorted(cancels)), tuple(sorted(unapplied)), tuple(sorted(team_issues)),
        tuple(sorted(potential)), ranking, review_detail)
