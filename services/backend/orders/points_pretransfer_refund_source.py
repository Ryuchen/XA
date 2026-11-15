"""Historical participant evidence for a refund before the first transfer.

This verifier does not authorize a transfer or create a funding version.
Callers must already hold the order and money locks.
"""
from rest_framework.exceptions import ValidationError

from stores.db import transaction


def verify_participants_locked(posting, *, receipt_id=None):
    from .addon_main_deduction import deductions_at
    from .financial_lock import lock_order_money
    from .models import (Order, OrderOriginalShareTransfer, OrderParticipationSegment,
                         OrderPrestartHandoff, OrderSegmentFinancialSnapshot)
    from .points_negotiation_execution import participant_digest

    order = posting.proposal.case.order
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('退款历史须在订单资金事务内核对')
    if receipt_id is None:
        if not Order.objects.select_for_update().filter(pk=order.pk).exists():
            raise ValidationError('退款历史订单不存在')
        lock_order_money()
    else:
        from users.receipt_usage_ledger import _locked_receipt
        from users.receipt_usage_sources import _require_current_unlocked_reads
        from .models import OrderPointsFundingAllocation
        from django.db.models import Q
        _locked_receipt(receipt_id)
        _require_current_unlocked_reads()
        if not OrderPointsFundingAllocation.objects.filter(
                Q(redemption__order=order) | Q(service_order=order), receipt_id=receipt_id).exists():
            raise ValidationError('退款历史缺少本单到账见证')
    rows = posting.participants
    if (not isinstance(rows, list) or not rows
            or posting.participants_hash != participant_digest(posting.proposal, rows)
            or OrderPrestartHandoff.objects.filter(order=order).exists()):
        raise ValidationError('首次转单前退款的履约快照异常')
    transfers = list(OrderOriginalShareTransfer.objects.filter(
        previous_segment__attribution__order=order).select_related('previous_segment').order_by('created_at', 'pk'))
    if (not transfers or any(not item.created_at or item.created_at <= posting.created_at for item in transfers)
            or len({item.previous_segment_id for item in transfers}) != len(transfers)):
        raise ValidationError('首次转单与退款的先后关系异常')
    outgoing = {item.previous_segment_id: item for item in transfers}
    # A refund can release its affected segments at acceptance, and a later
    # transfer releases the still-serving source. Both were active then.
    from django.db.models import Q
    segments = list(OrderParticipationSegment.objects.filter(attribution__order=order,
        accepted_at__lte=posting.accepted_at, started_at__isnull=False,
        started_at__lte=posting.accepted_at).filter(
            Q(released_at__isnull=True) | Q(released_at__gte=posting.accepted_at)).order_by('pk'))
    snapshots = {item.segment_id: item for item in OrderSegmentFinancialSnapshot.objects.filter(
        segment__in=segments)}
    assignments = {item.pk: item for item in order.providers.filter(
        pk__in=[row.get('assignment_id') for row in rows if isinstance(row, dict)])}
    deductions = deductions_at(order, before=posting.created_at)
    if (len(segments) != len(rows) or len(snapshots) != len(rows)
            or len(assignments) != len(rows) or any(not isinstance(row, dict) for row in rows)):
        raise ValidationError('首次转单前退款的陪玩记录不完整')
    evidence_by_segment = {row.get('segment_id'): row for row in rows}
    if len(evidence_by_segment) != len(rows):
        raise ValidationError('首次转单前退款的陪玩记录重复')
    for segment in segments:
        evidence = evidence_by_segment.get(segment.pk)
        snap = snapshots[segment.pk]
        assignment = assignments.get(evidence.get('assignment_id')) if evidence else None
        event = outgoing.get(segment.pk)
        original = event.original_share_before if event else segment.original_share
        actual = {'snapshot_id': snap.pk, 'segment_id': segment.pk,
            'assignment_id': assignment.pk if assignment else None,
            'account_id': segment.provider_account_id, 'provider_id': assignment.provider_id if assignment else None,
            'slot': segment.slot, 'original_share': original,
            'started_at': segment.started_at.isoformat(), 'incoming_basis': snap.incoming_basis,
            'commission_type': snap.commission_type, 'commission_rate': snap.commission_rate,
            'commission_fixed': snap.commission_fixed, 'fixed_rule_total': snap.fixed_rule_total,
            'commission_source': snap.commission_source, 'income_at_entry': snap.income_at_entry,
            'prior_addon_deduction': deductions.get(segment.provider_account_id, 0),
            'game_commission_snapshot': snap.game_commission_snapshot}
        if (not assignment or assignment.provider_account_id != segment.provider_account_id or actual != evidence):
            raise ValidationError('首次转单前退款的履约快照不一致')
        share = posting.shares.filter(account_id=segment.provider_account_id).first()
        if share and share.original_income != snap.income_at_entry - actual['prior_addon_deduction']:
            raise ValidationError('首次转单前退款的个人收益不一致')
    return rows
