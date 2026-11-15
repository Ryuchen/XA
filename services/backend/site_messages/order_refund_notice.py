"""Project committed non-points balance refunds into per-recipient notices."""
from django.conf import settings
from django.db.models import F
from django.utils import timezone
from stores.db import transaction


def enabled():
    return (getattr(settings, 'ORDER_REFUND_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))


def prepare(order, refund, amount):
    from wallet.models import Transaction
    if not refund or refund.order_id != order.pk or refund.amount != amount or amount <= 0:
        # Never fabricate a refund or disturb the existing business outcome.
        return None
    # The ordinary refund row lacks a direct wallet-transaction FK. Only bind
    # the result when one refund and one credit with this amount are provable.
    wallet_tx = None
    if order.refund_records.filter(amount=amount).count() == 1:
        matches = list(Transaction.objects.filter(order=order, wallet__account_id=order.customer_account_id,
            tx_type='REFUND', status='SUCCESS', amount=amount, reversal_of__isnull=True,
            balance_after=F('balance_before') + F('amount'), reversals__isnull=True).values_list('pk', flat=True)[:2])
        if len(matches) == 1:
            wallet_tx = matches[0]
    return refund, amount, wallet_tx


def record(source, *, account_id, legacy_user_id, cycle, title, preview, can_open_order):
    from .models import OrderRefundNotice
    if not account_id or source is None:
        return
    refund, amount, wallet_tx = source
    expected = {'wallet_transaction_id': wallet_tx, 'business_cycle': cycle, 'legacy_user_id': legacy_user_id,
                'amount': amount, 'title': title[:100], 'preview': preview[:255], 'can_open_order': can_open_order}
    intent, created = OrderRefundNotice.objects.get_or_create(refund=refund, account_id=account_id, defaults=expected)
    if not created and any(getattr(intent, key) != value for key, value in expected.items()):
        raise ValueError('退款通知来源不一致')
    transaction.on_commit(lambda: deliver(intent.pk), robust=True)


def deliver(intent_id):
    from club_accounts.models import ClubAccount, LegacyAccountMap
    from orders.models import Order, OrderRefund
    from orders.points_payment_models import OrderPointsRefund
    from wallet.models import Transaction
    from .models import Message, OrderRefundNotice
    from .notifier import notify_messages_after_commit
    from .order_cycle import recipient_cycle

    initial = OrderRefundNotice.objects.filter(pk=intent_id).values('account_id', 'refund_id').first()
    if not initial:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        order_id = OrderRefund.objects.filter(pk=initial['refund_id']).values_list('order_id', flat=True).first()
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        refund = OrderRefund.objects.select_for_update().filter(pk=initial['refund_id'], order=order).first() if order else None
        intent = OrderRefundNotice.objects.select_for_update().filter(pk=intent_id,
            account_id=initial['account_id'], refund_id=initial['refund_id']).first()
        if not intent or intent.completed_at:
            return
        cycle = intent.business_cycle
        wallet_valid = bool(refund and order and refund.amount == intent.amount and intent.amount > 0
            and order.refund_records.filter(amount=intent.amount).count() == 1
            and not OrderPointsRefund.objects.filter(refund=refund).exists()
            and Transaction.objects.filter(pk=intent.wallet_transaction_id, order=order,
                wallet__account_id=order.customer_account_id, amount=intent.amount, tx_type='REFUND',
                status='SUCCESS', reversal_of__isnull=True, reversals__isnull=True,
                balance_after=F('balance_before') + F('amount')).exists())
        owner_valid = (intent.legacy_user_id is None or LegacyAccountMap.objects.filter(
            account_id=intent.account_id, legacy_user_id=intent.legacy_user_id).exists())
        valid = bool(wallet_valid and owner_valid and account and account.account_type in ('BOSS', 'PROVIDER')
            and account.license_membership_active and type(cycle) is int and cycle > 0
            and account.business_cycle == cycle and recipient_cycle(order, intent.account_id) == cycle)
        if not valid:
            intent.skipped_reason = '退款流水或收件来源不可核验，或账号周期已变化'
        else:
            intent.message = Message.objects.create(recipient_account=account, recipient_id=intent.legacy_user_id,
                recipient_business_cycle=cycle, type='ORDER', title=intent.title, preview=intent.preview,
                related_order_id=order.pk if intent.can_open_order else None,
                action_url='' if intent.can_open_order else '/pages/wallet/index')
            notify_messages_after_commit(account.pk, business_cycle=cycle)
        intent.completed_at = timezone.now()
        intent.save(update_fields=['message', 'skipped_reason', 'completed_at'])
