"""Recover completion notices from committed room confirmation facts."""
from django.conf import settings
from django.utils import timezone
from stores.db import transaction


def enabled():
    return (getattr(settings, 'ROOM_CONFIRMATION_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))


def record(confirmation, *, account_id, account_type, cycle, title, preview):
    from .models import RoomConfirmationNotice
    if not account_id:
        return
    expected = {'account_type': account_type, 'business_cycle': cycle, 'title': title[:100], 'preview': preview[:255]}
    intent, created = RoomConfirmationNotice.objects.get_or_create(confirmation=confirmation,
        account_id=account_id, defaults=expected)
    if not created and any(getattr(intent, key) != value for key, value in expected.items()):
        raise ValueError('房间结单通知来源不一致')
    transaction.on_commit(lambda: deliver(intent.pk), robust=True)


def deliver(intent_id):
    from club_accounts.models import ClubAccount
    from orders.models import Order, OrderRoomCompletionRequest, OrderRoomConfirmation
    from .models import RoomConfirmationNotice, Message
    from .notifier import notify_messages_after_commit
    from .order_cycle import recipient_cycle

    initial = RoomConfirmationNotice.objects.filter(pk=intent_id).values('account_id', 'confirmation_id').first()
    if not initial:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        source = OrderRoomConfirmation.objects.filter(pk=initial['confirmation_id']).values(
            'request_id', 'request__room__order_id').first()
        order = Order.objects.select_for_update().filter(pk=source['request__room__order_id']).first() if source else None
        request = (OrderRoomCompletionRequest.objects.select_for_update().filter(pk=source['request_id'],
            room__order=order).first() if order else None)
        confirmation = (OrderRoomConfirmation.objects.select_for_update().filter(pk=initial['confirmation_id'],
            request=request).first() if request else None)
        intent = RoomConfirmationNotice.objects.select_for_update().filter(pk=intent_id,
            account_id=initial['account_id'], confirmation_id=initial['confirmation_id']).first()
        if not intent or intent.completed_at:
            return
        cycle = intent.business_cycle
        proof = bool(order and request and confirmation and request.status == 'VERIFIED'
            and confirmation.status in ('CONFIRMED', 'AUTO') and confirmation.confirmed_at
            and confirmation.confirmation_request_id and order.status == Order.Status.COMPLETED
            and order.status_logs.filter(from_status='IN_SERVICE', to_status='COMPLETED', action='COMPLETE').exists()
            and ((confirmation.status == 'CONFIRMED' and confirmation.confirmed_by_id == order.customer_account_id
                  and order.customer_account_id is not None)
                 or (confirmation.status == 'AUTO' and confirmation.confirmed_by_id is None)))
        if intent.account_type == 'PROVIDER':
            source_valid = bool(proof and isinstance(request.participants, list)
                and intent.account_id in request.participants and recipient_cycle(order, intent.account_id) == cycle)
        else:
            source_valid = bool(proof and intent.account_type == 'BOSS'
                and order.customer_account_id == intent.account_id and order.business_cycle == cycle)
        if (not source_valid or type(cycle) is not int or cycle <= 0 or not account
                or account.account_type != intent.account_type or not account.license_membership_active
                or account.business_cycle != cycle):
            intent.skipped_reason = '结单来源不可核验或收件账号周期已变化'
        else:
            intent.message = Message.objects.create(recipient_account=account, recipient_business_cycle=cycle,
                type='ORDER', title=intent.title, preview=intent.preview, related_order_id=order.pk)
            notify_messages_after_commit(account.pk, business_cycle=cycle)
        intent.completed_at = timezone.now()
        intent.save(update_fields=['message', 'skipped_reason', 'completed_at'])
