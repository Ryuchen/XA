"""Recover committed reservation decision notices without repeating decisions."""
import logging

from stores.db import transaction

log = logging.getLogger(__name__)


def deliver(kind, source_id):
    from club_accounts.models import ClubAccount
    from orders.models import Reservation, ReservationSeatEvent, ReservationStatusLog
    from .models import Message, ReservationStatusNoticeDelivery
    from .notifier import notify_messages_after_commit

    source_model = ReservationStatusLog if kind == 'status' else ReservationSeatEvent if kind == 'seat' else None
    if source_model is None:
        return
    hint = source_model.objects.filter(pk=source_id).values('reservation_id', 'status_notice_intent').first()
    intent = hint['status_notice_intent'] if hint else None
    if (not isinstance(intent, dict) or type(intent.get('account_id')) is not int
            or type(intent.get('business_cycle')) is not int or intent['business_cycle'] <= 0):
        return
    try:
        with transaction.atomic():
            account = ClubAccount.objects.select_for_update().filter(pk=intent['account_id']).first()
            booking = Reservation.objects.select_for_update().filter(pk=hint['reservation_id']).first()
            if not booking:
                return
            event = source_model.objects.select_for_update().filter(pk=source_id, reservation=booking).first()
            if not event or event.status_notice_intent != intent:
                return
            if ((kind == 'status' and (booking.required_provider_count != 1 or event.to_status not in ('CONFIRMED', 'REJECTED')))
                    or (kind == 'seat' and (booking.required_provider_count != 2 or event.action not in ('CONFIRMED', 'REJECTED', 'TIMEOUT')))):
                return
            key = {'status_log': event} if kind == 'status' else {'seat_event': event}
            delivery, created = ReservationStatusNoticeDelivery.objects.get_or_create(**key)
            if not created:
                return
            if kind == 'seat' and event.action == 'TIMEOUT':
                from orders.models import ReservationSeat
                vacant = ReservationSeat.objects.filter(reservation=booking, number=event.seat_number,
                                                         status='VACANT').exists()
                if booking.status != 'PENDING' or not vacant:
                    delivery.skipped_reason = '预约已结束或该席位已重新选择'
                    delivery.save(update_fields=['skipped_reason'])
                    return
            if (not account or account.account_type != 'BOSS' or not account.license_membership_active
                    or account.business_cycle != intent['business_cycle']
                    or booking.customer_account_id != intent['account_id']
                    or booking.customer_business_cycle != intent['business_cycle']
                    or booking.customer_id != intent.get('user_id')):
                delivery.skipped_reason = '原预约老板已离店或账号周期已变化'
                delivery.save(update_fields=['skipped_reason'])
                return
            delivery.message = Message.objects.create(recipient_account=account,
                recipient_id=intent.get('user_id'), recipient_business_cycle=intent['business_cycle'],
                type='ORDER', title=str(intent.get('title', ''))[:100], preview=str(intent.get('preview', ''))[:255],
                action_url=f'/pages/reservation-detail/index?id={booking.pk}')
            delivery.save(update_fields=['message'])
            notify_messages_after_commit(account.pk, business_cycle=intent['business_cycle'])
    except Exception:
        log.exception('预约状态消息待恢复 kind=%s source_id=%s', kind, source_id)


def schedule(kind, source_id):
    transaction.on_commit(lambda: deliver(kind, source_id), robust=True)
