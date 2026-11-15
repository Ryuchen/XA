"""Recover a provider's actual double-reservation seat timeout notice."""
from django.conf import settings
from django.utils import timezone
from stores.db import transaction


def enabled():
    return (getattr(settings, 'RESERVATION_EXPIRY_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))


def record(event, *, cycle, legacy_user_id):
    from orders.models import ReservationSeatEvent
    from .models import ReservationSeatTimeoutNotice
    previous = (ReservationSeatEvent.objects.filter(reservation_id=event.reservation_id,
        seat_number=event.seat_number, pk__lt=event.pk).order_by('-pk').first())
    invited = previous if previous and previous.action == 'INVITED' else None
    expected = {'invitation_event_id': invited.pk if invited else None, 'account_id': event.provider_account_id,
                'business_cycle': cycle, 'legacy_user_id': legacy_user_id}
    intent, created = ReservationSeatTimeoutNotice.objects.get_or_create(event=event, defaults=expected)
    if not created and any(getattr(intent, key) != value for key, value in expected.items()):
        raise ValueError('预约席位超时通知来源不一致')
    transaction.on_commit(lambda: deliver(intent.pk), robust=True)


def deliver(intent_id):
    from club_accounts.models import ClubAccount, LegacyAccountMap
    from orders.models import Reservation, ReservationSeatEvent
    from .models import Message, ReservationSeatTimeoutNotice
    from .notifier import notify_messages_after_commit

    initial = ReservationSeatTimeoutNotice.objects.filter(pk=intent_id).values('account_id', 'event_id').first()
    if not initial:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        reservation_id = ReservationSeatEvent.objects.filter(pk=initial['event_id']).values_list('reservation_id', flat=True).first()
        booking = Reservation.objects.select_for_update().filter(pk=reservation_id).first()
        event = (ReservationSeatEvent.objects.select_for_update().filter(pk=initial['event_id'], reservation=booking).first()
                 if booking else None)
        intent = ReservationSeatTimeoutNotice.objects.select_for_update().filter(pk=intent_id,
            account_id=initial['account_id'], event_id=initial['event_id']).first()
        if not intent or intent.completed_at:
            return
        prior = (ReservationSeatEvent.objects.filter(reservation=booking, seat_number=event.seat_number,
            pk__lt=event.pk).order_by('-pk').first() if event else None)
        later = bool(event and ReservationSeatEvent.objects.filter(reservation=booking,
            seat_number=event.seat_number, pk__gt=event.pk).exists())
        cycle = intent.business_cycle
        previous_intent = prior.invitation_notice_intent if prior else None
        matching_invitation = (previous_intent is None or (isinstance(previous_intent, dict)
            and previous_intent.get('account_id') == intent.account_id
            and previous_intent.get('business_cycle') == cycle
            and previous_intent.get('user_id') == intent.legacy_user_id))
        mapping_valid = (intent.legacy_user_id is None or LegacyAccountMap.objects.filter(
            account_id=intent.account_id, legacy_user_id=intent.legacy_user_id).exists())
        valid = bool(booking and booking.required_provider_count == 2 and event and event.action == 'TIMEOUT'
            and event.seat_number in (1, 2) and event.provider_account_id == intent.account_id
            and prior and prior.pk == intent.invitation_event_id and prior.action == 'INVITED'
            and prior.provider_account_id == intent.account_id and matching_invitation and not later
            and mapping_valid and type(cycle) is int and cycle > 0 and account
            and account.account_type == 'PROVIDER' and account.license_membership_active
            and account.business_cycle == cycle)
        if not valid:
            intent.skipped_reason = '席位来源不可核验、已重选或收件账号周期已变化'
        else:
            intent.message = Message.objects.create(recipient_account=account, recipient_id=intent.legacy_user_id,
                recipient_business_cycle=cycle, type='ORDER', title='预约确认已超时',
                preview='该预约席位已释放，如需了解详情请联系门店客服',
                action_url=f'/pages/reservations/index?id={booking.pk}')
            notify_messages_after_commit(account.pk, business_cycle=cycle)
        intent.completed_at = timezone.now()
        intent.save(update_fields=['message', 'skipped_reason', 'completed_at'])
