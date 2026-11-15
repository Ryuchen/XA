"""Store-funding rebalance for add-ons paid after an ordinary refund."""
from types import SimpleNamespace

from django.core.exceptions import ValidationError


KIND = 'ordinary_refund_addon_payment_v1'
HANDOVER_KIND = 'ordinary_refund_addon_handover_v1'


def facts(run, *, witness_receipt_id=None):
    from users.receipt_usage_sources import _addon_release_facts, order_facts
    from users.models import PassReceiptUsageState
    from .models import (NegotiationExecution, OrderAddonFundingRelease, OrderAddonFundingReleaseRun,
                         OrderPointsFundingAllocation, OrderPointsFundingRelease,
                         OrderPointsTransferFundingRelease, PointsPostRefundTransferRelease)
    from .ordinary_followup_receipt import _allocation_source
    from .ordinary_refund_receipt_evidence import verified_funding
    from .points_funding_allocation import plan_release

    handover = run.handover
    payment = handover.payment if handover else run.payment
    if not payment or not run.created_at:
        raise ValidationError('退款后加购付款资金版本缺失')
    order = payment.intent.order
    executions = list(NegotiationExecution.objects.filter(refund__order=order,
        refund__created_at__lt=run.created_at).select_related('refund__order').order_by('pk'))
    if not executions:
        from .negotiation_zero import marked_execution_id
        zero_id = marked_execution_id(order)
        if zero_id:
            executions = list(NegotiationExecution.objects.filter(pk=zero_id, zero_order=order,
                termination_processed_at__lt=run.created_at).select_related('zero_order'))
    event_at = handover.accepted_at if handover else payment.buyer_transaction.created_at
    if (len(executions) != 1 or not executions[0].termination_processed_at or not event_at
            or executions[0].termination_processed_at >= event_at
            or event_at >= run.created_at
            or (not handover and payment.refund_records.filter(created_at__lte=run.created_at).exists())
            or (not handover and payment.handovers.filter(created_at__lte=run.created_at).exists())
            or order.providers.filter(settled_at__lte=run.created_at).exists()):
        raise ValidationError('退款后加购付款时序或范围异常')
    if handover:
        from .ordinary_refunded_team_handover import verify as verify_handover
        verify_handover(handover)
    first = executions[0]
    first_event = first.refund.created_at if first.refund_id else first.termination_processed_at
    first_run = OrderAddonFundingReleaseRun.objects.filter(negotiation=first).first()
    if first_run:
        if first_run.created_at >= event_at:
            raise ValidationError('首次退款资金版本时序异常')
        verified_funding(first_run)
    elif first.refund_id and OrderPointsFundingAllocation.objects.filter(service_order=order,
            created_at__lte=first.termination_processed_at).exists():
        raise ValidationError('首次退款的补资版本缺失')
    allocations = list(OrderPointsFundingAllocation.objects.filter(service_order=order,
        created_at__lt=run.created_at).select_related('receipt').order_by('pk'))
    if (not allocations or len(allocations) != len(run.allocation_snapshot)
            or OrderPointsFundingAllocation.objects.filter(service_order=order, created_at=run.created_at).exists()):
        raise ValidationError('加购付款前补资分配异常')
    state = PassReceiptUsageState.objects.filter(receipt_id=witness_receipt_id).first() if witness_receipt_id else None
    if witness_receipt_id:
        from users.receipt_usage_ledger import _locked_receipt
        from users.receipt_usage_sources import _require_current_unlocked_reads
        _locked_receipt(witness_receipt_id)
        _require_current_unlocked_reads()
        if not state:
            raise ValidationError('到账用途账本缺失')
    normalized = []
    for allocation, snapshot in zip(allocations, run.allocation_snapshot):
        _allocation_source(allocation, order, run.created_at)
        if (OrderPointsFundingRelease.objects.filter(allocation=allocation, created_at__lte=run.created_at).exists()
                or OrderPointsTransferFundingRelease.objects.filter(allocation=allocation, revision__created_at__lte=run.created_at).exists()
                or PointsPostRefundTransferRelease.objects.filter(allocation=allocation, revision__created_at__lte=run.created_at).exists()):
            raise ValidationError('退款后加购付款的其他释放来源未接通')
        prior = list(OrderAddonFundingRelease.objects.filter(allocation=allocation,
            run__created_at__lt=run.created_at).exclude(run=run).select_related('allocation', 'run').order_by('pk'))
        def supported(item):
            source = item.run
            if first_run and source.pk == first_run.pk:
                return True
            if (source.refund_id and source.refund.order_id == order.pk
                    and source.refund.created_at < first_event
                    and source.ordinary_prestart_snapshot and source.ordinary_prestart_hash):
                return True
            if (source.handover_id and source.handover.payment.intent.order_id == order.pk
                    and source.handover.accepted_at < first_event
                    and source.ordinary_prestart_snapshot and source.ordinary_prestart_hash):
                return True
            if (source.payment_id and source.payment.intent.order_id == order.pk
                    and source.payment.buyer_transaction.created_at < first_event
                    and source.ordinary_prestart_snapshot and source.ordinary_prestart_hash):
                return True
            evidence = source.ordinary_refund_snapshot
            if not isinstance(evidence, dict):
                return False
            if source.payment_id:
                return (source.payment.intent.order_id == order.pk and evidence.get('kind') == KIND
                        and source.payment.buyer_transaction.created_at < source.created_at)
            if source.refund_id:
                return (source.refund.order_id == order.pk
                        and evidence.get('kind') == 'ordinary_addon_after_main_refund_v1'
                        and source.refund.created_at < source.created_at)
            if source.handover_id:
                return (source.handover.payment.intent.order_id == order.pk
                        and evidence.get('kind') == HANDOVER_KIND
                        and source.handover.accepted_at < source.created_at)
            return False
        if (any(not supported(item) for item in prior)
                or snapshot != [allocation.pk, allocation.amount, sum(item.amount for item in prior), []]):
            raise ValidationError('退款后加购付款前释放不一致')
        if state and allocation.receipt_id == witness_receipt_id:
            for event in [order_facts(allocation)] + [_addon_release_facts(item, witness_receipt_id=witness_receipt_id) for item in prior]:
                kind, key, request, amount, digest = event
                if not state.events.filter(kind=kind, allocation_key=key, request_key=request,
                                           amount=amount, source_digest=digest).exists():
                    raise ValidationError('退款后加购付款的到账用途缺失')
        normalized.append([allocation.pk, allocation.amount, snapshot[2]])
    before = sum(amount - used for _, amount, used in normalized)
    if first.refund_id:
        reconstructed = verified_funding(SimpleNamespace(negotiation_id=first.pk, negotiation=first,
            created_at=run.created_at, allocated_before=before), verify_snapshot=False)
    else:
        from .ordinary_zero_followup_evidence import verified_funding_at
        reconstructed = verified_funding_at(first, boundary=run.created_at, allocated_before=before,
                                             allow_post_addons=True, witness_receipt_id=witness_receipt_id)
    required = reconstructed['combined']['required_units']
    parts = plan_release(allocations=normalized, amount=max(before - required, 0))
    if (run.allocated_before != before or run.required_amount != required
            or run.released_amount != max(before - required, 0)
            or [(item.allocation_id, item.amount) for item in run.releases.order_by('-allocation_id')]
               != [(item.allocation_id, item.amount) for item in parts]):
        raise ValidationError('退款后加购付款补资释放异常')
    value = {'kind': HANDOVER_KIND if handover else KIND, 'payment_id': payment.pk,
             'first_execution_id': first.pk, 'combined': reconstructed['combined'],
             'addon_history': reconstructed['addon_history']}
    if handover:
        value['handover_id'] = handover.pk
    return value


def digest(run, value):
    from .ordinary_refunded_addon_release import _digest
    # Bind the original payment ID in the fact body and the run timestamp.
    return _digest(run, value)


def verify(run, *, witness_receipt_id=None):
    value = facts(run, witness_receipt_id=witness_receipt_id)
    if run.ordinary_refund_snapshot != value or run.ordinary_refund_hash != digest(run, value):
        raise ValidationError('退款后加购付款资金版本不一致')
    return run


def record_locked(order, payment=None, *, handover=None):
    from stores.db import transaction
    from .addon_funding_release import addon_release_totals
    from .models import OrderAddonFundingRelease, OrderAddonFundingReleaseRun
    from .ordinary_refunded_addon_funding import plan_locked
    from .points_funding_allocation import plan_release
    from .points_funding_evidence import verified_order_allocations_locked
    payment = handover.payment if handover else payment
    if not transaction.get_connection().in_atomic_block or not payment or payment.intent.order_id != order.pk:
        raise ValidationError('加购付款补资调整须在订单事务内执行')
    existing = (OrderAddonFundingReleaseRun.objects.filter(handover=handover).first() if handover
                else OrderAddonFundingReleaseRun.objects.filter(payment=payment).first())
    if existing:
        from .negotiation_zero import marked_execution_id
        if marked_execution_id(order):
            from .points_funding_evidence import verified_order_allocations_locked
            allocations = verified_order_allocations_locked(order)
            if not allocations:
                raise ValidationError('已付款加购的到账分配缺失')
            return verify(existing, witness_receipt_id=allocations[0].receipt_id)
        return verify(existing)
    allocations = verified_order_allocations_locked(order)
    if not allocations:
        return None
    prior = addon_release_totals(allocations)
    snapshot = [[item.pk, item.amount, prior[item.pk], []] for item in allocations]
    before = sum(item[1] - item[2] for item in snapshot)
    plan = plan_locked(order.pk, _allow_unconfirmed_addons=True)
    if plan.allocated != before:
        raise ValidationError('加购付款前补资已变化')
    parts = plan_release(allocations=[[item[0], item[1], item[2]] for item in snapshot],
                         amount=plan.releasable)
    run = OrderAddonFundingReleaseRun.objects.create(payment=None if handover else payment, handover=handover,
        required_amount=plan.required,
        allocated_before=before, released_amount=plan.releasable, allocation_snapshot=snapshot)
    OrderAddonFundingRelease.objects.bulk_create([OrderAddonFundingRelease(
        run=run, allocation_id=item.allocation_id, amount=item.amount) for item in parts])
    # Receipt locks are already held by verified_order_allocations_locked.
    # A zero-release prior refund has no usage row to trigger replay, so use
    # an actual allocated receipt as the direct witness for its funding run.
    from .negotiation_zero import marked_execution_id
    witness_receipt_id = allocations[0].receipt_id if marked_execution_id(order) else None
    value = facts(run, witness_receipt_id=witness_receipt_id)
    run.ordinary_refund_snapshot, run.ordinary_refund_hash = value, digest(run, value)
    run.save(update_fields=['ordinary_refund_snapshot', 'ordinary_refund_hash'])
    from users.receipt_usage_sources import record_first_addon_release_locked, verify_allocations_locked
    touched = set()
    for row in run.releases.select_related('allocation__receipt', 'run__payment__intent',
                                           'run__handover__payment__intent').order_by('allocation__receipt_id', 'pk'):
        record_first_addon_release_locked(row)
        touched.add(row.allocation.receipt_id)
    for receipt_id in sorted(touched):
        verify_allocations_locked(receipt_id)
    return verify(run, witness_receipt_id=witness_receipt_id)
