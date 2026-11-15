"""Recover store-local pending-order timeout notices from the cancellation fact."""
from django.conf import settings
from django.utils import timezone
from stores.db import transaction


def enabled():
    return (getattr(settings, 'ORDER_TIMEOUT_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))


def record(order, *, refund, refunded_to_wallet, refunded_points, preview):
    from .models import OrderTimeoutNotice
    expected = {'refund_id': refund.pk if refund else None, 'account_id': order.customer_account_id,
                'business_cycle': order.business_cycle, 'legacy_user_id': order.customer_id,
                'refunded_to_wallet': bool(refunded_to_wallet), 'refunded_points': bool(refunded_points),
                'preview': preview[:255]}
    intent, created = OrderTimeoutNotice.objects.get_or_create(order=order, defaults=expected)
    if not created and any(getattr(intent, key) != value for key, value in expected.items()):
        raise ValueError('订单超时通知来源不一致')
    transaction.on_commit(lambda: deliver(intent.pk), robust=True)


def deliver(intent_id):
    from club_accounts.models import ClubAccount
    from orders.models import Order, OrderRefund
    from orders.points_payment_models import OrderPointsRefund
    from .models import Message, OrderTimeoutNotice
    from .notifier import notify_messages_after_commit

    initial = OrderTimeoutNotice.objects.filter(pk=intent_id).values('account_id', 'order_id').first()
    if not initial:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        order = Order.objects.select_for_update().filter(pk=initial['order_id']).first()
        intent = OrderTimeoutNotice.objects.select_for_update().filter(pk=intent_id,
            account_id=initial['account_id'], order_id=initial['order_id']).first()
        if not intent or intent.completed_at:
            return
        cycle = intent.business_cycle
        refund = OrderRefund.objects.filter(pk=intent.refund_id, order=order).first() if intent.refund_id else None
        points = OrderPointsRefund.objects.filter(refund=refund).first() if refund else None
        actual_wallet = bool(points.coin_units) if points else bool(refund and refund.amount)
        actual_points = bool(points and points.points_hundredths and points.points_ledger_id)
        refund_valid = bool((not intent.refund_id and not intent.refunded_to_wallet and not intent.refunded_points)
            or (refund and actual_wallet == intent.refunded_to_wallet and actual_points == intent.refunded_points
                and order.payment_status == Order.PaymentStatus.REFUNDED
                and (not points or (points.is_final and ((not points.coin_units or points.coin_transaction_id)
                                    and (not points.points_hundredths or points.points_ledger_id))))))
        valid = bool(order and order.status == Order.Status.CANCELLED and order.status_logs.filter(
            from_status='PENDING', to_status='CANCELLED', action='AUTO_CANCEL').exists() and refund_valid
            and intent.account_id == order.customer_account_id and intent.legacy_user_id == order.customer_id
            and type(cycle) is int and cycle > 0 and cycle == order.business_cycle and account
            and account.account_type == 'BOSS' and account.license_membership_active and account.business_cycle == cycle)
        if not valid:
            intent.skipped_reason = '超时退款来源不可核验或收件账号周期已变化'
        else:
            intent.message = Message.objects.create(recipient_account=account, recipient_id=intent.legacy_user_id,
                recipient_business_cycle=cycle, type='ORDER', title='订单超时已取消', preview=intent.preview,
                action_url=f'/pages/orderList/index?orderId={order.pk}', related_order_id=order.pk)
            notify_messages_after_commit(account.pk, business_cycle=cycle)
        intent.completed_at = timezone.now()
        intent.save(update_fields=['message', 'skipped_reason', 'completed_at'])
