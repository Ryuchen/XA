"""Confirmed reservation commitments and their actual closure evidence."""
from django.db.models import Q
from django.utils import timezone


def reservation_clearance(profile, *, now=None):
    from orders.models import Reservation, ReservationSeat, ReservationSeatEvent, ReservationStatusLog
    now = now or timezone.now()
    account_id = profile.account_id
    result = {'active': 0, 'timestamps': [], 'needs_review': False, 'has_business': False}
    if not account_id:
        result['needs_review'] = True
        return result
    team_ids = ReservationSeatEvent.objects.filter(action='CONFIRMED', provider_account_id=account_id,
        actor_account_id=account_id).values_list('reservation_id', flat=True)
    current_seat_ids = ReservationSeat.objects.filter(provider_account_id=account_id, status='CONFIRMED').values_list('reservation_id', flat=True)
    reservations = (Reservation.objects.filter(Q(required_provider_count=1, provider_account_id=account_id)
        | Q(required_provider_count=2, pk__in=team_ids)
        | Q(required_provider_count=2, pk__in=current_seat_ids)).distinct().order_by('pk'))
    for booking in reservations:
        if booking.required_provider_count == 1:
            if not booking.confirmed_at:
                if booking.status in ('CONFIRMED', 'CONVERTED'):
                    result['needs_review'] = True
                continue
            confirmed = ReservationStatusLog.objects.filter(reservation=booking, action='CONFIRM',
                from_status='PENDING', to_status='CONFIRMED', operator_account_id=account_id).first()
            if (timezone.is_naive(booking.confirmed_at) or booking.confirmed_at < booking.created_at
                    or booking.confirmed_at > now or not confirmed or confirmed.created_at < booking.confirmed_at
                    or confirmed.created_at > now):
                result['needs_review'] = True
                continue
        else:
            events = list(ReservationSeatEvent.objects.filter(reservation=booking, action='CONFIRMED',
                provider_account_id=account_id, actor_account_id=account_id).order_by('pk'))
            if len(events) != 1:
                result['needs_review'] = True
                continue
            event = events[0]
            seat = ReservationSeat.objects.filter(reservation=booking, number=event.seat_number,
                provider_account_id=account_id, status='CONFIRMED', confirmed_at=event.occurred_at).first()
            if (not seat or event.seat_number not in (1, 2) or timezone.is_naive(event.occurred_at)
                    or event.occurred_at < booking.created_at or event.occurred_at > now):
                result['needs_review'] = True
                continue
        result['has_business'] = True
        if booking.status in ('PENDING', 'CONFIRMED'):
            result['active'] += 1
            continue
        if booking.status not in ('CANCELLED', 'EXPIRED', 'CONVERTED'):
            result['needs_review'] = True
            continue
        closed_at = {'CANCELLED': booking.cancelled_at, 'EXPIRED': booking.expired_at,
                     'CONVERTED': booking.converted_at}[booking.status]
        action = {'CANCELLED': 'CANCEL', 'EXPIRED': 'EXPIRE', 'CONVERTED': 'CONVERT'}[booking.status]
        logs = list(ReservationStatusLog.objects.filter(reservation=booking, action=action,
            to_status=booking.status).order_by('pk'))
        if (not closed_at or timezone.is_naive(closed_at) or closed_at > now or closed_at < booking.created_at
                or len(logs) != 1 or logs[0].created_at < closed_at or logs[0].created_at > now
                or closed_at < (booking.confirmed_at if booking.required_provider_count == 1 else event.occurred_at)):
            result['needs_review'] = True
            continue
        if booking.status == 'CONVERTED':
            if not booking.order_id or not (booking.order.provider_account_id == account_id
                    or booking.order.providers.filter(provider_account_id=account_id).exists()):
                result['needs_review'] = True
                continue
        result['timestamps'].append(logs[0].created_at)
    return result
