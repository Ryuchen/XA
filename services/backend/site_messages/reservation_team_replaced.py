"""Recover a teammate's notice when a vacant reservation seat is refilled."""
from django.conf import settings
from django.utils import timezone
from stores.db import transaction


def enabled():
    return (getattr(settings, 'RESERVATION_TEAM_REPLACED_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))


def record(event):
    from orders.models import ReservationSeat, ReservationSeatEvent
    from .models import ReservationTeamReplacedNotice
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('预约重选通知必须与重选事件同事务保存')
    seat = (ReservationSeat.objects.select_for_update().filter(reservation_id=event.reservation_id,
        provider_account__isnull=False).exclude(number=event.seat_number).first())
    if not seat:
        return
    prior = ReservationSeatEvent.objects.filter(reservation_id=event.reservation_id,
        seat_number=event.seat_number, pk__lt=event.pk).order_by('-pk').first()
    expected = {'account_id': seat.provider_account_id, 'business_cycle': seat.provider_business_cycle,
        'legacy_user_id': seat.provider_id, 'recipient_seat_number': seat.number,
        'vacancy_event_id': prior.pk if prior and prior.action in ('REJECTED', 'TIMEOUT') else None}
    intent, created = ReservationTeamReplacedNotice.objects.get_or_create(event=event, defaults=expected)
    if not created and any(getattr(intent, key) != value for key, value in expected.items()):
        raise ValueError('双陪重选通知来源不一致')
    transaction.on_commit(lambda pk=intent.pk: deliver(pk), robust=True)


def deliver(intent_id):
    from club_accounts.models import ClubAccount, LegacyAccountMap
    from orders.models import Reservation, ReservationSeat, ReservationSeatEvent
    from .models import Message, ReservationTeamReplacedNotice
    from .notifier import notify_messages_after_commit
    initial = ReservationTeamReplacedNotice.objects.filter(pk=intent_id).values('account_id', 'event_id').first()
    if not initial:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        booking_id = ReservationSeatEvent.objects.filter(pk=initial['event_id']).values_list('reservation_id', flat=True).first()
        booking = Reservation.objects.select_for_update().filter(pk=booking_id).first()
        event = ReservationSeatEvent.objects.filter(pk=initial['event_id'], reservation=booking).first() if booking else None
        intent = ReservationTeamReplacedNotice.objects.select_for_update().filter(pk=intent_id,
            account_id=initial['account_id'], event_id=initial['event_id']).first()
        if not intent or intent.completed_at:
            return
        prior = (ReservationSeatEvent.objects.filter(reservation=booking, seat_number=event.seat_number,
            pk__lt=event.pk).order_by('-pk').first() if event else None)
        later = bool(event and ReservationSeatEvent.objects.filter(reservation=booking,
            seat_number=event.seat_number, pk__gt=event.pk).exists())
        cycle = intent.business_cycle
        recipient = ReservationSeat.objects.filter(reservation=booking, number=intent.recipient_seat_number,
            provider_account_id=intent.account_id, provider_business_cycle=cycle, provider_id=intent.legacy_user_id,
            status__in=['PENDING', 'CONFIRMED']).exists() if booking else False
        invited = bool(event and ReservationSeat.objects.filter(reservation=booking, number=event.seat_number,
            provider_account_id=event.provider_account_id, status='PENDING', confirmation_deadline__gt=timezone.now()).exists())
        mapping = (intent.legacy_user_id is None or LegacyAccountMap.objects.filter(
            account_id=intent.account_id, legacy_user_id=intent.legacy_user_id).exists())
        valid = bool(booking and booking.status == 'PENDING' and booking.required_provider_count == 2
            and booking.start_time > timezone.now() and event and event.action == 'INVITED'
            and event.seat_number in (1, 2) and intent.recipient_seat_number == 3 - event.seat_number
            and event.provider_account_id and event.actor_account_id == booking.customer_account_id
            and event.provider_account_id != intent.account_id and prior and prior.pk == intent.vacancy_event_id
            and prior.action in ('REJECTED', 'TIMEOUT') and not later and invited and recipient and mapping
            and type(cycle) is int and cycle > 0 and account and account.account_type == 'PROVIDER'
            and account.license_membership_active and account.business_cycle == cycle)
        if not valid:
            intent.skipped_reason = '预约席位已变化或收件账号周期不可核验'
        else:
            intent.message = Message.objects.create(recipient_account=account, recipient_id=intent.legacy_user_id,
                recipient_business_cycle=cycle, type='ORDER', title='预约席位已重选',
                preview='另一预约席位已重新邀请陪玩，正在等待对方确认。',
                action_url=f'/pages/reservations/index?id={booking.pk}')
            notify_messages_after_commit(account.pk, business_cycle=cycle)
        intent.completed_at = timezone.now()
        intent.save(update_fields=['message', 'skipped_reason', 'completed_at'])
