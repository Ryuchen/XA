"""Transaction-scoped funding version for a real post-third add-on refund."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
import hashlib
import json

from django.db.models import Sum
from rest_framework.exceptions import ValidationError
from stores.db import transaction

_scope = ContextVar('points_third_addon_refund_preflight', default=None)


def active(order, payment):
    value = _scope.get()
    return bool(transaction.get_connection().in_atomic_block and value
                and value[:3] == (transaction.get_connection().alias, order.pk, payment.pk))


@contextmanager
def preflight_locked(order, payment):
    from .models import (PointsRefundRoundEvidence, PointsThirdAddonFundingRevision, OrderPointsFundingRelease,
        OrderPointsTransferFundingRelease, PointsPostRefundTransferRelease, PointsFollowupTransferRelease,
        OrderAddonFundingRelease, PointsThirdTransferRevision, PointsThirdTransferRelease)
    from .points_transfer_refund_access import permits
    from .points_third_refund_settlement_plan import build_locked
    from .points_funding_evidence import verified_order_allocations_locked
    if (not transaction.get_connection().in_atomic_block or not permits(order, phase='refund')
            or payment.intent.order_id != order.pk or order.refund_records.count() < 3 or order.settled_at
            or order.providers.filter(settled_at__isnull=False).exists()):
        raise ValidationError('第三轮后加购退款资金范围尚未接通')
    third = PointsRefundRoundEvidence.objects.select_for_update().filter(order=order).first()
    if not third or not third.posting.termination_processed_at:
        raise ValidationError('第三轮退款真实资金版本缺失')
    if order.refund_records.count() > 3:
        from .points_round_funding import current_locked
        state = current_locked(order)
        funding, previous_round = state['funding'], state['round']
    else:
        _, funding = build_locked(order.pk, funding_only=True, _allow_addons=True)
        previous_round = None
    previous = PointsThirdAddonFundingRevision.objects.select_for_update().filter(order=order).order_by('-created_at', '-pk').first()
    previous_transfer = PointsThirdTransferRevision.objects.select_for_update().filter(order=order).order_by('-created_at', '-pk').first()
    allocations = verified_order_allocations_locked(order)
    rows = []
    for allocation in allocations:
        released = sum(model.objects.filter(allocation=allocation).aggregate(value=Sum('amount'))['value'] or 0
            for model in (OrderPointsFundingRelease, OrderPointsTransferFundingRelease,
                          PointsPostRefundTransferRelease, PointsFollowupTransferRelease, OrderAddonFundingRelease,
                          PointsThirdTransferRelease))
        if released > allocation.amount:
            raise ValidationError('第三轮后加购退款的到账余额异常')
        rows.append([allocation.pk, allocation.amount, released])
    if sum(amount - released for _, amount, released in rows) != funding.allocated_units:
        raise ValidationError('第三轮后加购退款的资金余额已变化')
    token = _scope.set((transaction.get_connection().alias, order.pk, payment.pk, third.pk, third.evidence_hash,
        previous.pk if previous else None, previous.evidence_hash if previous else None, asdict(funding), rows,
        previous_transfer.pk if previous_transfer else None, previous_transfer.evidence_hash if previous_transfer else None,
        previous_round.pk if previous_round else None, previous_round.evidence_hash if previous_round else None))
    try:
        yield
    finally:
        _scope.reset(token)


def record_locked(order, refund):
    from .models import (PointsRefundRoundEvidence, PointsThirdAddonFundingRevision,
                         OrderAddonFundingReleaseRun, OrderAddonFundingRelease, PointsThirdTransferRevision)
    from .points_funding_allocation import plan_release, PointsFundingAllocationError
    from .addon_refund_cross_plan import verify_posted
    scope = _scope.get()
    if (not transaction.get_connection().in_atomic_block or not scope
            or scope[:3] != (transaction.get_connection().alias, order.pk, refund.payment_id)
            or refund.order_id != order.pk):
        raise ValidationError('第三轮后退款缺少同一订单付款的锁内预核')
    third = PointsRefundRoundEvidence.objects.select_for_update().filter(
        pk=scope[3], order=order, evidence_hash=scope[4]).select_related('posting').first()
    previous = PointsThirdAddonFundingRevision.objects.select_for_update().filter(order=order).order_by('-created_at', '-pk').first()
    previous_transfer = PointsThirdTransferRevision.objects.select_for_update().filter(order=order).order_by('-created_at', '-pk').first()
    previous_round = (PointsRefundRoundEvidence.objects.select_for_update().filter(order=order)
        .order_by('-posting__created_at', '-pk').first() if scope[11] else None)
    if (not third or not third.posting.termination_processed_at or refund.created_at <= third.posting.termination_processed_at
            or (previous.pk if previous else None) != scope[5]
            or (previous.evidence_hash if previous else None) != scope[6]
            or (previous_transfer.pk if previous_transfer else None) != scope[9]
            or (previous_transfer.evidence_hash if previous_transfer else None) != scope[10]
            or (previous_round.pk if previous_round else None) != scope[11]
            or (previous_round.evidence_hash if previous_round else None) != scope[12]
            or (previous_round and (not previous_round.posting.termination_processed_at
                                   or refund.created_at <= previous_round.created_at))
            or (previous_transfer and refund.created_at <= previous_transfer.created_at)
            or (previous and refund.created_at <= previous.created_at)
            or OrderAddonFundingReleaseRun.objects.filter(refund=refund).exists()):
        raise ValidationError('第三轮后退款事件时序或前版异常')
    verify_posted(refund)
    proposal = refund.proposal
    sources = proposal.source_plan
    if not isinstance(sources, list):
        raise ValidationError('加购退款的原付款来源缺失')
    allocations = {row.pk: row for row in refund.payment.allocations.all()}
    returned = {'PREPAID': 0, 'GIFT': 0, 'COMPENSATION': 0}
    source_ids = []
    for item in sources:
        if (not isinstance(item, dict) or set(item) != {'allocation_id', 'kind', 'amount_raw'}
                or item.get('kind') not in returned or type(item.get('amount_raw')) is not int
                or item['amount_raw'] <= 0 or item.get('allocation_id') not in allocations
                or allocations[item['allocation_id']].kind != item['kind']):
            raise ValidationError('加购退款的原付款来源异常')
        source_ids.append(item['allocation_id'])
        returned[item['kind']] += item['amount_raw']
    if source_ids != sorted(set(source_ids)) or sum(returned.values()) != refund.amount:
        raise ValidationError('加购退款原付款来源不守恒')
    participants = []
    for person in proposal.participants.order_by('account_id'):
        participants.append({'id': person.pk, 'account_id': person.account_id, 'basis': person.basis,
            'burden': person.burden, 'share_plan': person.share_plan, 'addon_pending': person.addon_pending_deduction,
            'main_pending': person.main_pending_deduction, 'wallet': person.wallet_deduction,
            'deposit': person.deposit_deduction, 'provider_transaction_id': person.provider_transaction_id,
            'earning_deductions': [list(row) for row in refund.earning_deductions.filter(participant=person).order_by('pk')
                .values_list('pk', 'payment_id', 'segment_id', 'amount')],
            'main_deductions': [list(row) for row in person.main_segment_deductions.order_by('pk')
                .values_list('pk', 'segment_id', 'amount')],
            'share_allocations': [list(row) for row in refund.share_allocations.filter(participant=person).order_by('pk')
                .values_list('pk', 'payment_id', 'share_id', 'refund_basis', 'earning_deduction')],
            'deposit_deficit_ids': list(refund.deposit_deficits.filter(account_id=person.account_id).order_by('pk')
                .values_list('pk', flat=True))})
    before, allocated = scope[7], scope[8]
    principal = before['net_principal_units'] - returned['PREPAID']
    gift = before['net_gift_units'] - returned['GIFT']
    compensation = before['net_compensation_units'] - returned['COMPENSATION']
    provider = before['provider_income_units'] - refund.addon_pending_deduction - refund.main_pending_deduction
    external = before['planned_provider_external_units'] + refund.wallet_deduction + refund.deposit_deduction
    reserved = before['rounding_reserved_units'] + refund.rounding_surplus
    margin = principal + external - provider - before['inviter_income_units']
    required = max(reserved - margin, 0)
    available = sum(amount - released for _, amount, released in allocated)
    release = max(available - required, 0)
    after = dict(before, net_coin_units=before['net_coin_units'] - refund.amount,
        net_principal_units=principal, net_gift_units=gift, net_compensation_units=compensation,
        provider_income_units=provider, planned_provider_external_units=external, rounding_reserved_units=reserved,
        store_income_units=margin + required, required_units=required, allocated_units=available,
        release_units=release, remaining_units=max(required - available, 0))
    if (min(principal, gift, compensation, provider, after['net_coin_units']) < 0
            or after['net_coin_units'] != principal + gift + compensation
            or principal + external + required != provider + before['inviter_income_units'] + after['store_income_units']):
        raise ValidationError('第三轮后加购退款资金不守恒')
    try:
        parts = plan_release(allocations=allocated, amount=release)
    except PointsFundingAllocationError as exc:
        raise ValidationError('第三轮后加购退款到账释放异常') from exc
    with transaction.atomic():
        run = OrderAddonFundingReleaseRun.objects.create(refund=refund, required_amount=required,
            allocated_before=available, released_amount=release, allocation_snapshot=allocated)
        snapshot = {'kind': 'POINTS_THIRD_ADDON_REFUND_V1', 'third_id': third.pk, 'third_hash': third.evidence_hash,
            'previous_id': previous.pk if previous else None, 'previous_hash': previous.evidence_hash if previous else None,
            'refund_id': refund.pk, 'payment_id': refund.payment_id, 'buyer_transaction_id': refund.buyer_transaction_id,
            'proposal_id': proposal.pk, 'source_plan': sources, 'participants': participants,
            'rounding_surplus': refund.rounding_surplus, 'rounding_transaction_id': refund.rounding_transaction_id,
            'before': before, 'after': after, 'allocations': allocated,
            'releases': sorted([[part.allocation_id, part.amount] for part in parts])}
        if previous_transfer:
            snapshot.update(previous_transfer_id=previous_transfer.pk, previous_transfer_hash=previous_transfer.evidence_hash)
        if previous_round:
            snapshot.update(previous_round_id=previous_round.pk, previous_round_hash=previous_round.evidence_hash)
        digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        revision = PointsThirdAddonFundingRevision.objects.create(order=order, third=third, refund=refund,
            run=run, previous=previous, previous_transfer=previous_transfer, previous_round=previous_round,
            snapshot=snapshot, evidence_hash=digest)
        OrderAddonFundingRelease.objects.bulk_create([OrderAddonFundingRelease(
            run=run, allocation_id=part.allocation_id, amount=part.amount) for part in parts])
        from users.receipt_usage_sources import record_third_addon_release_locked, verify_allocations_locked
        for row in run.releases.select_related('allocation__receipt').order_by('allocation__receipt_id', 'pk'):
            record_third_addon_release_locked(row)
        for receipt_id in sorted(set(run.releases.values_list('allocation__receipt_id', flat=True))):
            verify_allocations_locked(receipt_id)
        from .points_third_addon_receipt_usage import verify_run_locked
        verify_run_locked(run)
        return revision
