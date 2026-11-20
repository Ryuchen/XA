"""Project paid customer cancellation notices from the committed refund."""
from django.conf import settings
from django.utils import timezone
from stores.db import transaction


def enabled():
    return (getattr(settings, 'CUSTOMER_CANCEL_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))


def prepare(order, *, refund, receipt=None, review_audit=None):
    if not enabled() or not refund:
        return
    from orders.points_payment_models import OrderPointsRefund
    from wallet.models import Transaction
    from stores.context import current_currency_name
    from .models import CustomerCancellationNotice
    points = OrderPointsRefund.objects.filter(refund=refund).first()
    wallet_units = points.coin_units if points else refund.amount
    points_units = points.points_hundredths if points else 0
    wallet_tx_id = points.coin_transaction_id if points else None
    if review_audit and not points:
        wallet_tx_id = refund.customer_transaction_id
    elif not points and wallet_units and order.refund_records.count() == 1:
        matches = list(Transaction.objects.filter(order=order, wallet__account_id=order.customer_account_id,
            tx_type='REFUND', status='SUCCESS', amount=wallet_units).values_list('pk', flat=True)[:2])
        if len(matches) == 1:
            wallet_tx_id = matches[0]
    preview = ('订单已取消，退款已退回' + current_currency_name() + '余额。' if wallet_units else '订单已取消。')
    if points_units:
        preview += '抵扣积分已退回。'
    expected = {'refund_id': refund.pk, 'receipt_id': receipt.pk if receipt else None,
                'review_audit_id': review_audit.pk if review_audit else None,
                'wallet_transaction_id': wallet_tx_id, 'account_id': order.customer_account_id,
                'business_cycle': order.business_cycle, 'legacy_user_id': order.customer_id,
                'refunded_to_wallet': bool(wallet_units), 'refunded_points': bool(points_units), 'preview': preview[:255]}
    intent, created = CustomerCancellationNotice.objects.get_or_create(order=order, defaults=expected)
    if not created and any(getattr(intent, key) != value for key, value in expected.items()):
        raise ValueError('客户取消通知来源不一致')
    transaction.on_commit(lambda: deliver(intent.pk), robust=True)


def _review_operator_id(intent, order, refund, points):
    from engagement.models import PointLedger
    from orders.models import OrderGrabReviewCancellation
    from orders.points_payment_models import OrderPointsRedemption
    from wallet.models import Transaction

    if (not order or not refund or intent.receipt_id or not refund.operator_account_id
            or refund.operator_account_id == intent.account_id or order.cancel_reason != refund.reason
            or refund.amount != order.amount or order.refund_records.count() != 1):
        return None
    audit = intent.review_audit
    body = audit.request_body
    operator_id = refund.operator_account_id
    if (audit.resource != 'grab-review' or audit.object_id != str(order.pk) or audit.method != 'POST'
            or audit.status_code != 200 or audit.operator_account_id != operator_id
            or not isinstance(body, dict) or body.get('action') != 'CANCEL' or body.get('reason') != refund.reason
            or not OrderGrabReviewCancellation.objects.filter(order=order, customer_id=intent.account_id,
                status='CANCELLED', completed_at__isnull=False).exists()):
        return None

    redemption = points.redemption if points else None
    if redemption:
        if (redemption.order_id != order.pk or points.coin_units != redemption.paid_units
                or points.coin_units != refund.amount or points.points_hundredths != redemption.points_hundredths
                or points.equivalent_units != redemption.deal_units):
            return None
    elif OrderPointsRedemption.objects.filter(order=order).exists():
        return None

    transaction_id = points.coin_transaction_id if points else refund.customer_transaction_id
    if transaction_id != intent.wallet_transaction_id:
        return None
    if transaction_id:
        credit = Transaction.objects.filter(pk=transaction_id, operator_account_id=operator_id)
        if redemption:
            credit = credit.filter(wallet_id=redemption.payment.wallet_id)
        if not credit.exists():
            return None
    if points and points.points_ledger_id and not PointLedger.objects.filter(
            pk=points.points_ledger_id, operator_id=operator_id).exists():
        return None
    return operator_id


def deliver(intent_id):
    from club_accounts.models import ClubAccount
    from engagement.models import PointLedger
    from orders.models import Order, OrderRefund, OrderGrabReceiptProbe
    from orders.points_payment_models import OrderPointsRefund
    from wallet.models import Transaction
    from django.db.models import F
    from .models import CustomerCancellationNotice, Message
    from .notifier import notify_messages_after_commit

    initial = CustomerCancellationNotice.objects.filter(pk=intent_id).values('account_id', 'order_id').first()
    if not initial:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        order = Order.objects.select_for_update().filter(pk=initial['order_id']).first()
        intent = CustomerCancellationNotice.objects.select_for_update().filter(pk=intent_id,
            account_id=initial['account_id'], order_id=initial['order_id']).first()
        if not intent or intent.completed_at:
            return
        refund = OrderRefund.objects.filter(pk=intent.refund_id, order=order).first() if order else None
        points = OrderPointsRefund.objects.filter(refund=refund).first() if refund else None
        wallet_units = points.coin_units if points else (refund.amount if refund else 0)
        points_units = points.points_hundredths if points else 0
        wallet_valid = (not wallet_units and intent.wallet_transaction_id is None) or bool(wallet_units and
            Transaction.objects.filter(pk=intent.wallet_transaction_id, order=order, wallet__account_id=intent.account_id,
                tx_type='REFUND', status='SUCCESS', amount=wallet_units, reversal_of__isnull=True,
                balance_after=F('balance_before') + F('amount'), reversals__isnull=True).exists()
            and (points.coin_transaction_id == intent.wallet_transaction_id if points
                 else order.refund_records.count() == 1))
        points_valid = (not points or (points.is_final and points.redemption.order_id == order.pk
            and ((not points_units and points.points_ledger_id is None)
            or (points_units and PointLedger.objects.filter(pk=points.points_ledger_id, order=order,
                account_id=intent.account_id, amount=points_units, kind='ORDER_POINTS_RETURN',
                event_key=f'order:{order.pk}:points-return:{refund.pk}').exists()))))
        receipt_valid = (not intent.receipt_id or bool(order and OrderGrabReceiptProbe.objects.filter(
            pk=intent.receipt_id, round__pool__order=order, actor_id=intent.account_id,
            kind='BOSS_CANCEL', state='BEFORE').exists()))
        operator_id = (_review_operator_id(intent, order, refund, points)
                       if intent.review_audit_id else intent.account_id)
        log_facts = {'from_status': 'PENDING', 'to_status': 'CANCELLED', 'action': 'CANCEL',
                     'operator_account_id': operator_id}
        if intent.review_audit_id and refund:
            log_facts['reason'] = refund.reason
        cycle = intent.business_cycle
        valid = bool(order and order.status == Order.Status.CANCELLED and order.payment_status == Order.PaymentStatus.REFUNDED
            and operator_id and order.status_logs.filter(**log_facts).exists()
            and refund and receipt_valid and wallet_valid and points_valid
            and intent.refunded_to_wallet == bool(wallet_units) and intent.refunded_points == bool(points_units)
            and intent.account_id == order.customer_account_id and intent.legacy_user_id == order.customer_id
            and type(cycle) is int and cycle > 0 and cycle == order.business_cycle and account
            and account.account_type == 'BOSS' and account.license_membership_active and account.business_cycle == cycle)
        if not valid:
            intent.skipped_reason = '取消退款来源不可核验或收件账号周期已变化'
        else:
            intent.message = Message.objects.create(recipient_account=account, recipient_id=intent.legacy_user_id,
                recipient_business_cycle=cycle, type='ORDER', title='订单已取消', preview=intent.preview,
                action_url=f'/pages/orderList/index?orderId={order.pk}', related_order_id=order.pk)
            notify_messages_after_commit(account.pk, business_cycle=cycle)
        intent.completed_at = timezone.now()
        intent.save(update_fields=['message', 'skipped_reason', 'completed_at'])
