"""Recover notices for a buyer's paid, directly selected assignment."""
from django.conf import settings
from django.utils import timezone
from stores.db import transaction


def enabled():
    return bool(getattr(settings, 'ORDER_ASSIGNMENT_NOTICE_RECOVERY_ENABLED', False)
                and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))


def record_all(order, payment, log, provider_ids):
    from .models import OrderAssignmentNotice
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('指定订单通知须与付款和安排同事务保存')
    assignments = list(order.providers.filter(released_at__isnull=True).order_by('pk'))
    if {row.provider_account_id for row in assignments} != set(provider_ids) or len(assignments) != len(provider_ids):
        raise ValueError('指定订单通知席位待核对')
    targets = [(0, None, order.customer_account_id, 'BOSS', order.business_cycle, order.customer_id,
                '订单已安排', f'「{order.service_name_snapshot}」已按你的选择安排陪玩。')]
    targets += [(row.pk, row, row.provider_account_id, 'PROVIDER', row.provider_business_cycle, row.provider_id,
                 '老板指定你服务', f'你收到一笔「{order.service_name_snapshot}」订单，请尽快开始服务')
                for row in assignments]
    for slot, assignment, account_id, kind, cycle, user_id, title, preview in targets:
        intent = OrderAssignmentNotice.objects.create(status_log=log, payment=payment, assignment=assignment,
            slot=slot, account_id=account_id, account_type=kind, business_cycle=cycle, legacy_user_id=user_id,
            title=title, preview=preview[:255])
        transaction.on_commit(lambda pk=intent.pk: deliver(pk), robust=True)


def schedule_immediate(order):
    """Keep original cycles even when persistent recovery is disabled."""
    from .utils import create_cycle_message
    import logging
    targets = [(order.customer_account_id, order.customer_id, order.business_cycle, '订单已安排',
                f'「{order.service_name_snapshot}」已按你的选择安排陪玩。', False)]
    targets += [(row.provider_account_id, row.provider_id, row.provider_business_cycle, '老板指定你服务',
                 f'你收到一笔「{order.service_name_snapshot}」订单，请尽快开始服务', True)
                for row in order.providers.filter(released_at__isnull=True)]
    for account_id, user_id, cycle, title, preview, alert in targets:
        def send(account_id=account_id, user_id=user_id, cycle=cycle, title=title, preview=preview, alert=alert):
            message = create_cycle_message(recipient_account_id=account_id, recipient_id=user_id,
                recipient_business_cycle=cycle, title=title, preview=preview, msg_type='ORDER',
                related_order_id=order.pk, action_url=f'/pages/orderList/index?orderId={order.pk}',
                assignment_alert=alert)
            if message is None:
                logging.getLogger(__name__).warning('指定订单通知未投递，原周期待核对 order=%s account=%s', order.pk, account_id)
        transaction.on_commit(send, robust=True)


def deliver(intent_id):
    from club_accounts.models import ClubAccount
    from orders.models import Order, OrderProvider, OrderStatusLog
    from wallet.models import Transaction
    from .models import Message, OrderAssignmentNotice
    from .notifier import notify_messages_after_commit
    initial = OrderAssignmentNotice.objects.filter(pk=intent_id).values('account_id', 'status_log__order_id').first()
    if not initial:
        return
    with transaction.atomic():
        # Match the payment / retirement order before locking the receiver.
        order = Order.objects.select_for_update().filter(pk=initial['status_log__order_id']).first()
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        intent = OrderAssignmentNotice.objects.select_for_update().filter(pk=intent_id).first()
        if not intent or intent.status != 'PENDING':
            return
        log = OrderStatusLog.objects.filter(pk=intent.status_log_id, order=order).first() if order else None
        payment = Transaction.objects.select_related('wallet').filter(pk=intent.payment_id, order=order).first() if order else None
        assignment = OrderProvider.objects.filter(pk=intent.assignment_id, order=order).first() if intent.assignment_id else None
        cycle = intent.business_cycle
        proof = bool(order and log and log.action == 'ASSIGN' and log.from_status == 'PENDING'
            and log.to_status == 'GRABBED' and log.reason == '老板指定陪玩'
            and log.operator_account_id == order.customer_account_id and payment
            and payment.wallet.account_id == order.customer_account_id and payment.tx_type == 'PAY'
            and payment.status == 'SUCCESS' and payment.amount == -order.amount
            and payment.balance_after == payment.balance_before + payment.amount
            and not payment.reversal_of_id and not payment.reversals.exists())
        if intent.account_type == 'BOSS':
            proof = bool(proof and intent.slot == 0 and not intent.assignment_id
                         and order.customer_account_id == intent.account_id and order.business_cycle == cycle
                         and order.customer_id == intent.legacy_user_id)
        elif intent.account_type == 'PROVIDER':
            proof = bool(proof and assignment and intent.slot == assignment.pk
                         and assignment.provider_account_id == intent.account_id
                         and assignment.provider_business_cycle == cycle
                         and assignment.provider_id == intent.legacy_user_id)
        else:
            proof = False
        if not proof or type(cycle) is not int or cycle <= 0:
            intent.status, intent.note = 'REVIEW', '付款、安排或原业务周期待核对'
        elif (not account or account.account_type != intent.account_type or not account.license_membership_active
              or account.business_cycle != cycle or order.payment_status != Order.PaymentStatus.PAID
              or order.status not in (Order.Status.GRABBED, Order.Status.IN_SERVICE)
              or order.status_logs.filter(pk__gt=log.pk, action__in=('REJECT', 'TRANSFER', 'CANCEL', 'REFUND')).exists()
              or (assignment and (assignment.released_at or order.status != Order.Status.GRABBED))):
            intent.status, intent.note = 'SKIPPED', '订单、席位或原账号周期已变化'
        else:
            alert = intent.account_type == 'PROVIDER'
            intent.message = Message.objects.create(recipient_account=account, recipient_id=intent.legacy_user_id,
                recipient_business_cycle=cycle, type='ORDER', title=intent.title, preview=intent.preview,
                related_order_id=order.pk, action_url=f'/pages/orderList/index?orderId={order.pk}', assignment_alert=alert)
            notify_messages_after_commit(account.pk, business_cycle=cycle,
                                         assignment_message_id=intent.message_id if alert else None)
            intent.status = 'SENT'
        intent.completed_at = timezone.now()
        intent.save(update_fields=['message', 'status', 'note', 'completed_at'])
