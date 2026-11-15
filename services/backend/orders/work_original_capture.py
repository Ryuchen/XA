"""Narrow, money-independent original-price facts for specified single orders."""
from .models import (Order, OrderStatusLog, OrderTransferSeatEvent, OrderWorkOriginalShare,
                     OrderWorkOriginalTransfer)


def initial(order, stint):
    from .models import OrderOriginalAttribution
    if OrderOriginalAttribution.objects.filter(order=order).exists():
        return
    if (order.escort_mode != Order.EscortMode.SINGLE or order.is_gift_snapshot or order.source_order_id
            or order.required_provider_count != 1 or not order.unit_price_snapshot or not order.game_rounds
            or order.original_amount != order.unit_price_snapshot * order.game_rounds
            or order.payment_status != Order.PaymentStatus.PAID or not order.grabbed_at
            or order.provider_account_id != stint.account_id or stint.incoming_id
            or not stint.started_at or stint.started_at < order.grabbed_at
            or type(order.original_amount) is not int or order.original_amount < order.amount
            or order.original_amount % 10 or order.amount <= 0):
        return
    # Only the actual boss-selected assignment has an unambiguous first entry.
    if not OrderStatusLog.objects.filter(order=order, action=OrderStatusLog.Action.ASSIGN,
        from_status=Order.Status.PENDING, to_status=Order.Status.GRABBED,
        operator_account_id=order.customer_account_id, reason='老板指定陪玩').exists():
        return
    if OrderTransferSeatEvent.objects.filter(seat__order=order).exists():
        return
    from .direct_payment_models import DirectPaymentIntent
    from .points_service_payment import needs_integrated_processing
    if DirectPaymentIntent.objects.filter(order=order).exists() or needs_integrated_processing(order):
        return
    OrderWorkOriginalShare.objects.create(stint=stint, accepted_at=order.grabbed_at, amount=order.original_amount)


def transfer(previous, incoming, event, command, operator, *, at):
    prior = OrderWorkOriginalShare.objects.select_for_update().filter(stint=previous).first() if previous else None
    if not prior:
        return
    terms = command.get('original_share')
    if not operator or operator.account_type != 'STAFF' or not isinstance(terms, dict):
        raise ValueError('请填写双方协商的原价份额和依据')
    retained, moved, agreement = terms.get('retained'), terms.get('transferred'), terms.get('agreement')
    if (type(retained) is not int or type(moved) is not int or retained < 0 or moved < 0
            or retained % 10 or moved % 10 or retained + moved != prior.amount
            or not isinstance(agreement, str) or not 3 <= len(agreement.strip()) <= 500):
        raise ValueError('原价份额须为整数店币、合计等于本席原份额并填写依据')
    if previous.started_at is None and retained:
        raise ValueError('原陪玩未开始履约，不能保留原价份额')
    before = prior.amount
    prior.amount = retained
    prior.save(update_fields=['amount'])
    next_share = OrderWorkOriginalShare.objects.create(stint=incoming, accepted_at=at, amount=moved)
    OrderWorkOriginalTransfer.objects.create(event=event, previous=prior, incoming=next_share,
        original_before=before, retained=retained, transferred=moved, agreement=agreement.strip(),
        operator=operator, created_at=at)
