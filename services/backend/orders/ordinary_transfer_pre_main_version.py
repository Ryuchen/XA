"""Event-time ordinary transfer obligations before any main-order refund."""
from dataclasses import asdict, replace

from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as RestValidationError


def facts(order, transfer, boundary, allocated):
    from engagement.funding_wallet import validate_order_funding_sources
    from engagement.models import OrderFundingAllocation, OrderPointAllocation
    from wallet.models import Transaction
    from .addon_funding_history import combined_at
    from .addon_payment import verify_receipt
    from .addon_refund_share_allocation import verify as verify_shares
    from .models import OrderAddonPayment
    from .ordinary_transfer_event_scope import segment_lines_at, transfers_at
    from .points_negotiation_preview import funding_after_refund
    from .refunded_addon_funding import combine_refunded_history

    transfers = transfers_at(order, boundary)
    if (transfer.pk not in {event.pk for event in transfers} or order.refund_records.filter(created_at__lte=boundary).exists()
            or OrderPointAllocation.objects.filter(order=order).exists() or type(allocated) is not int or allocated < 0):
        raise ValidationError('转单前主单资金事件时序异常')
    lines = segment_lines_at(order, boundary, allow_unstarted=True)
    from .ordinary_zero_pretransfer import verify as verify_pretransfer_zero
    zero_execution = verify_pretransfer_zero(order, before=transfer.created_at)
    sources = list(OrderFundingAllocation.objects.filter(order=order).select_related('lot'))
    try:
        validate_order_funding_sources(order, sources)
    except RestValidationError as exc:
        raise ValidationError('转单原付款资金来源异常') from exc
    payments = list(Transaction.objects.filter(order=order, tx_type='PAY', status='SUCCESS').select_related('wallet'))
    if (sum(row.amount for row in sources) != order.amount or len(payments) != 1
            or payments[0].amount != -order.amount or payments[0].wallet.account_id != order.customer_account_id
            or payments[0].wallet.user_id != order.customer_id
            or payments[0].balance_after != payments[0].balance_before + payments[0].amount
            or payments[0].reversal_of_id or payments[0].reversals.exists()):
        raise ValidationError('转单原付款流水异常')
    addons = list(OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__lt=boundary).select_related('intent', 'buyer_transaction'))
    for addon in addons:
        try:
            verify_receipt(addon)
        except RestValidationError as exc:
            raise ValidationError('转单加购付款或退款流水异常') from exc
        handovers = list(addon.handovers.filter(status='ACCEPTED', accepted_at__lt=boundary).order_by('version'))
        if addon.handovers.filter(status='PENDING', created_at__lt=boundary).exists():
            raise ValidationError('转单前加购接替历史尚未接通')
        from .ordinary_transfer_pre_main_addon import verify_handover_at
        for handover in handovers:
            verify_handover_at(addon, handover, boundary)
        for refund in addon.refund_records.filter(created_at__lt=boundary):
            verify_shares(refund)
    paid = {kind: sum(row.amount for row in sources if row.kind == kind)
            for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
    retained = {}
    for line in lines:
        retained[line.account_id] = retained.get(line.account_id, 0) + line.income
    main = funding_after_refund(principal_units=paid['PREPAID'], gift_units=paid['GIFT'],
        compensation_units=paid['COMPENSATION'], returned_prepaid_units=0, returned_gift_units=0,
        returned_compensation_units=0, provider_income_units=sum(retained.values()), pending_deduction_units=0,
        provider_external_units=0,
        inviter_rate=order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0,
        allocated_units=allocated)
    history = combined_at(order, main, boundary, segment_shares=[
        (line.segment_id, line.account_id, line.basis, line.fee, line.income) for line in lines])
    result = combine_refunded_history(main, history, main_retained_by_account=retained) if addons else main
    rounding = sum(order.addon_refunds.filter(created_at__lt=boundary).values_list('rounding_surplus', flat=True))
    extra = max(rounding - result.store_income_units, 0)
    if extra:
        required = result.required_units + extra
        result = replace(result, store_income_units=result.store_income_units + extra, required_units=required,
            release_units=max(allocated - required, 0), remaining_units=max(required - allocated, 0))
    facts = {'lines': [asdict(line) for line in lines], 'funding': asdict(result),
             'addon_history': history.get('addon_history', [])}
    if zero_execution:
        facts['zero_pretransfer_execution_id'] = zero_execution.pk
    return facts
