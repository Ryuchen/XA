"""Verify a settled ordinary transferred service before VIP decisions."""
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def verify_locked(order):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('转单会员实销凭证须在门店事务内核对')
    from orders.models import OrderAddonConsumption, OrderAddonPayment, OrderOriginalShareTransfer, OrderSegmentSettlementManifest
    from orders.points_service_payment import needs_integrated_processing
    from orders.segment_settlement import verify_finalized
    from wallet.models import Transaction
    if (order.is_gift_snapshot or order.source_order_id or order.funding_terms is None
            or not order.settled_at or needs_integrated_processing(order)
            or order.payment_status not in ('PAID', 'PARTIALLY_REFUNDED', 'REFUNDED')
            or order.aftersales.filter(status='OPEN').exists()
            or not OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        raise ValidationError('普通转单会员实销范围待核对')
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(
        order=order, finalized_at=order.settled_at).first()
    if not manifest:
        raise ValidationError('转单逐段结算清单缺失')
    verify_finalized(order, manifest)
    refunds = list(order.refund_records.order_by('pk'))
    from orders.models import OrderTransferNegotiationEvidence, OrdinaryTransferFollowupRound
    initial = OrderTransferNegotiationEvidence.objects.select_for_update().select_related('execution').filter(
        order=order).order_by('pk').first()
    if initial or refunds:
        from orders.negotiation_execution import verify_execution
        from orders.negotiation_termination import verify_termination_evidence
        if not initial:
            raise ValidationError('转单退款结案凭证缺失')
        executions = [initial.execution]
        previous = initial.execution_id
        for round_record in OrdinaryTransferFollowupRound.objects.select_for_update().filter(
                order=order, execution__isnull=False).select_related('execution').order_by('sequence', 'pk'):
            if round_record.previous_id != previous:
                raise ValidationError('转单追加退款顺序待核对')
            executions.append(round_record.execution)
            previous = round_record.execution_id
        if {item.refund_id for item in executions if item.refund_id} != {item.pk for item in refunds}:
            raise ValidationError('转单退款与协商轮次不一致')
        for index, execution in enumerate(executions):
            if not execution.termination_processed_at:
                raise ValidationError('转单退款结案凭证缺失')
            if execution.refund_id:
                verify_execution(execution)
                verify_termination_evidence(execution)
            elif index:
                from orders.ordinary_transfer_followup_zero import verify_termination
                verify_termination(execution)
            else:
                from orders.negotiation_zero import verify_zero_execution, verify_zero_termination
                verify_zero_execution(execution)
                verify_zero_termination(execution)
    payments = list(Transaction.objects.select_for_update().filter(order=order, tx_type='PAY', status='SUCCESS')
                    .select_related('wallet'))
    if (len(payments) != 1 or payments[0].wallet.account_id != order.customer_account_id
            or payments[0].amount != -order.amount or payments[0].reversal_of_id or payments[0].reversals.exists()
            or payments[0].balance_after != payments[0].balance_before + payments[0].amount):
        raise ValidationError('普通转单付款流水待核对')
    from .models import OrderFundingAllocation
    from .funding_wallet import validate_order_funding_sources
    sources = list(OrderFundingAllocation.objects.select_for_update().select_related('lot').filter(order=order))
    validate_order_funding_sources(order, sources)
    if (not sources or sum(item.amount for item in sources) != order.amount
            or sum(item.refunded_amount for item in sources) != sum(item.amount for item in refunds)):
        raise ValidationError('普通转单付款来源待核对')
    addons = list(OrderAddonPayment.objects.select_for_update().filter(intent__order=order)
                  .select_related('intent', 'buyer_transaction__wallet').order_by('pk'))
    terms = manifest.plan if isinstance(manifest.plan, dict) else {}
    funding = terms.get('ordinary_transfer_funding') or terms.get('points_funding') or {}
    recorded = {row.get('payment_id') for row in funding.get('addons', [])}
    recorded.update(row.get('payment_id') for row in funding.get('addon_history', []))
    # A fully returned add-on has no payable segment. Its actual refund IDs
    # remain in the finalized funding plan, even though `addons` is empty.
    refund_ids = funding.get('addon_refund_ids', [])
    if not isinstance(refund_ids, list) or any(type(pk) is not int for pk in refund_ids):
        raise ValidationError('转单加购退款清单待核对')
    refund_ids = set(refund_ids)
    for payment in addons:
        if payment.pk in recorded:
            continue
        refunds = list(payment.refund_records.all())
        if (refunds and {row.pk for row in refunds}.issubset(refund_ids)
                and sum(row.amount for row in refunds) == payment.intent.amount):
            recorded.add(payment.pk)
    if addons and recorded != {item.pk for item in addons}:
        raise ValidationError('转单加购结算清单不完整')
    from orders.addon_payment import verify_receipt
    for payment in addons:
        verify_receipt(payment)
        consumption = OrderAddonConsumption.objects.filter(payment=payment).first()
        addon_refunds = list(payment.refund_records.all())
        from django.db.models import Sum
        refunded_principal = payment.allocations.filter(kind='PREPAID').aggregate(
            total=Sum('refunded_amount'))['total'] or 0
        if (payment.intent.buyer_id != order.customer_account_id
                or not consumption or consumption.account_id != order.customer_account_id
                or consumption.business_cycle != order.business_cycle
                or consumption.paid_units != payment.intent.amount
                or consumption.refunded_units != sum(item.amount for item in addon_refunds)
                or consumption.principal_units != payment.principal
                or consumption.refunded_principal_units != refunded_principal):
            raise ValidationError('转单加购实销与付款凭证不一致')
    if not addons and recorded:
        raise ValidationError('转单加购结算与付款不一致')
    return manifest
