"""Expire reservations at their saved confirmation or payment deadline."""
import logging

from stores.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Reservation
from .reservation_notifier import notify_reservation_after_commit
from .reservation_state_machine import transition_reservation


def expire_locked_reservation(reservation):
    """The caller holds the reservation row lock until commit."""
    from .reservation_deadlines import due
    from django.conf import settings
    recovery = (getattr(settings, 'RESERVATION_EXPIRY_NOTICE_RECOVERY_ENABLED', False)
                and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))
    if not due(reservation, now=timezone.now()):
        return False
    if reservation.required_provider_count == 2 and reservation.status == Reservation.Status.PENDING:
        from .reservation_team import refresh_pending
        expired = refresh_pending(reservation, now=timezone.now())
        reservation.refresh_from_db()
        notify_reservation_after_commit(reservation)
        if not expired:
            if not recovery:
                from .reservation_views import _reservation_notice
                confirmed = reservation.seats.filter(status='CONFIRMED').exists()
                preview = ('已有陪玩确认，请重新选择空出的席位' if confirmed
                           else '有陪玩确认超时，请查看预约并重新选择空出的席位')
                _reservation_notice(reservation, 'customer', '预约席位已超时', preview)
            return False
        if not recovery:
            from .reservation_views import _reservation_notice
            _reservation_notice(reservation, 'customer', '预约已过期', f'预约 {reservation.reservation_no} 已过期')
            _reservation_notice(reservation, 'provider', '预约已过期', f'预约 {reservation.reservation_no} 已过期')
        return True
    reason = ('预约确认超时' if reservation.status == Reservation.Status.PENDING and reservation.confirmation_deadline
              else '预约付款超时' if reservation.status == Reservation.Status.CONFIRMED and reservation.payment_deadline
              else '预约结束时间已过')
    expired = transition_reservation(reservation.pk, Reservation.Status.EXPIRED, reason=reason)
    notify_reservation_after_commit(expired)
    # Use the same recipient-specific persistent notice as other transitions.
    if not recovery:
        from .reservation_views import _reservation_notice
        preview = f'预约 {expired.reservation_no} 已过期，如需服务请重新预约'
        _reservation_notice(expired, 'customer', '预约已过期', preview)
        _reservation_notice(expired, 'provider', '预约已过期', preview)
    return True


def expire_reservation(reservation_id):
    from django.conf import settings
    info = Reservation.objects.filter(pk=reservation_id).values('required_provider_count', 'provider_account_id').first()
    members = []
    if info and info['required_provider_count'] == 2:
        from .reservation_team import _snapshot_members
        members = _snapshot_members(reservation_id)
    elif info and info['provider_account_id']:
        from club_accounts.models import ClubAccount
        members = list(ClubAccount.objects.filter(pk=info['provider_account_id']))
    with transaction.atomic():
        if (info and info['required_provider_count'] != 2 and info['provider_account_id']
                and not members and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)):
            return False
        if members and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
            from club_accounts.provider_lifecycle import ProviderLifecycleError, lock_provider_operations
            try:
                lock_provider_operations(members)
            except ProviderLifecycleError:
                return False  # Preserve the reservation for a later retry or review.
        if members and info['required_provider_count'] == 2:
            from .reservation_team import _lock_profiles
            _lock_profiles(members)
        reservation = Reservation.objects.select_for_update().filter(pk=reservation_id).first()
        if reservation and (not info or reservation.required_provider_count != info['required_provider_count']):
            return False
        if reservation and reservation.required_provider_count == 2:
            # Read the actual locked seats after the booking lock. A prior
            # consistent snapshot may predate a concurrent replacement, whose
            # new provider lifecycle/profile locks we do not hold.
            current = set(reservation.seats.select_for_update().filter(
                provider_account__isnull=False).values_list('provider_account_id', flat=True))
            if current != {row.pk for row in members}:
                return False  # a concurrent replacement won; retry on the next scan
        elif reservation and reservation.provider_account_id != info['provider_account_id']:
            return False
        return bool(reservation and expire_locked_reservation(reservation))


def expire_due_reservations(queryset=None):
    """Scan candidates; each lock rechecks both the deadline and status."""
    now = timezone.now()
    candidates = (queryset if queryset is not None else Reservation.objects.all()).filter(
        Q(status=Reservation.Status.PENDING, confirmation_deadline__lte=now)
        | Q(status=Reservation.Status.PENDING, confirmation_deadline__isnull=True, end_time__lte=now)
        | Q(status=Reservation.Status.CONFIRMED, payment_deadline__lte=now)
        | Q(status=Reservation.Status.CONFIRMED, payment_deadline__isnull=True, end_time__lte=now)
    ).values_list('pk', flat=True)
    count = 0
    for reservation_id in list(candidates):
        try:
            # Each reservation has its own real transaction and rechecks its
            # deadline under lock. A broken record must not strand later slots.
            count += int(expire_reservation(reservation_id))
        except Exception:
            logging.getLogger(__name__).exception(
                '预约到期处理失败，等待下次逐店扫描 reservation_id=%s', reservation_id)
    return count
