"""Resolve add-on payment, refund and earnings to their formal order."""
from django.db.models import Q


def addon_links(transaction_ids):
    from orders.models import (OrderAddonPayment, OrderAddonRefund, OrderAddonRefundParticipant,
                               OrderAddonSettlement, OrderSegmentAddonSettlement)
    from .models import RefundDepositDeficit

    ids = list(transaction_ids)
    if not ids:
        return {}
    links = {}
    payments = OrderAddonPayment.objects.filter(buyer_transaction_id__in=ids).select_related('intent__order')
    for payment in payments:
        order = payment.intent.order
        links[payment.buyer_transaction_id] = {'order_id': order.pk, 'order_no': order.order_no,
            'payment_id': payment.pk, 'addon_name': payment.intent.addon_name, 'refund_id': None}
    refunds = OrderAddonRefund.objects.filter(Q(buyer_transaction_id__in=ids) | Q(provider_transaction_id__in=ids)) \
        .select_related('order', 'payment__intent')
    for refund in refunds:
        link = {'order_id': refund.order_id, 'order_no': refund.order.order_no,
            'payment_id': refund.payment_id, 'addon_name': refund.payment.intent.addon_name, 'refund_id': refund.pk}
        for tx_id in (refund.buyer_transaction_id, refund.provider_transaction_id):
            if tx_id in ids:
                links[tx_id] = link
    participants = (OrderAddonRefundParticipant.objects.filter(provider_transaction_id__in=ids,
        proposal__refund__isnull=False).select_related('proposal__refund__order', 'proposal__payment__intent'))
    for participant in participants:
        refund = participant.proposal.refund
        links[participant.provider_transaction_id] = {'order_id': refund.order_id, 'order_no': refund.order.order_no,
            'payment_id': refund.payment_id, 'addon_name': participant.proposal.payment.intent.addon_name,
            'refund_id': refund.pk}
    for model in (OrderAddonSettlement, OrderSegmentAddonSettlement):
        settlements = model.objects.filter(income_transaction_id__in=ids).select_related('payment__intent__order')
        for settlement in settlements:
            payment, order = settlement.payment, settlement.payment.intent.order
            links[settlement.income_transaction_id] = {'order_id': order.pk, 'order_no': order.order_no,
                'payment_id': payment.pk, 'addon_name': payment.intent.addon_name, 'refund_id': None}
    deficits = RefundDepositDeficit.objects.filter(source_store_transaction_id__in=ids,
        addon_refund__isnull=False).select_related('addon_refund__order', 'addon_refund__payment__intent')
    for deficit in deficits:
        refund = deficit.addon_refund
        links[deficit.source_store_transaction_id] = {'order_id': refund.order_id, 'order_no': refund.order.order_no,
            'payment_id': refund.payment_id, 'addon_name': refund.payment.intent.addon_name, 'refund_id': refund.pk}
    return links
