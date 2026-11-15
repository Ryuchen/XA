"""Recover each committed reservation invitation without repeating the booking."""
import logging

from django.utils import timezone
from stores.db import transaction

log = logging.getLogger(__name__)


def deliver(kind, source_id):
    from club_accounts.models import ClubAccount
    from orders.models import Reservation, ReservationSeat, ReservationSeatEvent
    from .models import Message, ReservationInviteNoticeDelivery
    from .notifier import notify_messages_after_commit

    if kind == 'single':
        hint = Reservation.objects.filter(pk=source_id).values('pk', 'invitation_notice_intent').first()
        booking_id = source_id
    elif kind == 'seat':
        hint = ReservationSeatEvent.objects.filter(pk=source_id, action='INVITED').values(
            'pk', 'reservation_id', 'invitation_notice_intent').first()
        booking_id = hint['reservation_id'] if hint else None
    else:
        return
    intent = hint['invitation_notice_intent'] if hint else None
    if (not isinstance(intent, dict) or type(intent.get('account_id')) is not int
            or type(intent.get('business_cycle')) is not int or intent['business_cycle'] <= 0):
        return
    try:
        with transaction.atomic():
            account = ClubAccount.objects.select_for_update().filter(pk=intent['account_id']).first()
            booking = Reservation.objects.select_for_update().filter(pk=booking_id).first()
            if not booking:
                return
            if kind == 'single':
                if booking.invitation_notice_intent != intent:
                    return
                source = {'reservation': booking}
                valid_seat = (booking.required_provider_count == 1
                    and booking.provider_account_id == intent['account_id']
                    and booking.provider_business_cycle == intent['business_cycle']
                    and booking.provider_id == intent.get('user_id')
                    and booking.confirmation_deadline and booking.confirmation_deadline > timezone.now())
            else:
                event = ReservationSeatEvent.objects.select_for_update().filter(pk=source_id,
                    reservation=booking, action='INVITED').first()
                if not event or event.invitation_notice_intent != intent:
                    return
                source = {'seat_event': event}
                valid_seat = ReservationSeat.objects.select_for_update().filter(reservation=booking,
                    number=event.seat_number, provider_account_id=intent['account_id'],
                    provider_business_cycle=intent['business_cycle'], provider_id=intent.get('user_id'),
                    status='PENDING', confirmation_deadline__gt=timezone.now()).exists()
                valid_seat = valid_seat and (ReservationSeatEvent.objects.filter(reservation=booking,
                    seat_number=event.seat_number).order_by('-pk').values_list('pk', flat=True).first() == event.pk)
            delivery, created = ReservationInviteNoticeDelivery.objects.get_or_create(**source)
            if not created:
                return
            if (not account or account.account_type != 'PROVIDER' or not account.license_membership_active
                    or account.business_cycle != intent['business_cycle'] or booking.status != 'PENDING'
                    or not valid_seat):
                delivery.skipped_reason = '预约席位已变化或收件人周期已变化'
                delivery.save(update_fields=['skipped_reason'])
                return
            delivery.message = Message.objects.create(recipient_account=account,
                recipient_id=intent.get('user_id'), recipient_business_cycle=intent['business_cycle'],
                type='ORDER', title=str(intent.get('title', ''))[:100], preview=str(intent.get('preview', ''))[:255],
                action_url=f'/pages/reservations/index?id={booking.pk}')
            delivery.save(update_fields=['message'])
            notify_messages_after_commit(account.pk, business_cycle=intent['business_cycle'])
    except Exception:
        log.exception('预约邀请消息待恢复 kind=%s source_id=%s', kind, source_id)


def schedule_single(reservation_id):
    transaction.on_commit(lambda: deliver('single', reservation_id), robust=True)


def schedule_seat(event_id):
    transaction.on_commit(lambda: deliver('seat', event_id), robust=True)
