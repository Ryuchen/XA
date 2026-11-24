"""Notify confirmed service transfers using original participation facts."""
from django.conf import settings
from django.utils import timezone
from stores.db import transaction


def enabled():
    return (getattr(settings, 'SERVICE_TRANSFER_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))


def record_all(transfer):
    if not enabled():
        return
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('转单确认通知必须与实际入队事实同事务保存')
    from .models import ServiceTransferNotice
    old, new = transfer.previous_segment, transfer.next_segment
    order = new.attribution.order
    if not new.started_at or new.start_source != 'TRANSFER_ENTRY':
        raise ValueError('转单接替尚未确认入队')
    recipients = [
        (order.customer_account_id, 'BOSS', None, order.business_cycle,
         '转单接替已确认', '接替陪玩已确认本次入队，请查看订单当前安排。'),
        (old.provider_account_id, 'PROVIDER', old.pk, old.provider_business_cycle,
         '转单接替已确认', '本次转单接替已确认，原履约情况请查看订单。'),
        (new.provider_account_id, 'PROVIDER', new.pk, new.provider_business_cycle,
         '转单入队已确认', '你已确认本次转单入队，请查看订单当前安排。'),
    ]
    for account_id, role, segment_id, cycle, title, preview in recipients:
        if not account_id:
            continue
        expected = {'segment_id': segment_id, 'role': role, 'business_cycle': cycle,
                    'started_at': new.started_at, 'title': title, 'preview': preview}
        intent, created = ServiceTransferNotice.objects.get_or_create(transfer=transfer,
            account_id=account_id, defaults=expected)
        if not created and any(getattr(intent, key) != value for key, value in expected.items()):
            raise ValueError('转单通知来源不一致')
        transaction.on_commit(lambda pk=intent.pk: deliver(pk), robust=True)


def deliver(intent_id):
    from club_accounts.models import ClubAccount
    from orders.models import Order, OrderOriginalShareTransfer, OrderParticipationSegment
    from .models import ServiceTransferNotice, Message
    from .notifier import notify_messages_after_commit

    initial = ServiceTransferNotice.objects.filter(pk=intent_id).values(
        'account_id', 'transfer_id', 'transfer__next_segment__attribution__order_id').first()
    if not initial:
        return
    with transaction.atomic():
        # Both transfer notices serialize on the order before the recipient.
        order = Order.objects.select_for_update().filter(pk=initial['transfer__next_segment__attribution__order_id']).first()
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        transfer = (OrderOriginalShareTransfer.objects.select_for_update().filter(pk=initial['transfer_id'],
            next_segment__attribution__order=order).first() if order else None)
        intent = ServiceTransferNotice.objects.select_for_update().filter(pk=intent_id,
            account_id=initial['account_id'], transfer_id=initial['transfer_id']).first()
        if not intent or intent.completed_at:
            return
        old, new = (transfer.previous_segment, transfer.next_segment) if transfer else (None, None)
        proof = bool(order and transfer and old and new and old.attribution_id == new.attribution_id
            and old.attribution.order_id == order.pk and old.pk != new.pk and old.slot == new.slot
            and old.provider_account_id != new.provider_account_id and old.released_at == transfer.created_at
            and new.accepted_at == transfer.created_at and new.started_at == intent.started_at
            and new.start_source == 'TRANSFER_ENTRY' and new.started_at)
        cycle = intent.business_cycle
        if intent.role == 'BOSS':
            own_source = bool(proof and intent.segment_id is None and intent.account_id == order.customer_account_id
                              and order.business_cycle == cycle)
        else:
            segment = OrderParticipationSegment.objects.filter(pk=intent.segment_id,
                attribution__order=order, provider_account_id=intent.account_id).first() if order else None
            own_source = bool(proof and intent.role == 'PROVIDER' and segment
                and segment.pk in (old.pk, new.pk) and segment.provider_business_cycle == cycle)
        if (not own_source or type(cycle) is not int or cycle <= 0 or not account
                or account.account_type != intent.role or not account.license_membership_active
                or account.business_cycle != cycle):
            intent.skipped_reason = '转单来源不可核验或收件账号周期已变化'
        else:
            intent.message = Message.objects.create(recipient_account=account, recipient_business_cycle=cycle,
                type='ORDER', title=intent.title, preview=intent.preview, related_order_id=order.pk)
            notify_messages_after_commit(account.pk, business_cycle=cycle)
        intent.completed_at = timezone.now()
        intent.save(update_fields=['message', 'skipped_reason', 'completed_at'])
