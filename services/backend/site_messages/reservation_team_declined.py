"""Recover a teammate's notification when the other reservation seat declines."""
from django.conf import settings
from django.utils import timezone
from stores.db import transaction


def enabled():
    return (getattr(settings, 'RESERVATION_TEAM_DECLINED_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))


def record(event):
    from orders.models import ReservationSeat, ReservationSeatEvent
    from .models import ReservationTeamDeclinedNotice
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('预约拒绝通知必须与本人拒绝事件同事务保存')
    seat = (ReservationSeat.objects.select_for_update().filter(reservation_id=event.reservation_id,
        provider_account__isnull=False).exclude(number=event.seat_number).first())
    if not seat:
        return
    prior = ReservationSeatEvent.objects.filter(reservation_id=event.reservation_id,
        seat_number=event.seat_number, pk__lt=event.pk).order_by('-pk').first()
    expected = {'account_id': seat.provider_account_id, 'business_cycle': seat.provider_business_cycle,
        'legacy_user_id': seat.provider_id, 'recipient_seat_number': seat.number,
        'invitation_event_id': prior.pk if prior and prior.action == 'INVITED' else None}
    intent, created = ReservationTeamDeclinedNotice.objects.get_or_create(event=event, defaults=expected)
    if not created and any(getattr(intent, key) != value for key, value in expected.items()):
        raise ValueError('双陪拒绝通知来源不一致')
    transaction.on_commit(lambda pk=intent.pk: deliver(pk), robust=True)


def deliver(intent_id):
    from club_accounts.models import ClubAccount, LegacyAccountMap
    from orders.models import Reservation, ReservationSeat, ReservationSeatEvent
    from .models import Message, ReservationTeamDeclinedNotice
    from .notifier import notify_messages_after_commit
    initial = ReservationTeamDeclinedNotice.objects.filter(pk=intent_id).values('account_id', 'event_id').first()
    if not initial:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        booking_id = ReservationSeatEvent.objects.filter(pk=initial['event_id']).values_list('reservation_id', flat=True).first()
        booking = Reservation.objects.select_for_update().filter(pk=booking_id).first()
        event = ReservationSeatEvent.objects.filter(pk=initial['event_id'], reservation=booking).first() if booking else None
        intent = ReservationTeamDeclinedNotice.objects.select_for_update().filter(pk=intent_id,
            account_id=initial['account_id'], event_id=initial['event_id']).first()
        if not intent or intent.completed_at:
            return
        prior = (ReservationSeatEvent.objects.filter(reservation=booking, seat_number=event.seat_number,
            pk__lt=event.pk).order_by('-pk').first() if event else None)
        later = bool(event and ReservationSeatEvent.objects.filter(reservation=booking,
            seat_number=event.seat_number, pk__gt=event.pk).exists())
        cycle = intent.business_cycle
        recipient_seat = ReservationSeat.objects.filter(reservation=booking, number=intent.recipient_seat_number,
            provider_account_id=intent.account_id, provider_business_cycle=cycle, provider_id=intent.legacy_user_id,
            status__in=['PENDING', 'CONFIRMED']).exists() if booking else False
        vacant = bool(event and ReservationSeat.objects.filter(reservation=booking, number=event.seat_number,
            status='VACANT').exists())
        mapping = (intent.legacy_user_id is None or LegacyAccountMap.objects.filter(
            account_id=intent.account_id, legacy_user_id=intent.legacy_user_id).exists())
        valid = bool(booking and booking.status == 'PENDING' and booking.required_provider_count == 2
            and booking.start_time > timezone.now() and event and event.action == 'REJECTED'
            and event.seat_number in (1, 2) and intent.recipient_seat_number == 3 - event.seat_number
            and event.provider_account_id and event.actor_account_id == event.provider_account_id
            and event.provider_account_id != intent.account_id and prior and prior.pk == intent.invitation_event_id
            and prior.action == 'INVITED' and prior.provider_account_id == event.provider_account_id
            and not later and vacant and recipient_seat and mapping and type(cycle) is int and cycle > 0
            and account and account.account_type == 'PROVIDER' and account.license_membership_active
            and account.business_cycle == cycle)
        if not valid:
            intent.skipped_reason = '预约席位已变化或收件账号周期不可核验'
        else:
            intent.message = Message.objects.create(recipient_account=account, recipient_id=intent.legacy_user_id,
                recipient_business_cycle=cycle, type='ORDER', title='预约席位有变动',
                preview='另一位陪玩已拒绝预约，请留意预约后续安排。',
                action_url=f'/pages/reservations/index?id={booking.pk}')
            notify_messages_after_commit(account.pk, business_cycle=cycle)
        intent.completed_at = timezone.now()
        intent.save(update_fields=['message', 'skipped_reason', 'completed_at'])
