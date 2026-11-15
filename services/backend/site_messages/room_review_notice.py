"""Recover the staff review result of a room completion request."""
from django.conf import settings
from django.utils import timezone
from stores.db import transaction


def enabled():
    return (getattr(settings, 'ROOM_REVIEW_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))


def record(request, *, account_id, account_type, cycle, decision, title, preview):
    from .models import RoomReviewNotice
    if not account_id:
        return
    expected = {'account_type': account_type, 'business_cycle': cycle, 'decision': decision,
                'title': title[:100], 'preview': preview[:255]}
    intent, created = RoomReviewNotice.objects.get_or_create(request=request, account_id=account_id, defaults=expected)
    if not created and any(getattr(intent, key) != value for key, value in expected.items()):
        raise ValueError('房间核实通知来源不一致')
    transaction.on_commit(lambda: deliver(intent.pk), robust=True)


def deliver(intent_id):
    from club_accounts.models import ClubAccount
    from orders.models import Order, OrderRoomCompletionRequest, OrderRoomConfirmation
    from .models import RoomReviewNotice, Message
    from .notifier import notify_messages_after_commit
    from .order_cycle import recipient_cycle

    initial = RoomReviewNotice.objects.filter(pk=intent_id).values('account_id', 'request_id').first()
    if not initial:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        order_id = OrderRoomCompletionRequest.objects.filter(pk=initial['request_id']).values_list(
            'room__order_id', flat=True).first()
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        request = (OrderRoomCompletionRequest.objects.select_for_update().filter(pk=initial['request_id'],
            room__order=order).first() if order else None)
        intent = RoomReviewNotice.objects.select_for_update().filter(pk=intent_id,
            account_id=initial['account_id'], request_id=initial['request_id']).first()
        if not intent or intent.completed_at:
            return
        cycle = intent.business_cycle
        proof = bool(order and request and request.status == intent.decision
                     and intent.decision in ('VERIFIED', 'REJECTED') and request.reviewed_by_id
                     and request.reviewed_at and request.review_request_id and request.review_note)
        if intent.decision == 'VERIFIED':
            wait = OrderRoomConfirmation.objects.filter(request=request).first() if request else None
            if proof and wait and wait.status == 'PAUSED' and order.status == Order.Status.IN_SERVICE:
                return  # An objection can be resolved and the same confirmation resumed.
            proof = bool(proof and wait and wait.status == 'RUNNING' and order.status == Order.Status.IN_SERVICE)
        if intent.account_type == 'PROVIDER':
            source_valid = bool(proof and request.submitted_by_id == intent.account_id
                                and recipient_cycle(order, intent.account_id) == cycle)
        else:
            source_valid = bool(proof and intent.account_type == 'BOSS' and intent.decision == 'VERIFIED'
                                and order.customer_account_id == intent.account_id and order.business_cycle == cycle)
        if (not source_valid or type(cycle) is not int or cycle <= 0 or not account
                or account.account_type != intent.account_type or not account.license_membership_active
                or account.business_cycle != cycle):
            intent.skipped_reason = '核实来源不可核验、状态或收件账号周期已变化'
        else:
            intent.message = Message.objects.create(recipient_account=account, recipient_business_cycle=cycle,
                type='ORDER', title=intent.title, preview=intent.preview,
                detail=request.review_note if intent.account_type == 'PROVIDER' else '', related_order_id=order.pk)
            notify_messages_after_commit(account.pk, business_cycle=cycle)
        intent.completed_at = timezone.now()
        intent.save(update_fields=['message', 'skipped_reason', 'completed_at'])
