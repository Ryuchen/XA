"""Project a customer completion notice from a committed order status transition."""
from django.conf import settings
from django.utils import timezone
from stores.db import transaction


def prepare(order, status_log):
    from .models import OrderCompletionNotice
    guarded = getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
    recovery = guarded and getattr(settings, 'ORDER_COMPLETION_NOTICE_RECOVERY_ENABLED', False)
    title = '订单已完成'
    preview = f'你的「{order.service_name_snapshot}」已完成，可在订单中评价'
    account_id, cycle, user_id, order_id = order.customer_account_id, order.business_cycle, order.customer_id, order.pk
    if recovery:
        intent = OrderCompletionNotice.objects.create(status_log=status_log, account_id=account_id,
            business_cycle=cycle, legacy_user_id=user_id, title=title, preview=preview[:255])
        transaction.on_commit(lambda: deliver(intent.pk), robust=True)
    elif guarded:
        from .utils import create_cycle_message
        transaction.on_commit(lambda: create_cycle_message(recipient_id=user_id, recipient_account_id=account_id,
            recipient_business_cycle=cycle, title=title, preview=preview, msg_type='ORDER', related_order_id=order_id),
            robust=True)
    else:
        from orders.services import push_message_safe
        transaction.on_commit(lambda: push_message_safe(recipient_id=user_id, recipient_account_id=account_id,
            title=title, preview=preview, msg_type='ORDER', related_order_id=order_id), robust=True)


def deliver(intent_id):
    from club_accounts.models import ClubAccount
    from orders.models import Order, OrderStatusLog
    from .models import Message, OrderCompletionNotice
    from .notifier import notify_messages_after_commit

    initial = OrderCompletionNotice.objects.filter(pk=intent_id).values('account_id', 'status_log_id').first()
    if not initial:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        order_id = OrderStatusLog.objects.filter(pk=initial['status_log_id']).values_list('order_id', flat=True).first()
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        log = (OrderStatusLog.objects.select_for_update().filter(pk=initial['status_log_id'], order=order).first()
               if order else None)
        intent = OrderCompletionNotice.objects.select_for_update().filter(pk=intent_id,
            account_id=initial['account_id'], status_log_id=initial['status_log_id']).first()
        if not intent or intent.completed_at:
            return
        cycle = intent.business_cycle
        valid = bool(order and log and log.action == 'COMPLETE' and log.from_status == 'IN_SERVICE'
                     and log.to_status == 'COMPLETED' and order.status == Order.Status.COMPLETED
                     and order.room_required_snapshot is not True and order.customer_account_id == intent.account_id
                     and order.customer_id == intent.legacy_user_id and type(cycle) is int and cycle > 0
                     and order.business_cycle == cycle and account and account.account_type == 'BOSS'
                     and account.license_membership_active and account.business_cycle == cycle)
        if not valid:
            intent.skipped_reason = '完成来源不可核验、订单状态或账号周期已变化'
        else:
            intent.message = Message.objects.create(recipient_account=account, recipient_id=intent.legacy_user_id,
                recipient_business_cycle=cycle, type='ORDER', title=intent.title, preview=intent.preview,
                related_order_id=order.pk)
            notify_messages_after_commit(account.pk, business_cycle=cycle)
        intent.completed_at = timezone.now()
        intent.save(update_fields=['message', 'skipped_reason', 'completed_at'])
