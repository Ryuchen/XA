"""Write a later real refund's releases and immutable round in one transaction."""
from dataclasses import asdict
from contextlib import contextmanager
from contextvars import ContextVar

from django.db.models import Q, Sum
from rest_framework.exceptions import ValidationError
from stores.db import transaction

_posting = ContextVar('points_round_release_posting', default=None)


@contextmanager
def posting_scope(order, posting):
    if not transaction.get_connection().in_atomic_block or posting.proposal.case.order_id != order.pk:
        raise ValidationError('多轮退款暂态不属于当前事务')
    token = _posting.set((transaction.get_connection().alias, order.pk, posting.pk))
    try:
        yield
    finally:
        _posting.reset(token)


def posting_active(order, posting):
    return bool(transaction.get_connection().in_atomic_block and _posting.get() ==
                (transaction.get_connection().alias, order.pk, posting.pk))


def record_locked(order, posting, calculation, before_state):
    with posting_scope(order, posting):
        return _record_locked(order, posting, calculation, before_state)


def _record_locked(order, posting, calculation, before_state):
    from .models import (OrderPointsFundingAllocation, OrderPointsFundingRelease,
        OrderPointsTransferFundingRelease, PointsPostRefundTransferRelease, PointsFollowupTransferRelease,
        OrderAddonFundingRelease, PointsThirdTransferRelease, PointsRefundRoundEvidence)
    from .points_funding_allocation import plan_release, PointsFundingAllocationError
    from .points_refund_round_evidence import record_locked as record_version
    from users.models import PassRefundRepaymentReceipt
    if (not transaction.get_connection().in_atomic_block or posting.proposal.case.order_id != order.pk
            or calculation.get('order_id') != order.pk or calculation.get('case_id') != posting.proposal.case_id
            or calculation.get('shortfall_units') != 0):
        raise ValidationError('多轮退款释放不属于当前事务或承担不足')
    previous, addon, transfer = (before_state['round'], before_state['addon'], before_state['transfer'])
    context = {'prior_round_id': previous.pk, 'prior_round_hash': previous.evidence_hash,
        'previous_addon_id': addon.pk if addon else None, 'previous_addon_hash': addon.evidence_hash if addon else None,
        'previous_transfer_id': transfer.pk if transfer else None,
        'previous_transfer_hash': transfer.evidence_hash if transfer else None,
        'prior_funding': asdict(before_state['funding']), 'allocations': before_state['allocations']}
    if calculation.get('pending_handovers'):
        context['pending_handovers'] = calculation['pending_handovers']
    if calculation.get('provider_recoveries') is not None:
        context['provider_recoveries'] = calculation['provider_recoveries']
    if calculation.get('paid_snapshot') is not None:
        context['paid_snapshot'] = calculation['paid_snapshot']
    if any(calculation.get(key) != value for key, value in context.items()):
        raise ValidationError('多轮退款前版与确认方案不一致')
    funding, planned = calculation.get('funding'), calculation.get('releases')
    if not isinstance(funding, dict) or not isinstance(planned, list):
        raise ValidationError('多轮退款释放计划缺失')
    try:
        parts = plan_release(allocations=context['allocations'], amount=funding['release_units'])
    except (KeyError, PointsFundingAllocationError) as exc:
        raise ValidationError('多轮退款释放计划异常') from exc
    if sorted([[p.allocation_id, p.amount] for p in parts]) != planned:
        raise ValidationError('多轮退款释放计划已变化')
    owner = Q(redemption__order=order) | Q(service_order=order)
    ids = [row[0] for row in context['allocations']]
    allocations = list(OrderPointsFundingAllocation.objects.select_for_update().filter(owner, pk__in=ids).order_by('pk'))
    if [row.pk for row in allocations] != ids:
        raise ValidationError('多轮退款分配不属于本单')
    receipt_ids = {row.receipt_id for row in allocations}
    receipts = list(PassRefundRepaymentReceipt.objects.select_for_update().filter(pk__in=receipt_ids).order_by('pk'))
    if len(receipts) != len(receipt_ids) or any(row.usage_version is None for row in receipts):
        raise ValidationError('多轮退款到账用途账本缺失')
    for allocation, frozen in zip(allocations, context['allocations']):
        actual = sum(model.objects.filter(allocation=allocation).aggregate(total=Sum('amount'))['total'] or 0
            for model in (OrderPointsFundingRelease, OrderPointsTransferFundingRelease,
                PointsPostRefundTransferRelease, PointsFollowupTransferRelease, OrderAddonFundingRelease,
                PointsThirdTransferRelease))
        if [allocation.pk, allocation.amount, actual] != frozen:
            raise ValidationError('多轮退款前的真实到账余额已变化')
    if (PointsRefundRoundEvidence.objects.filter(posting=posting).exists()
            or OrderPointsFundingRelease.objects.filter(posting=posting).exists()):
        raise ValidationError('多轮退款已存在实际释放，请按原请求回读')
    OrderPointsFundingRelease.objects.bulk_create([OrderPointsFundingRelease(posting=posting,
        allocation_id=part.allocation_id, amount=part.amount) for part in parts])
    source = {**context, 'funding': funding, 'releases': planned}
    version = record_version(order, posting, transfer_revision=previous.transfer_revision, previous=previous,
                             latest_transfer_version=transfer, current_release=source)
    from users.receipt_usage_sources import record_round_release_locked, verify_allocations_locked
    touched = set()
    for row in OrderPointsFundingRelease.objects.filter(posting=posting).select_related(
            'allocation__receipt').order_by('allocation__receipt_id', 'pk'):
        record_round_release_locked(row)
        touched.add(row.allocation.receipt_id)
    for receipt_id in sorted(touched):
        verify_allocations_locked(receipt_id)
    from .points_round_receipt_usage import verify_locked
    verify_locked(version)
    return version
