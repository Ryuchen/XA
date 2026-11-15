"""Recipients captured only when both reservation seats are confirmed."""
from django.conf import settings


def enabled():
    return (getattr(settings, 'RESERVATION_TEAM_CONFIRMED_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))


def intents(booking):
    from orders.models import ReservationSeatEvent
    seats = list(booking.seats.select_for_update().order_by('number'))
    if len(seats) != 2 or [s.number for s in seats] != [1, 2] or any(s.status != 'CONFIRMED' for s in seats):
        raise ValueError('双陪确认通知缺少已确认席位')
    result = []
    for seat in seats:
        event = ReservationSeatEvent.objects.filter(reservation=booking, seat_number=seat.number).order_by('-pk').first()
        if (not event or event.action != 'CONFIRMED' or event.provider_account_id != seat.provider_account_id
                or event.actor_account_id != seat.provider_account_id or event.occurred_at != seat.confirmed_at):
            raise ValueError('双陪确认通知缺少本人确认事实')
        result.append({'account_id': seat.provider_account_id, 'business_cycle': seat.provider_business_cycle,
            'user_id': seat.provider_id, 'account_type': 'PROVIDER', 'seat_number': seat.number,
            'seat_event_id': event.pk, 'title': '双陪预约已确认',
            'preview': '双方已确认预约，请查看预约详情和服务安排。',
            'action_url': f'/pages/reservations/index?id={booking.pk}'})
    return result
