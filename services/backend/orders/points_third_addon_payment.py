"""Bound funding preflight for an add-on payment after a third refund.

This does not authorize payment. The caller must bind the order's authorized
payment scope and verify the current receipt and settlement chain.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
import hashlib
import json

from django.db.models import Sum
from rest_framework.exceptions import ValidationError
from stores.db import transaction

_preflight = ContextVar('points_third_addon_payment_preflight', default=None)


@contextmanager
def preflight_locked(order, intent):
    from .models import (OrderPointsFundingRelease, OrderPointsTransferFundingRelease,
                         PointsPostRefundTransferRelease, PointsFollowupTransferRelease,
                         OrderAddonFundingRelease, PointsRefundRoundEvidence, PointsThirdAddonFundingRevision,
                         PointsThirdTransferRevision, PointsThirdTransferRelease)
    from .points_transfer_refund_access import permits
    from .points_third_refund_settlement_plan import build_locked
    from .points_funding_evidence import verified_order_allocations_locked
    if (not transaction.get_connection().in_atomic_block or not permits(order, phase='refund')
            or intent.order_id != order.pk or order.refund_records.count() < 3
            or order.status != order.Status.IN_SERVICE or order.settled_at
            or order.providers.filter(settled_at__isnull=False).exists()):
        raise ValidationError('第三轮退款后加购付款范围尚未接通')
    third = PointsRefundRoundEvidence.objects.select_for_update().select_related('posting').filter(order=order).first()
    if (not third or not third.posting.termination_processed_at or not intent.segment.started_at
            or intent.segment.released_at or intent.segment.attribution.order_id != order.pk
            or intent.segment.provider_account_id != intent.provider_id
            or not order.providers.filter(provider_account_id=intent.provider_id,
                released_at__isnull=True, settled_at__isnull=True).exists()):
        raise ValidationError('第三轮后加购的当前履约或退款事实待核对')
    if order.refund_records.count() > 3:
        from .points_round_funding import current_locked
        state = current_locked(order)
        funding, previous_round = state['funding'], state['round']
    else:
        _, funding = build_locked(order.pk, funding_only=True, _allow_addons=True)
        previous_round = None
    previous = (PointsThirdAddonFundingRevision.objects.select_for_update().filter(order=order)
                .order_by('-created_at', '-pk').first())
    previous_transfer = PointsThirdTransferRevision.objects.select_for_update().filter(order=order).order_by('-created_at', '-pk').first()
    allocations = verified_order_allocations_locked(order)
    rows = []
    for allocation in allocations:
        released = sum(model.objects.filter(allocation=allocation).aggregate(value=Sum('amount'))['value'] or 0
            for model in (OrderPointsFundingRelease, OrderPointsTransferFundingRelease,
                          PointsPostRefundTransferRelease, PointsFollowupTransferRelease, OrderAddonFundingRelease,
                          PointsThirdTransferRelease))
        if released > allocation.amount:
            raise ValidationError('第三轮后加购付款的到账余额异常')
        rows.append([allocation.pk, allocation.amount, released])
    if sum(amount - released for _, amount, released in rows) != funding.allocated_units:
        raise ValidationError('第三轮后加购付款的补资余额已变化')
    token = _preflight.set((transaction.get_connection().alias, order.pk, intent.pk,
                            third.pk, third.evidence_hash, asdict(funding), rows,
                            previous.pk if previous else None, previous.evidence_hash if previous else None,
                            previous_transfer.pk if previous_transfer else None,
                            previous_transfer.evidence_hash if previous_transfer else None,
                            previous_round.pk if previous_round else None,
                            previous_round.evidence_hash if previous_round else None))
    try:
        yield
    finally:
        _preflight.reset(token)


def record_locked(order, payment):
    """Post the actual payment's funding event in its existing transaction.

    The bound preflight and receipt-local verifier both participate in the
    same transaction; any mismatch rolls the payment back.
    """
    from .models import (PointsRefundRoundEvidence, PointsThirdAddonFundingRevision,
                         OrderAddonFundingReleaseRun, OrderAddonFundingRelease, PointsThirdTransferRevision)
    from .addon_payment import verify_receipt
    from .points_funding_allocation import plan_release, PointsFundingAllocationError
    value = _preflight.get()
    if (not transaction.get_connection().in_atomic_block or not value
            or value[:3] != (transaction.get_connection().alias, order.pk, payment.intent_id)
            or payment.intent.order_id != order.pk):
        raise ValidationError('第三轮后付款缺少同一订单的锁内预核')
    third = PointsRefundRoundEvidence.objects.select_for_update().filter(
        pk=value[3], order=order, evidence_hash=value[4]).select_related('posting').first()
    previous = (PointsThirdAddonFundingRevision.objects.select_for_update().filter(order=order)
                .order_by('-created_at', '-pk').first())
    previous_transfer = PointsThirdTransferRevision.objects.select_for_update().filter(order=order).order_by('-created_at', '-pk').first()
    previous_round = (PointsRefundRoundEvidence.objects.select_for_update().filter(order=order)
        .order_by('-posting__created_at', '-pk').first() if value[11] else None)
    if (not third or not third.posting.termination_processed_at
            or payment.buyer_transaction.created_at <= third.posting.termination_processed_at
            or (previous.pk if previous else None) != value[7]
            or (previous.evidence_hash if previous else None) != value[8]
            or (previous_transfer.pk if previous_transfer else None) != value[9]
            or (previous_transfer.evidence_hash if previous_transfer else None) != value[10]
            or (previous_round.pk if previous_round else None) != value[11]
            or (previous_round.evidence_hash if previous_round else None) != value[12]
            or (previous_round and (not previous_round.posting.termination_processed_at
                                   or payment.buyer_transaction.created_at <= previous_round.created_at))
            or (previous_transfer and payment.buyer_transaction.created_at <= previous_transfer.created_at)
            or (previous and payment.buyer_transaction.created_at <= previous.created_at)
            or OrderAddonFundingReleaseRun.objects.filter(payment=payment).exists()):
        raise ValidationError('第三轮后付款的事件顺序或前版异常')
    verify_receipt(payment)
    if payment.inviter_income or payment.inviter_rate:
        raise ValidationError('加购付款不能产生推荐佣金')
    rows = list(payment.allocations.order_by('pk'))
    amounts = {kind: sum(row.amount for row in rows if row.kind == kind)
               for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
    if (sum(amounts.values()) != payment.intent.amount or amounts['PREPAID'] != payment.principal
            or any(row.refunded_amount for row in rows)):
        raise ValidationError('第三轮后付款的实际支付构成异常')
    before, allocations = value[5], value[6]
    principal = before['net_principal_units'] + amounts['PREPAID']
    provider = before['provider_income_units'] + payment.provider_income
    margin = principal + before['planned_provider_external_units'] - provider - before['inviter_income_units']
    required = max(before['rounding_reserved_units'] - margin, 0)
    available = sum(amount - released for _, amount, released in allocations)
    release = max(available - required, 0)
    after = dict(before, net_coin_units=before['net_coin_units'] + payment.intent.amount,
        net_principal_units=principal, net_gift_units=before['net_gift_units'] + amounts['GIFT'],
        net_compensation_units=before['net_compensation_units'] + amounts['COMPENSATION'],
        provider_income_units=provider, store_income_units=margin + required, required_units=required,
        allocated_units=available, release_units=release, remaining_units=max(required - available, 0))
    if (after['net_coin_units'] != principal + after['net_gift_units'] + after['net_compensation_units']
            or principal + after['planned_provider_external_units'] + required !=
               provider + after['inviter_income_units'] + after['store_income_units']):
        raise ValidationError('第三轮后付款资金不守恒')
    try:
        parts = plan_release(allocations=allocations, amount=release)
    except PointsFundingAllocationError as exc:
        raise ValidationError('第三轮后付款到账释放计划异常') from exc
    with transaction.atomic():
        run = OrderAddonFundingReleaseRun.objects.create(payment=payment, required_amount=required,
            allocated_before=available, released_amount=release, allocation_snapshot=allocations)
        snapshot = {'kind': 'POINTS_THIRD_ADDON_PAYMENT_V1', 'third_id': third.pk,
            'third_hash': third.evidence_hash, 'payment_id': payment.pk,
            'previous_id': previous.pk if previous else None,
            'previous_hash': previous.evidence_hash if previous else None,
            'payment_transaction_id': payment.buyer_transaction_id,
            'sources': [[row.pk, row.lot_id, row.kind, row.amount] for row in rows],
            'before': before, 'after': after, 'allocations': allocations,
            'releases': sorted([[part.allocation_id, part.amount] for part in parts])}
        if previous_transfer:
            snapshot.update(previous_transfer_id=previous_transfer.pk, previous_transfer_hash=previous_transfer.evidence_hash)
        if previous_round:
            snapshot.update(previous_round_id=previous_round.pk, previous_round_hash=previous_round.evidence_hash)
        digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        revision = PointsThirdAddonFundingRevision.objects.create(order=order, third=third,
            payment=payment, run=run, previous=previous, previous_transfer=previous_transfer, previous_round=previous_round,
            snapshot=snapshot, evidence_hash=digest)
        OrderAddonFundingRelease.objects.bulk_create([OrderAddonFundingRelease(
            run=run, allocation_id=part.allocation_id, amount=part.amount) for part in parts])
        # The receipt-local source must verify this version before an actual
        # release event can be appended. A failure rolls the payment back.
        from users.receipt_usage_sources import record_third_addon_release_locked, verify_allocations_locked
        for row in run.releases.select_related('allocation__receipt').order_by('allocation__receipt_id', 'pk'):
            record_third_addon_release_locked(row)
        for receipt_id in sorted(set(run.releases.values_list('allocation__receipt_id', flat=True))):
            verify_allocations_locked(receipt_id)
        from .points_third_addon_receipt_usage import verify_run_locked
        verify_run_locked(run)
        return revision
