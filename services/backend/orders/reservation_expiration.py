"""Expire unconverted reservations after their booked end time."""
from django.db import transaction
from django.utils import timezone

from .models import Reservation
from .reservation_notifier import notify_reservation_after_commit
from .reservation_state_machine import transition_reservation


def expire_locked_reservation(reservation):
    """The caller holds the reservation row lock until commit."""
    if reservation.status not in Reservation.ACTIVE_STATUSES or reservation.end_time > timezone.now():
        return False
    expired = transition_reservation(reservation.pk, Reservation.Status.EXPIRED, reason='预约结束时间已过')
    notify_reservation_after_commit(expired)
    # Use the same recipient-specific persistent notice as other transitions.
    from .reservation_views import _reservation_notice
    preview = f'预约 {expired.reservation_no} 已过期，如需服务请重新预约'
    _reservation_notice(expired, 'customer', '预约已过期', preview)
    _reservation_notice(expired, 'provider', '预约已过期', preview)
    return True


def expire_reservation(reservation_id):
    with transaction.atomic():
        reservation = Reservation.objects.select_for_update().filter(pk=reservation_id).first()
        return bool(reservation and expire_locked_reservation(reservation))


def expire_due_reservations(queryset=None):
    """Scan candidates; each lock rechecks both the deadline and status."""
    candidates = (queryset if queryset is not None else Reservation.objects.all()).filter(
        status__in=Reservation.ACTIVE_STATUSES, end_time__lte=timezone.now(),
    ).values_list('pk', flat=True)
    count = 0
    for reservation_id in list(candidates):
        count += int(expire_reservation(reservation_id))
    return count
