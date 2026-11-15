"""Evidence for ordinary service growth when paid add-ons are retained."""
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def verify_locked(order):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('加购会员实销凭证须在订单事务内核对')
    from orders.models import OrderAddonPayment, OrderOriginalShareTransfer
    from orders.points_service_payment import needs_integrated_processing
    from orders.points_settlement_test import verified_order_settlement_display
    if (order.funding_terms is None or order.is_gift_snapshot or order.source_order_id
            or not order.settled_at or needs_integrated_processing(order)
            or order.status_logs.filter(action='TRANSFER').exists()
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        raise ValidationError('普通加购会员实销范围待核对')
    refunds = list(order.refund_records.order_by('pk'))
    if len(refunds) > 1:
        # A real ordinary follow-up has its own finalized, fully funded
        # settlement verifier; the generic display handles a single refund.
        from orders.models import NegotiationFollowupLink
        from orders.ordinary_addon_followup_settlement import is_followup, verify_finalized_locked
        links = list(NegotiationFollowupLink.objects.select_for_update().filter(order=order)
                     .select_related('previous', 'execution').order_by('pk')[:2])
        if (len(refunds) != 2 or len(links) != 1 or not is_followup(links[0])
                or {links[0].previous.refund_id, links[0].execution.refund_id}
                   != {refund.pk for refund in refunds}):
            raise ValidationError('普通加购追加退款凭证待核对')
        verify_finalized_locked(order, links[0])
    elif verified_order_settlement_display(order) != 'SETTLED':
        raise ValidationError('普通加购主单及收益结算待核对')
    from orders.addon_payment import verify_receipt
    from orders.models import OrderAddonConsumption
    payments = list(OrderAddonPayment.objects.select_for_update().filter(intent__order=order)
                    .select_related('intent', 'buyer_transaction__wallet').order_by('pk'))
    if not payments:
        raise ValidationError('普通加购付款凭证缺失')
    for payment in payments:
        verify_receipt(payment)
        consumption = OrderAddonConsumption.objects.filter(payment=payment).first()
        refunds = list(payment.refund_records.all())
        refunded_units = sum(row.amount for row in refunds)
        from django.db.models import Sum
        returned_principal = payment.allocations.filter(kind='PREPAID').aggregate(
            amount=Sum('refunded_amount'))['amount'] or 0
        if (payment.intent.buyer_id != order.customer_account_id
                or not consumption or consumption.account_id != order.customer_account_id
                or consumption.business_cycle != order.business_cycle
                or consumption.paid_units != payment.intent.amount or consumption.refunded_units != refunded_units
                or consumption.principal_units != payment.principal
                or consumption.refunded_principal_units != returned_principal):
            raise ValidationError('普通加购实销与付款凭证不一致')
    from engagement.models import OrderFundingAllocation
    from django.db.models import Sum
    totals = OrderFundingAllocation.objects.filter(order=order).aggregate(
        paid=Sum('amount'), returned=Sum('refunded_amount'))
    from orders.refunds import refunded_amount
    if totals['paid'] != order.amount or (totals['returned'] or 0) != refunded_amount(order):
        raise ValidationError('普通加购主单付款来源待核对')
    return payments
