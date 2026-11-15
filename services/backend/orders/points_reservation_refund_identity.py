"""Verify the original two-seat reservation for points refund and settlement."""

from rest_framework.exceptions import ValidationError

from .models import (OrderParticipationSegment, OrderPrestartHandoff, Reservation, ReservationSeat,
                     ReservationSeatEvent, ReservationStatusLog)


def verify_dual_reservation(order):
    """The converted booking and its confirmed seats must still identify the order."""
    booking = Reservation.objects.filter(order=order, required_provider_count=2).first()
    if booking is None:
        return
    if (booking.status != Reservation.Status.CONVERTED or booking.customer_account_id != order.customer_account_id
            or booking.customer_id != order.customer_id or booking.service_id != order.service_id
            or order.required_provider_count != 2 or not booking.confirmed_at or not booking.converted_at
            or booking.confirmed_at > booking.converted_at or not order.in_service_at
            or booking.converted_at > order.in_service_at):
        raise ValidationError('双陪预约转单身份或时间待核对')
    logs = list(ReservationStatusLog.objects.filter(reservation=booking, action='CONVERT',
        from_status='CONFIRMED', to_status='CONVERTED'))
    if len(logs) != 1 or logs[0].created_at < booking.converted_at:
        raise ValidationError('双陪预约转单记录待核对')
    seats = list(booking.seats.order_by('number'))
    handoff = OrderPrestartHandoff.objects.filter(order=order).order_by('-pk').first()
    if handoff:
        # The reservation confirms the original people, not the people who
        # replaced them later. Replay real handoffs without editing the booking.
        from .points_prestart_release import capture
        selected_ids = capture(order, handoff)['segments']
        segments = list(OrderParticipationSegment.objects.filter(attribution__order=order).order_by('slot', 'pk'))
        chains = {slot: [row for row in segments if row.slot == slot] for slot in (1, 2)}
        if (any(not chain or len(chain) > 3 for chain in chains.values())
                or len(segments) != sum(map(len, chains.values()))):
            raise ValidationError('双陪预约改派席位或次数待核对')
        originals = [chains[slot][0] for slot in (1, 2)]
        selected = [row for row in segments if row.pk in selected_ids]
        current = list(order.providers.filter(provider_account_id__in=[row.provider_account_id for row in selected])
            .values_list('provider_account_id', 'provider_business_cycle'))
        if (len(selected) != 2 or len(current) != 2
                or sorted(current) != sorted((row.provider_account_id, row.provider_business_cycle) for row in selected)):
            raise ValidationError('双陪预约改派后履约人员周期待核对')
        original_accounts = [row.provider_account_id for row in originals]
        original_cycles = {row.provider_account_id: row.provider_business_cycle for row in originals}
        assignments = list(order.providers.filter(provider_account_id__in=original_accounts)
            .order_by('provider_account_id').values_list('provider_account_id', 'provider_id'))
        if (len(set(original_accounts)) != 2 or any(not row.provider_business_cycle for row in originals)
                or any(original_cycles.get(seat.provider_account_id) != seat.provider_business_cycle for seat in seats)):
            raise ValidationError('双陪预约原履约人员周期待核对')
    else:
        assignments = list(order.providers.order_by('provider_account_id').values_list('provider_account_id', 'provider_id'))
    if (len(seats) != 2 or [seat.number for seat in seats] != [1, 2]
            or any(seat.status != ReservationSeat.Status.CONFIRMED or not seat.provider_account_id
                   or not seat.provider_id or not seat.confirmed_at or not seat.confirmation_deadline
                   or seat.confirmed_at >= seat.confirmation_deadline
                   or seat.confirmed_at > booking.confirmed_at for seat in seats)
            or len({seat.provider_account_id for seat in seats}) != 2
            or sorted((seat.provider_account_id, seat.provider_id) for seat in seats) != assignments):
        raise ValidationError('双陪预约席位与订单履约人员待核对')
    for seat in seats:
        if not ReservationSeatEvent.objects.filter(reservation=booking, seat_number=seat.number,
                action='CONFIRMED', provider_account_id=seat.provider_account_id,
                actor_account_id=seat.provider_account_id, occurred_at=seat.confirmed_at).exists():
            raise ValidationError('双陪预约本人确认记录待核对')
