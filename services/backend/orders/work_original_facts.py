"""Read the independently captured specified-single original-price slice."""
from collections import defaultdict

from .models import (Order, OrderAddonPayment, OrderProvider, OrderStatusLog, OrderTransferSeatEvent,
                     OrderWorkOriginalShare, OrderWorkOriginalTransfer, OrderWorkStint)
from wallet.models import Transaction


def verified_amounts(account_ids, *, since, as_of):
    order_ids = set(OrderWorkOriginalShare.objects.filter(stint__account_id__in=account_ids,
        accepted_at__lte=as_of).values_list('stint__order_id', flat=True))
    values, recognized = defaultdict(int), set()
    from .direct_payment_models import DirectPaymentIntent
    from .points_service_payment import needs_integrated_processing
    from .grab_refunded_facts import verified_coin_refund
    for order in Order.objects.filter(pk__in=order_ids):
        if (order.is_gift_snapshot or order.escort_mode != Order.EscortMode.SINGLE or order.source_order_id
                or order.required_provider_count != 1 or not order.unit_price_snapshot or not order.game_rounds
                or order.original_amount != order.unit_price_snapshot * order.game_rounds
                or not order.grabbed_at or order.original_amount < order.amount or order.original_amount % 10
                or DirectPaymentIntent.objects.filter(order=order).exists() or needs_integrated_processing(order)
                or OrderAddonPayment.objects.filter(intent__order=order).exists()
                or Order.objects.filter(source_order=order, is_gift_snapshot=False, created_at__lte=as_of).exists()):
            continue
        payment = list(Transaction.objects.filter(order=order, tx_type='PAY', status='SUCCESS').select_related('wallet')[:2])
        if len(payment) != 1:
            continue
        payment = payment[0]
        if (payment.wallet.account_id != order.customer_account_id or payment.wallet.user_id != order.customer_id
                or payment.amount != -order.amount or payment.balance_after != payment.balance_before + payment.amount
                or payment.created_at > order.grabbed_at or payment.created_at > as_of
                or payment.reversal_of_id or payment.reversals.filter(status='SUCCESS').exists()):
            continue
        refunded = order.refund_records.exists()
        if refunded:
            refund = verified_coin_refund(order, as_of=as_of)
            if not refund or not refund.is_full:
                continue
        elif order.payment_status != Order.PaymentStatus.PAID or order.refunded_at:
            continue
        stints = list(OrderWorkStint.objects.filter(order=order).order_by('pk'))
        shares = list(OrderWorkOriginalShare.objects.filter(stint__order=order).select_related('stint'))
        if not stints or len(stints) != len(shares):
            continue
        by_stint = {row.stint_id: row for row in shares}
        if len(by_stint) != len(stints) or set(by_stint) != {row.pk for row in stints}:
            continue
        initial = [row for row in stints if row.incoming_id is None]
        transfers = list(OrderWorkOriginalTransfer.objects.filter(previous__stint__order=order)
                         .select_related('event', 'event__seat', 'previous__stint', 'incoming__stint'))
        if (len(initial) != 1 or len(transfers) != len(stints) - 1 or len(transfers) > 2
                or by_stint[initial[0].pk].accepted_at != order.grabbed_at
                or not OrderStatusLog.objects.filter(order=order, action=OrderStatusLog.Action.ASSIGN,
                    from_status=Order.Status.PENDING, to_status=Order.Status.GRABBED,
                    operator_account_id=order.customer_account_id, reason='老板指定陪玩').exists()
                or sum(row.amount for row in shares) != order.original_amount
                or any(row.amount % 10 or row.accepted_at > as_of for row in shares)):
            continue
        outgoing = {row.previous_id: row for row in transfers}
        incoming = {row.incoming_id: row for row in transfers}
        if len(outgoing) != len(transfers) or len(incoming) != len(transfers):
            continue
        initial_share = by_stint[initial[0].pk]
        initial_value = outgoing[initial_share.pk].original_before if initial_share.pk in outgoing else initial_share.amount
        if initial_value != order.original_amount:
            continue
        ordered = sorted(transfers, key=lambda row: row.event.sequence)
        cursor = initial_share
        for index, transfer in enumerate(ordered, start=1):
            if (transfer.previous_id != cursor.pk or transfer.event.sequence != index
                    or transfer.event.seat.initial_account_id != initial[0].account_id
                    or transfer.event.seat.transfer_count != len(ordered)
                    or (index > 1 and transfer.event.seat_id != ordered[0].event.seat_id)):
                valid = False
                break
            cursor = transfer.incoming
        else:
            valid = True
        if (not valid or order.provider_account_id != cursor.stint.account_id
                or not OrderProvider.objects.filter(order=order, provider_account_id=initial[0].account_id).exists()
                or (ordered and ordered[-1].event.seat.current_account_id != cursor.stint.account_id)):
            continue
        if OrderTransferSeatEvent.objects.filter(seat__order=order).count() != len(ordered):
            continue
        for transfer in transfers:
            previous, new, event = transfer.previous, transfer.incoming, transfer.event
            next_value = outgoing[new.pk].original_before if new.pk in outgoing else new.amount
            if (previous.stint_id not in by_stint or new.stint_id not in by_stint
                    or previous.stint.order_id != order.pk or new.stint.order_id != order.pk
                    or event.seat.order_id != order.pk or new.stint.incoming_id != event.pk
                    or event.old_account_id != previous.stint.account_id or event.new_account_id != new.stint.account_id
                    or previous.stint.released_at != transfer.created_at or new.accepted_at != transfer.created_at
                    or event.created_at > transfer.created_at
                    or transfer.created_at > as_of or transfer.retained != previous.amount
                    or transfer.transferred != next_value or transfer.original_before != transfer.retained + transfer.transferred
                    or not transfer.operator_id or transfer.operator.account_type != 'STAFF'
                    or len(transfer.agreement.strip()) < 3
                    or (not previous.stint.started_at and transfer.retained)):
                valid = False
                break
        if not valid or set(incoming) != {by_stint[row.pk].pk for row in stints if row.incoming_id}:
            continue
        for share in shares:
            stint = share.stint
            if (stint.started_at and (stint.started_at < share.accepted_at
                    or (stint.released_at and stint.released_at < stint.started_at))):
                valid = False
                break
        if not valid:
            continue
        for share in shares:
            stint = share.stint
            if stint.account_id not in account_ids:
                continue
            recognized.add((order.pk, stint.account_id))
            if (stint.started_at and stint.started_at <= as_of and since <= share.accepted_at <= as_of):
                values[stint.account_id] += 0 if refunded else share.amount
    return values, recognized
