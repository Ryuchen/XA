"""Capture and post a real service transfer after three points refunds.

Both functions require the same order/money/receipt transaction.  The public
transfer remains gated until receipt-local replay and settlement are wired.
"""
from dataclasses import asdict
import hashlib
import json

from django.db.models import Sum
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def _digest(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def capture_locked(order):
    from .models import (PointsRefundRoundEvidence, PointsThirdAddonFundingRevision, PointsThirdTransferRevision,
        OrderPointsFundingRelease, OrderPointsTransferFundingRelease, PointsPostRefundTransferRelease,
        PointsFollowupTransferRelease, OrderAddonFundingRelease, PointsThirdTransferRelease)
    from .points_third_refund_settlement_plan import build_locked
    from .points_transfer_refund_evidence import _facts
    from .points_transfer_segment_math import segment_lines
    from .points_funding_evidence import verified_order_allocations_locked
    if (not transaction.get_connection().in_atomic_block or order.refund_records.count() < 3
            or order.status != order.Status.IN_SERVICE or order.settled_at
            or order.providers.filter(settled_at__isnull=False).exists()):
        raise ValidationError('第三轮后转单资金范围尚未接通')
    third = PointsRefundRoundEvidence.objects.select_for_update().select_related(
        'posting', 'transfer_revision__first_posting').filter(order=order).order_by('posting__created_at', 'pk').first()
    if not third or not third.posting.termination_processed_at:
        raise ValidationError('第三轮退款资金版本缺失')
    latest_round = None
    if order.refund_records.count() > 3:
        from .points_round_funding import current_locked
        state = current_locked(order)
        latest_round, funding = state['round'], state['funding']
    else:
        _, funding = build_locked(order.pk, funding_only=True, _allow_addons=True)
    facts = _facts(order)
    if not facts.get('transfers'):
        raise ValidationError('第三轮前转单履约事实缺失')
    lines = segment_lines(facts, through_transfer_id=facts['transfers'][-1]['id'])
    latest_addon = PointsThirdAddonFundingRevision.objects.select_for_update().filter(order=order).order_by('-created_at', '-pk').first()
    latest_transfer = PointsThirdTransferRevision.objects.select_for_update().filter(order=order).order_by('-created_at', '-pk').first()
    allocations = []
    for item in verified_order_allocations_locked(order):
        released = sum(model.objects.filter(allocation=item).aggregate(value=Sum('amount'))['value'] or 0
            for model in (OrderPointsFundingRelease, OrderPointsTransferFundingRelease,
                PointsPostRefundTransferRelease, PointsFollowupTransferRelease,
                OrderAddonFundingRelease, PointsThirdTransferRelease))
        if released > item.amount:
            raise ValidationError('转单前到账释放超过实际分配')
        allocations.append([item.pk, item.amount, released])
    if sum(amount - released for _, amount, released in allocations) != funding.allocated_units:
        raise ValidationError('转单前补资余额已变化')
    result = {'alias': transaction.get_connection().alias, 'order_id': order.pk,
        'third_id': third.pk, 'third_hash': third.evidence_hash,
        'previous_addon_id': latest_addon.pk if latest_addon else None,
        'previous_addon_hash': latest_addon.evidence_hash if latest_addon else None,
        'previous_transfer_id': latest_transfer.pk if latest_transfer else None,
        'previous_transfer_hash': latest_transfer.evidence_hash if latest_transfer else None,
        'before_facts': facts, 'before_income': sum(row[4] for row in lines),
        'before': asdict(funding), 'allocations': allocations}
    if latest_round:
        result.update(previous_round_id=latest_round.pk, previous_round_hash=latest_round.evidence_hash)
    return result


def record_locked(order, transfer, capture):
    from .models import (PointsRefundRoundEvidence, PointsThirdAddonFundingRevision, PointsThirdTransferRevision,
        PointsThirdTransferRelease, OrderAddonPayment, OrderPrestartHandoff)
    from .points_transfer_refund_evidence import _facts
    from .points_transfer_segment_math import segment_lines
    from .points_third_addon_responsibilities import current
    from .points_funding_allocation import plan_release, PointsFundingAllocationError
    if (not transaction.get_connection().in_atomic_block or capture['alias'] != transaction.get_connection().alias
            or capture['order_id'] != order.pk or transfer.previous_segment.attribution.order_id != order.pk
            or transfer.next_segment.attribution.order_id != order.pk or order.refund_records.count() < 3):
        raise ValidationError('第三轮后转单缺少同事务预核')
    third = PointsRefundRoundEvidence.objects.select_for_update().select_related(
        'posting__proposal', 'transfer_revision__first_posting__proposal', 'transfer_revision__second_posting__proposal',
        'transfer_revision__first_posting__points_refund').filter(order=order, pk=capture['third_id'],
            evidence_hash=capture['third_hash']).first()
    addon = PointsThirdAddonFundingRevision.objects.select_for_update().filter(order=order).order_by('-created_at', '-pk').first()
    previous = PointsThirdTransferRevision.objects.select_for_update().filter(order=order).order_by('-created_at', '-pk').first()
    if (not third or not third.posting.termination_processed_at or transfer.created_at <= third.posting.termination_processed_at
            or (addon.pk if addon else None) != capture['previous_addon_id']
            or (addon.evidence_hash if addon else None) != capture['previous_addon_hash']
            or (previous.pk if previous else None) != capture['previous_transfer_id']
            or (previous.evidence_hash if previous else None) != capture['previous_transfer_hash']
            or (addon and transfer.created_at <= addon.created_at)
            or (previous and transfer.created_at <= previous.created_at)
            or PointsThirdTransferRevision.objects.filter(transfer=transfer).exists()):
        raise ValidationError('第三轮后转单的事件前版不一致')
    previous_round = None
    if order.refund_records.count() > 3:
        from .models import PointsNegotiationPosting
        previous_round = PointsRefundRoundEvidence.objects.select_for_update().filter(order=order).order_by(
            '-posting__created_at', '-posting_id').first()
        if (not previous_round or previous_round.pk != capture.get('previous_round_id')
                or previous_round.evidence_hash != capture.get('previous_round_hash')
                or transfer.created_at <= previous_round.created_at):
            raise ValidationError('转单前最近退款版本已变化')
        postings = list(PointsNegotiationPosting.objects.select_for_update().filter(
            points_refund__redemption__order=order).select_related('proposal').order_by('created_at', 'pk'))
        if len(postings) != order.refund_records.count() or any(not p.termination_processed_at for p in postings):
            raise ValidationError('转单前退款结案事实不完整')
    else:
        postings = [third.transfer_revision.first_posting, third.transfer_revision.second_posting, third.posting]
    for posting in postings:
        segments = posting.proposal.affected_segment_ids or []
        if (transfer.previous_segment_id in segments or
                (not segments and transfer.previous_segment.provider_account_id in posting.proposal.affected_accounts)):
            raise ValidationError('已确认终止的履约段不能再次转单')
    facts = _facts(order)
    if (not facts.get('transfers') or facts['transfers'][-1]['id'] != transfer.pk
            or {row['id'] for row in facts['transfers'][:-1]} !=
               {row['id'] for row in capture['before_facts']['transfers']}):
        raise ValidationError('转单实际履约事件与预核不一致')
    segments = {row['id']: row for row in facts['segments']}
    from .points_transfer_refund_access import _prestart_slot_counts
    prestart = OrderPrestartHandoff.objects.filter(order=order).order_by('-pk').first()
    counts = _prestart_slot_counts(order, prestart) if prestart else {}
    for event in facts['transfers']:
        source = segments.get(event['previous_segment_id'])
        if not source:
            raise ValidationError('转单所属席位缺失')
        counts[source['slot']] = counts.get(source['slot'], 0) + 1
    if any(count > 2 for count in counts.values()):
        raise ValidationError('同一席位改派和转单次数超过上限')
    lines = segment_lines(facts, through_transfer_id=transfer.pk)
    # The transfer really ended its target segment. Supersede only pending
    # proposals bound to that target before deciding which liabilities remain;
    # all changes roll back together if funding capture or release fails.
    from .points_third_target_invalidation import record_transfer_locked
    record_transfer_locked(order, transfer)
    pending = []
    for payment in OrderAddonPayment.objects.select_for_update().filter(intent__order=order).order_by('pk'):
        if payment.buyer_transaction.created_at >= transfer.created_at:
            raise ValidationError('转单与加购付款的时间顺序异常')
        _, shares = current(payment, lock=True, before=transfer.created_at)
        source = [row for row in shares if row['is_current'] and row['basis'] > 0
                  and row['segment_id'] == transfer.previous_segment_id]
        if source:
            if len(source) != 1 or payment.handovers.filter(status='PENDING').exists():
                raise ValidationError('转出加购责任或待确认方案异常')
            pending.append([payment.pk, source[0]['segment_id'], source[0]['basis']])
    before, allocations = capture['before'], capture['allocations']
    provider = before['provider_income_units'] + sum(row[4] for row in lines) - capture['before_income']
    margin = before['net_principal_units'] + before['planned_provider_external_units'] - provider - before['inviter_income_units']
    required = max(before['rounding_reserved_units'] - margin, 0)
    available = sum(amount - released for _, amount, released in allocations)
    release = max(available - required, 0)
    after = dict(before, provider_income_units=provider, store_income_units=margin + required,
        required_units=required, allocated_units=available, release_units=release,
        remaining_units=max(required - available, 0))
    if provider < 0 or before['net_principal_units'] + before['planned_provider_external_units'] + required != provider + before['inviter_income_units'] + after['store_income_units']:
        raise ValidationError('第三轮后转单资金不守恒')
    try:
        parts = plan_release(allocations=allocations, amount=release)
    except PointsFundingAllocationError as exc:
        raise ValidationError('第三轮后转单到账释放异常') from exc
    snapshot = {'kind': 'POINTS_THIRD_TRANSFER_V1', 'third_id': third.pk, 'third_hash': third.evidence_hash,
        'previous_addon_id': addon.pk if addon else None, 'previous_addon_hash': addon.evidence_hash if addon else None,
        'previous_transfer_id': previous.pk if previous else None, 'previous_transfer_hash': previous.evidence_hash if previous else None,
        'transfer_id': transfer.pk, 'posting_ids': [p.pk for p in postings],
        'before_facts': capture['before_facts'], 'facts': facts, 'before_income': capture['before_income'],
        'segment_income': [[row[0], row[4]] for row in lines],
        'before': before, 'after': after, 'allocations': allocations,
        'releases': sorted([[p.allocation_id, p.amount] for p in parts]), 'pending_handover': pending}
    if previous_round:
        snapshot.update(previous_round_id=previous_round.pk, previous_round_hash=previous_round.evidence_hash)
    version = PointsThirdTransferRevision.objects.create(order=order, transfer=transfer, third=third,
        previous_addon=addon, previous_transfer=previous, previous_round=previous_round,
        snapshot=snapshot, evidence_hash=_digest(snapshot))
    PointsThirdTransferRelease.objects.bulk_create([PointsThirdTransferRelease(revision=version,
        allocation_id=p.allocation_id, amount=p.amount) for p in parts])
    from users.receipt_usage_sources import record_third_transfer_release_locked, verify_allocations_locked
    for row in version.releases.select_related('allocation__receipt').order_by('allocation__receipt_id', 'pk'):
        record_third_transfer_release_locked(row)
    for receipt_id in sorted(set(version.releases.values_list('allocation__receipt_id', flat=True))):
        verify_allocations_locked(receipt_id)
    from .points_third_addon_receipt_usage import verify_third_transfer_run_locked
    verify_third_transfer_run_locked(version)
    return version
