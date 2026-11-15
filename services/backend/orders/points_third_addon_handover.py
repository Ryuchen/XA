"""Funding capture for a confirmed add-on handover after the third refund.

The authorized voluntary and transfer handover paths capture and post under
the existing order, funding and receipt locks in one transaction. Receipt
replay and final settlement consume the same recorded event.
"""
from dataclasses import asdict
import hashlib
import json

from django.db.models import Sum
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def _shares(rows):
    return [[row.get('share_id'), row['segment_id'], row['provider_id'], row['basis'],
             row['income'], row['commission_snapshot'], row['is_current']] for row in rows]


def capture_locked(order, payment):
    from .models import (PointsRefundRoundEvidence, PointsThirdAddonFundingRevision,
        OrderPointsFundingRelease, OrderPointsTransferFundingRelease, PointsPostRefundTransferRelease,
        PointsFollowupTransferRelease, OrderAddonFundingRelease, PointsThirdTransferRevision,
        PointsThirdTransferRelease)
    from .points_third_addon_responsibilities import current
    from .points_third_refund_settlement_plan import build_locked
    from .points_funding_evidence import verified_order_allocations_locked
    if (not transaction.get_connection().in_atomic_block or payment.intent.order_id != order.pk
            or order.refund_records.count() < 3 or order.status != order.Status.IN_SERVICE
            or order.settled_at or order.providers.filter(settled_at__isnull=False).exists()):
        raise ValidationError('第三轮后加购接替资金范围尚未接通')
    third = PointsRefundRoundEvidence.objects.select_for_update().select_related('posting').filter(order=order).first()
    if not third or not third.posting.termination_processed_at:
        raise ValidationError('第三轮退款的真实资金版本缺失')
    from .points_business_access import _bind
    with _bind(phase='refund', kind='transfer_addon', order_id=order.pk,
               account_id=order.customer_account_id, service_id=order.service_id):
        if order.refund_records.count() > 3:
            from .points_round_funding import current_locked
            state = current_locked(order)
            funding = state['funding']
            previous_round = state['round']
        else:
            _, funding = build_locked(order.pk, funding_only=True, _allow_addons=True)
            previous_round = None
    previous = PointsThirdAddonFundingRevision.objects.select_for_update().filter(order=order).order_by('-created_at', '-pk').first()
    previous_transfer = PointsThirdTransferRevision.objects.select_for_update().filter(order=order).order_by('-created_at', '-pk').first()
    with _bind(phase='refund', kind='transfer_addon', order_id=order.pk,
               account_id=order.customer_account_id, service_id=order.service_id):
        prior, shares = current(payment, lock=True, with_share_id=True)
        verified_allocations = verified_order_allocations_locked(order)
    allocations = []
    for item in verified_allocations:
        released = sum(model.objects.filter(allocation=item).aggregate(value=Sum('amount'))['value'] or 0
            for model in (OrderPointsFundingRelease, OrderPointsTransferFundingRelease,
                          PointsPostRefundTransferRelease, PointsFollowupTransferRelease, OrderAddonFundingRelease,
                          PointsThirdTransferRelease))
        if released > item.amount:
            raise ValidationError('加购接替到账余额异常')
        allocations.append([item.pk, item.amount, released])
    if sum(amount - released for _, amount, released in allocations) != funding.allocated_units:
        raise ValidationError('加购接替补资余额已变化')
    return {'alias': transaction.get_connection().alias, 'order_id': order.pk, 'payment_id': payment.pk,
        'third_id': third.pk, 'third_hash': third.evidence_hash, 'previous_id': previous.pk if previous else None,
        'previous_hash': previous.evidence_hash if previous else None, 'prior_handover_id': prior.pk if prior else None,
        'previous_transfer_id': previous_transfer.pk if previous_transfer else None,
        'previous_transfer_hash': previous_transfer.evidence_hash if previous_transfer else None,
        'previous_round_id': previous_round.pk if previous_round else None,
        'previous_round_hash': previous_round.evidence_hash if previous_round else None,
        'before_shares': _shares(shares), 'before': asdict(funding), 'allocations': allocations}


def record_locked(order, handover, capture):
    from .models import (PointsRefundRoundEvidence, PointsThirdAddonFundingRevision,
                         OrderAddonFundingReleaseRun, OrderAddonFundingRelease, PointsThirdTransferRevision)
    from .points_funding_allocation import plan_release, PointsFundingAllocationError
    if (not transaction.get_connection().in_atomic_block or capture['alias'] != transaction.get_connection().alias
            or capture['order_id'] != order.pk or capture['payment_id'] != handover.payment_id
            or handover.payment.intent.order_id != order.pk or handover.status != 'ACCEPTED'
            or not handover.accepted_at or handover.previous_id != capture['prior_handover_id']):
        raise ValidationError('加购接替缺少同一事务的真实资金预核')
    third = PointsRefundRoundEvidence.objects.select_for_update().filter(order=order, pk=capture['third_id'],
        evidence_hash=capture['third_hash']).select_related('posting').first()
    previous = PointsThirdAddonFundingRevision.objects.select_for_update().filter(order=order).order_by('-created_at', '-pk').first()
    previous_transfer = PointsThirdTransferRevision.objects.select_for_update().filter(order=order).order_by('-created_at', '-pk').first()
    previous_round = (PointsRefundRoundEvidence.objects.select_for_update().filter(order=order)
                      .order_by('-posting__created_at', '-pk').first() if capture['previous_round_id'] else None)
    if (not third or not third.posting.termination_processed_at
            or handover.accepted_at <= third.posting.termination_processed_at
            or (previous.pk if previous else None) != capture['previous_id']
            or (previous.evidence_hash if previous else None) != capture['previous_hash']
            or (previous_transfer.pk if previous_transfer else None) != capture['previous_transfer_id']
            or (previous_transfer.evidence_hash if previous_transfer else None) != capture['previous_transfer_hash']
            or (previous_transfer and handover.accepted_at <= previous_transfer.created_at)
            or (previous_round.pk if previous_round else None) != capture['previous_round_id']
            or (previous_round.evidence_hash if previous_round else None) != capture['previous_round_hash']
            or (previous_round and (not previous_round.posting.termination_processed_at
                                   or handover.accepted_at <= previous_round.created_at))
            or (previous and handover.accepted_at <= previous.created_at)
            or OrderAddonFundingReleaseRun.objects.filter(handover=handover).exists()):
        raise ValidationError('加购接替资金版本或事件顺序异常')
    decisions = list(handover.decisions.order_by('pk'))
    expected = {(handover.payment.intent.buyer_id, 'BOSS'),
                (handover.source_segment.provider_account_id, 'PROVIDER'),
                (handover.target_segment.provider_account_id, 'PROVIDER')}
    if (len(decisions) != 3 or {(d.account_id, d.role) for d in decisions} != expected
            or any(d.accepted is not True or not d.decided_at or d.decided_at < handover.created_at for d in decisions)
            or max(d.decided_at for d in decisions) != handover.accepted_at):
        raise ValidationError('加购接替缺少真实各方确认')
    after_shares = [[s.pk, s.segment_id, s.segment.provider_account_id, s.basis, s.income,
                     s.commission_snapshot, s.is_current]
                    for s in handover.shares.select_related('segment').order_by('position')]
    before_shares = capture['before_shares']
    from .addon_handover import _income, _target_commission
    current = [s for s in before_shares if s[6]]
    if (len(current) != 1 or current[0][1] != handover.source_segment_id
            or current[0][3] != handover.source_basis_before or handover.retained_basis < 0
            or handover.transferred_basis <= 0
            or handover.retained_basis + handover.transferred_basis != current[0][3]):
        raise ValidationError('加购接替原责任已变化')
    source = current[0]
    expected_shares = [s[1:] for s in before_shares if not s[6]]
    if handover.retained_basis:
        original_income = _income(source[3], source[5])
        retained_income = (_income(handover.retained_basis, source[5]) if source[4] == original_income
                           else source[4] * handover.retained_basis // source[3])
        expected_shares.append([source[1], source[2], handover.retained_basis, retained_income, source[5], False])
    target_snapshot = _target_commission(handover.target_segment, pending_handover=handover)
    expected_shares.append([handover.target_segment_id, handover.target_segment.provider_account_id,
        handover.transferred_basis, _income(handover.transferred_basis, target_snapshot), target_snapshot, True])
    if ([s[1:] for s in after_shares] != expected_shares
            or sum(s[3] for s in before_shares) != sum(s[3] for s in after_shares)):
        raise ValidationError('加购接替份额不守恒')
    before, allocations = capture['before'], capture['allocations']
    provider = before['provider_income_units'] + sum(s[4] for s in after_shares) - sum(s[4] for s in before_shares)
    margin = before['net_principal_units'] + before['planned_provider_external_units'] - provider - before['inviter_income_units']
    required = max(before['rounding_reserved_units'] - margin, 0)
    available = sum(amount - released for _, amount, released in allocations)
    release = max(available - required, 0)
    after = dict(before, provider_income_units=provider, store_income_units=margin + required,
        required_units=required, allocated_units=available, release_units=release,
        remaining_units=max(required - available, 0))
    if provider < 0 or before['net_principal_units'] + before['planned_provider_external_units'] + required != provider + before['inviter_income_units'] + after['store_income_units']:
        raise ValidationError('加购接替资金不守恒')
    try:
        parts = plan_release(allocations=allocations, amount=release)
    except PointsFundingAllocationError as exc:
        raise ValidationError('加购接替到账释放计划异常') from exc
    run = OrderAddonFundingReleaseRun.objects.create(handover=handover, required_amount=required,
        allocated_before=available, released_amount=release, allocation_snapshot=allocations)
    snapshot = {'kind': 'POINTS_THIRD_ADDON_HANDOVER_V1', 'third_id': third.pk, 'third_hash': third.evidence_hash,
        'previous_id': previous.pk if previous else None, 'previous_hash': previous.evidence_hash if previous else None,
        'handover_id': handover.pk, 'payment_id': handover.payment_id, 'accepted_at': handover.accepted_at.isoformat(),
        'decisions': [[d.pk, d.account_id, d.role, d.accepted, d.decided_at.isoformat()] for d in decisions],
        'before_shares': before_shares, 'after_shares': after_shares, 'before': before, 'after': after,
        'allocations': allocations, 'releases': sorted([[p.allocation_id, p.amount] for p in parts])}
    if previous_transfer:
        snapshot.update(previous_transfer_id=previous_transfer.pk, previous_transfer_hash=previous_transfer.evidence_hash)
    if previous_round:
        snapshot.update(previous_round_id=previous_round.pk, previous_round_hash=previous_round.evidence_hash)
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    revision = PointsThirdAddonFundingRevision.objects.create(order=order, third=third, handover=handover,
        run=run, previous=previous, previous_transfer=previous_transfer, previous_round=previous_round,
        snapshot=snapshot, evidence_hash=digest)
    OrderAddonFundingRelease.objects.bulk_create([OrderAddonFundingRelease(run=run, allocation_id=p.allocation_id,
        amount=p.amount) for p in parts])
    from users.receipt_usage_sources import record_third_addon_release_locked, verify_allocations_locked
    from .points_third_addon_receipt_usage import verify_run_locked
    from .points_business_access import _bind
    with _bind(phase='refund', kind='transfer_addon', order_id=order.pk,
               account_id=order.customer_account_id, service_id=order.service_id):
        for row in run.releases.select_related('allocation__receipt').order_by('allocation__receipt_id', 'pk'):
            record_third_addon_release_locked(row)
        for receipt_id in sorted(set(run.releases.values_list('allocation__receipt_id', flat=True))):
            verify_allocations_locked(receipt_id)
        verify_run_locked(run)
    return revision
