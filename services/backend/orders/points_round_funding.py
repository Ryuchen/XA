"""Locked funding baseline for a later real points-refund proposal.

This is not an authorization or posting API.  Its caller must already hold
the tenant's order, money and receipt locks, and must check the case scope.
"""
from dataclasses import replace

from django.db.models import Sum
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def current_locked(order):
    from .models import (PointsRefundRoundEvidence, PointsThirdAddonFundingRevision, PointsThirdTransferRevision,
        PointsNegotiationPosting, OrderPointsFundingRelease, OrderPointsTransferFundingRelease,
        PointsPostRefundTransferRelease, PointsFollowupTransferRelease, OrderAddonFundingRelease,
        PointsThirdTransferRelease, OrderAddonRefundMainSegmentDeduction)
    from .points_transfer_refund_access import permits
    from .points_refund_round_evidence import _digest, _facts
    from .points_negotiation_preview import RefundedFunding
    from .points_funding_evidence import verified_order_allocations_locked
    from .points_transfer_segment_math import segment_lines
    if not transaction.get_connection().in_atomic_block or not permits(order, phase='refund'):
        raise ValidationError('多轮退款资金须在本单事务中核对')
    postings = list(PointsNegotiationPosting.objects.select_for_update().filter(
        points_refund__redemption__order=order).select_related('proposal__case', 'points_refund__refund')
        .order_by('created_at', 'pk'))
    versions = list(PointsRefundRoundEvidence.objects.select_for_update().filter(order=order)
        .select_related('posting', 'transfer_revision__transfer', 'latest_transfer_version')
        .order_by('posting__created_at', 'posting_id'))
    if (len(postings) < 3 or len(postings) != order.refund_records.count() or len(versions) != len(postings) - 2
            or [v.posting_id for v in versions] != [p.pk for p in postings[2:]]
            or any(not p.termination_processed_at for p in postings)):
        raise ValidationError('多轮退款的实际结案版本不完整')
    for i, version in enumerate(versions):
        if (version.previous_id != (versions[i - 1].pk if i else None)
                or version.evidence_hash != _digest(version.snapshot)
                or version.snapshot.get('order_id') != order.pk
                or [entry.get('posting_id') for entry in version.snapshot.get('rounds', [])]
                   != [p.pk for p in postings[:i + 3]]):
            raise ValidationError('多轮退款版本前链异常')
    latest = versions[-1]
    source = latest.snapshot['rounds'][-1].get('release_source')
    if not isinstance(source, list) or len(source) != 2 or source[0] != 'current' or not isinstance(source[1], dict):
        raise ValidationError('最近退款缺少实际释放资金计划')
    actual = _facts(order, latest.posting, current_release=source[1])
    if actual.get('rounds') != latest.snapshot['rounds']:
        raise ValidationError('最近退款原支付或逐笔释放事实不一致')
    addon = PointsThirdAddonFundingRevision.objects.select_for_update().filter(order=order).order_by('-created_at', '-pk').first()
    transfer = PointsThirdTransferRevision.objects.select_for_update().filter(order=order).order_by('-created_at', '-pk').first()
    following = [event for event in (addon, transfer) if event and event.created_at > latest.created_at]
    if len(postings) > 3:
        later_addons = PointsThirdAddonFundingRevision.objects.filter(order=order, created_at__gt=latest.created_at)
        later_transfers = PointsThirdTransferRevision.objects.filter(order=order, created_at__gt=latest.created_at)
        if (any(event.previous_round_id != latest.pk for event in later_addons)
                or any(event.previous_round_id != latest.pk for event in later_transfers)):
            raise ValidationError('多轮退款后新增付款、加购退款或转单资金版本尚未接通')
    if addon:
        from .points_third_addon_receipt_usage import verify_run_locked
        verify_run_locked(addon.run)
    if transfer:
        from .points_third_addon_receipt_usage import verify_third_transfer_run_locked
        verify_third_transfer_run_locked(transfer)
    latest_event = max(following, key=lambda event: (event.created_at, event.pk), default=None)
    base = RefundedFunding(**(latest_event.snapshot['after'] if latest_event else source[1]['funding']))
    allocation_rows = []
    for item in verified_order_allocations_locked(order):
        released = sum(model.objects.filter(allocation=item).aggregate(total=Sum('amount'))['total'] or 0
            for model in (OrderPointsFundingRelease, OrderPointsTransferFundingRelease,
                PointsPostRefundTransferRelease, PointsFollowupTransferRelease, OrderAddonFundingRelease,
                PointsThirdTransferRelease))
        if released > item.amount:
            raise ValidationError('多轮退款已有释放超过实际到账')
        allocation_rows.append([item.pk, item.amount, released])
    available = sum(amount - released for _, amount, released in allocation_rows)
    if available > base.required_units:
        raise ValidationError('多轮退款实际到账超过当前缺口')
    funding = replace(base, allocated_units=available, release_units=0,
                      remaining_units=max(base.required_units - available, 0))
    source_transfer = transfer if transfer and (not latest.latest_transfer_version_id
        or transfer.created_at >= latest.latest_transfer_version.created_at) else latest.latest_transfer_version
    anchor = source_transfer if source_transfer else latest.transfer_revision
    facts = anchor.snapshot.get('facts') if isinstance(anchor.snapshot, dict) else None
    if not isinstance(facts, dict):
        raise ValidationError('多轮退款履约资金快照缺失')
    lines = segment_lines(facts, through_transfer_id=anchor.transfer_id)
    income = {row[0]: row[4] for row in lines}
    from .models import OrderParticipationSegment
    from .points_round_closed import from_postings
    closed_map = from_postings(postings)
    closed = set(closed_map)
    for posting in postings:
        chosen = [pk for pk, posting_id in closed_map.items() if posting_id == posting.pk]
        segments = list(OrderParticipationSegment.objects.filter(pk__in=chosen, attribution__order=order))
        if (len(segments) != len(set(chosen)) or
                {row.provider_account_id for row in segments} != set(posting.proposal.affected_accounts) or
                (posting.termination_in_service and any(row.released_at != posting.accepted_at for row in segments))):
            raise ValidationError('已结案退款的履约段事实异常')
        for share in posting.shares.select_related('assignment').order_by('pk'):
            parts = list(share.segment_deductions.values_list('segment_id', 'amount'))
            addon_sum = sum(share.addon_deductions.values_list('amount', flat=True))
            if not parts and share.pending_deduction and not addon_sum:
                participants = posting.participants
                entries = (participants.get('facts', {}).get('segments', []) if isinstance(participants, dict)
                           else participants if isinstance(participants, list) else [])
                selected = set(posting.proposal.affected_segment_ids or [])
                ids = [(item.get('id') if isinstance(participants, dict) else item.get('segment_id'))
                    for item in entries if item.get('account_id') == share.account_id and item.get('started_at')
                    and (not selected or (item.get('id') if isinstance(participants, dict)
                         else item.get('segment_id')) in selected)]
                if len(ids) != 1:
                    raise ValidationError('历史退款本人承担缺少唯一履约段')
                parts = [(ids[0], share.pending_deduction)]
            if sum(amount for _, amount in parts) + addon_sum != share.pending_deduction:
                raise ValidationError('历史退款逐段承担合计异常')
            for segment_id, amount in parts:
                if segment_id not in income or amount > income[segment_id]:
                    raise ValidationError('历史退款超过本人履约段收益')
                income[segment_id] -= amount
    for segment_id, amount in OrderAddonRefundMainSegmentDeduction.objects.filter(
            participant__proposal__payment__intent__order=order,
            participant__proposal__refund__isnull=False).values_list('segment_id', 'amount'):
        if segment_id not in income or amount > income[segment_id]:
            raise ValidationError('加购退款主单承担超过对应履约段')
        income[segment_id] -= amount
    return {'round': latest, 'addon': addon, 'transfer': transfer,
            'funding': funding, 'allocations': allocation_rows, 'segment_income': income,
            'closed_segment_ids': closed}


def verify_active_segment_locked(order, segment):
    """Verify this new add-on's current segment before allowing refund REVIEW."""
    from .points_business_access import _bind, permits
    if (not transaction.get_connection().in_atomic_block or order.refund_records.count() < 3
            or segment.attribution.order_id != order.pk or not segment.started_at or segment.released_at
            or not order.providers.filter(provider_account_id=segment.provider_account_id,
                                          released_at__isnull=True, settled_at__isnull=True).exists()
            or not (any(permits(order, phase=phase) for phase in ('refund', 'payment'))
                    or permits(order, phase='handover', kind='transfer_addon'))):
        raise ValidationError('加购的当前履约段或资金范围异常')
    with _bind(phase='refund', kind='transfer_addon', order_id=order.pk,
               account_id=order.customer_account_id, service_id=order.service_id):
        state = current_locked(order)
    if segment.pk not in state['segment_income'] or segment.pk in state['closed_segment_ids']:
        raise ValidationError('加购履约段不在真实未终止资金版本中')
    return state
