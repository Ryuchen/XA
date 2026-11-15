"""Recover per-recipient messages from committed reservation events."""
import logging

from django.utils import timezone
from stores.db import transaction

log = logging.getLogger(__name__)


def _deliver_one(log_id, position, intent):
    from club_accounts.models import ClubAccount
    from orders.models import Reservation, ReservationSeat, ReservationSeatEvent, ReservationStatusLog
    from .models import Message, ReservationEventNoticeDelivery
    from .notifier import notify_messages_after_commit

    with transaction.atomic():
        account_id = intent.get('account_id') if isinstance(intent, dict) else None
        account = (ClubAccount.objects.select_for_update().filter(pk=account_id).first()
                   if type(account_id) is int and account_id > 0 else None)
        hint = ReservationStatusLog.objects.filter(pk=log_id).values('reservation_id', 'action').first()
        booking = (Reservation.objects.select_for_update().filter(pk=hint['reservation_id']).first()
                   if hint and hint['action'] == 'CONFIRM' else None)
        event = ReservationStatusLog.objects.select_for_update().filter(pk=log_id).first()
        if (not event or not isinstance(event.notification_intents, list)
                or (event.action, event.to_status) not in (('CANCEL', 'CANCELLED'), ('EXPIRE', 'EXPIRED'), ('CONVERT', 'CONVERTED'), ('CONFIRM', 'CONFIRMED'))
                or position >= len(event.notification_intents) or event.notification_intents[position] != intent):
            return
        delivery, created = ReservationEventNoticeDelivery.objects.get_or_create(status_log=event, position=position)
        if not created:
            return
        cycle = intent.get('business_cycle') if isinstance(intent, dict) else None
        if event.action == 'CONFIRM':
            source = ReservationSeatEvent.objects.filter(pk=intent.get('seat_event_id'), reservation=booking,
                seat_number=intent.get('seat_number'), action='CONFIRMED', provider_account_id=account_id,
                actor_account_id=account_id).first()
            seat = ReservationSeat.objects.filter(reservation=booking, number=intent.get('seat_number'),
                provider_account_id=account_id, provider_business_cycle=cycle, provider_id=intent.get('user_id'),
                status='CONFIRMED').first()
            batch = event.notification_intents
            whole_team_valid = (len(batch) == 2 and all(isinstance(item, dict) for item in batch)
                and {item.get('seat_number') for item in batch} == {1, 2}
                and all(ReservationSeat.objects.filter(reservation=booking, number=item.get('seat_number'),
                    provider_account_id=item.get('account_id'), provider_business_cycle=item.get('business_cycle'),
                    provider_id=item.get('user_id'), status='CONFIRMED').exists() for item in batch))
            if (not booking or not whole_team_valid or booking.required_provider_count != 2 or booking.status != 'CONFIRMED'
                    or not booking.payment_deadline or booking.payment_deadline <= timezone.now()
                    or event.from_status != 'PENDING' or intent.get('account_type') != 'PROVIDER'
                    or not source or not seat or source.occurred_at != seat.confirmed_at
                    or ReservationSeatEvent.objects.filter(reservation=booking, seat_number=seat.number, pk__gt=source.pk).exists()):
                delivery.skipped_reason = '预约确认事实或席位已变化'
                delivery.save(update_fields=['skipped_reason'])
                return
        if (not account or type(cycle) is not int or cycle <= 0 or account.business_cycle != cycle
                or not account.license_membership_active or account.account_type != intent.get('account_type')):
            delivery.skipped_reason = '原收件账号离店、周期变化或来源不可核验'
            delivery.save(update_fields=['skipped_reason'])
            return
        delivery.message = Message.objects.create(recipient_account=account, recipient_id=intent.get('user_id'),
            recipient_business_cycle=cycle, type='ORDER', title=str(intent.get('title', ''))[:100],
            preview=str(intent.get('preview', ''))[:255], action_url=str(intent.get('action_url', ''))[:255],
            related_order_id=intent.get('related_order_id'), assignment_alert=intent.get('assignment_alert') is True)
        delivery.save(update_fields=['message'])
        notify_messages_after_commit(account.pk, business_cycle=cycle,
            assignment_message_id=delivery.message_id if intent.get('assignment_alert') is True else None)


def deliver(log_id):
    from orders.models import ReservationStatusLog
    from .models import ReservationEventNoticeBatch, ReservationEventNoticeDelivery

    event = ReservationStatusLog.objects.filter(pk=log_id).first()
    intents = event.notification_intents if event else None
    if not isinstance(intents, list) or not intents:
        return
    for position, intent in enumerate(intents):
        try:
            _deliver_one(log_id, position, intent)
        except Exception:
            log.exception('预约状态通知待恢复 log_id=%s position=%s', log_id, position)
    try:
        with transaction.atomic():
            event = ReservationStatusLog.objects.select_for_update().filter(pk=log_id).first()
            if not event or event.notification_intents != intents:
                return
            positions = set(ReservationEventNoticeDelivery.objects.filter(status_log=event)
                            .values_list('position', flat=True))
            if positions == set(range(len(intents))):
                batch, _ = ReservationEventNoticeBatch.objects.get_or_create(status_log=event)
                if not batch.completed_at:
                    batch.completed_at = timezone.now()
                    batch.save(update_fields=['completed_at'])
    except Exception:
        log.exception('预约状态通知批次待恢复 log_id=%s', log_id)


def schedule(log_id):
    transaction.on_commit(lambda: deliver(log_id), robust=True)
