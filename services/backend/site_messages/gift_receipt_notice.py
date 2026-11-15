"""Store-local gift receipt notices from committed payment facts."""
from django.conf import settings
from django.utils import timezone
from stores.db import transaction


def enabled():
    return (getattr(settings, 'GIFT_RECEIPT_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))


def record(order, payment, income, *, preview):
    from .models import GiftReceiptNotice
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('赠礼通知必须与付款同事务保存')
    expected = {'account_id': order.provider_account_id, 'business_cycle': order.gift_provider_business_cycle,
        'legacy_user_id': order.provider_id, 'payment_id': payment.pk, 'income_id': income.pk,
        'preview': preview[:255]}
    intent, created = GiftReceiptNotice.objects.get_or_create(order=order, defaults=expected)
    if not created and any(getattr(intent, key) != value for key, value in expected.items()):
        raise ValueError('赠礼通知来源不一致')
    transaction.on_commit(lambda pk=intent.pk: deliver(pk), robust=True)


def deliver(intent_id):
    from club_accounts.models import ClubAccount, LegacyAccountMap
    from orders.models import Order
    from wallet.models import Transaction
    from .models import GiftReceiptNotice, Message
    from .notifier import notify_messages_after_commit
    initial = GiftReceiptNotice.objects.filter(pk=intent_id).values('account_id', 'order_id').first()
    if not initial:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        order = Order.objects.select_for_update().filter(pk=initial['order_id']).first()
        intent = GiftReceiptNotice.objects.select_for_update().filter(pk=intent_id,
            account_id=initial['account_id'], order_id=initial['order_id']).first()
        if not intent or intent.status != 'PENDING':
            return
        cycle = intent.business_cycle
        if type(cycle) is not int or cycle <= 0:
            intent.status, intent.note = 'REVIEW', '赠礼原收件周期缺失，待核对'
        else:
            payment = Transaction.objects.select_related('wallet').filter(pk=intent.payment_id, order=order).first()
            income = Transaction.objects.select_related('wallet').filter(pk=intent.income_id, order=order).first()
            mapping = (intent.legacy_user_id is None or LegacyAccountMap.objects.filter(
                account_id=intent.account_id, legacy_user_id=intent.legacy_user_id).exists())
            proof = bool(order and order.is_gift_snapshot and order.status == 'COMPLETED'
                and order.payment_status == 'PAID' and order.provider_account_id == intent.account_id
                and order.provider_id == intent.legacy_user_id and order.gift_provider_business_cycle == cycle
                and order.amount > 0 and payment and payment.tx_type == 'GIFT' and payment.status == 'SUCCESS'
                and payment.wallet.account_id == order.customer_account_id and payment.amount == -order.amount
                and payment.balance_after == payment.balance_before + payment.amount and not payment.reversal_of_id
                and not payment.reversals.filter(status='SUCCESS').exists() and income and income.tx_type == 'GIFT'
                and income.status == 'SUCCESS' and income.wallet.account_id == intent.account_id
                and income.amount == order.provider_income and income.balance_after == income.balance_before + income.amount
                and not income.reversal_of_id and not income.reversals.filter(status='SUCCESS').exists())
            if not proof:
                intent.status, intent.note = 'REVIEW', '赠礼付款或收益来源待核对'
            elif not (mapping and account and account.account_type == 'PROVIDER'
                      and account.license_membership_active and account.business_cycle == cycle):
                intent.status, intent.note = 'SKIPPED', '原收件账号或业务周期已变化'
            else:
                intent.message = Message.objects.create(recipient_account=account, recipient_id=intent.legacy_user_id,
                    recipient_business_cycle=cycle, type='PROMOTION', title='收到老板礼物', preview=intent.preview,
                    related_order_id=order.pk, action_url=f'/pages/orderList/index?orderId={order.pk}')
                notify_messages_after_commit(account.pk, business_cycle=cycle)
                intent.status = 'SENT'
        intent.completed_at = timezone.now()
        intent.save(update_fields=['status', 'note', 'message', 'completed_at'])
