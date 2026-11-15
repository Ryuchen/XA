"""Create per-person notices for an actually posted add-on refund."""
from django.conf import settings
from django.utils import timezone
from stores.db import transaction


def enabled():
    return (getattr(settings, 'ADDON_REFUND_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))


def _record(refund, account_id, role, cycle, title, preview):
    from .models import AddonRefundNotice
    if not account_id:
        return
    expected = {'role': role, 'business_cycle': cycle, 'amount': refund.amount,
                'title': title[:100], 'preview': preview[:255]}
    intent, created = AddonRefundNotice.objects.get_or_create(refund=refund, account_id=account_id, defaults=expected)
    if not created and any(getattr(intent, key) != value for key, value in expected.items()):
        raise ValueError('加购退款通知来源不一致')
    transaction.on_commit(lambda: deliver(intent.pk), robust=True)


def record_all(refund):
    if not enabled():
        return
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('加购退款通知必须与实际退款同事务保存')
    from stores.context import current_currency_name
    from .order_cycle import recipient_cycle
    order, payment = refund.order, refund.payment
    whole, fraction = divmod(refund.amount, 10)
    amount = f'{whole}.{fraction}' if fraction else str(whole)
    buyer_id = payment.intent.buyer_id
    _record(refund, buyer_id, 'BOSS', order.business_cycle if buyer_id == order.customer_account_id else None,
        '加购退款已处理', f'加购退款已退回 {amount} {current_currency_name()}至余额，请查看钱包流水。')
    for person in refund.proposal.participants.order_by('account_id'):
        _record(refund, person.account_id, 'PROVIDER', recipient_cycle(order, person.account_id),
            '加购退款已处理', '你参与的加购退款已处理，收益及承担情况请查看订单和钱包流水。')


def deliver(intent_id):
    from club_accounts.models import ClubAccount
    from orders.models import Order, OrderAddonRefund, OrderAddonPayment
    from orders.addon_payment import verify_receipt
    from rest_framework.exceptions import ValidationError
    from .models import AddonRefundNotice, Message
    from .notifier import notify_messages_after_commit
    from .order_cycle import recipient_cycle

    initial = AddonRefundNotice.objects.filter(pk=intent_id).values('account_id', 'refund_id').first()
    if not initial:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        source = OrderAddonRefund.objects.filter(pk=initial['refund_id']).values('order_id', 'payment_id').first()
        order = Order.objects.select_for_update().filter(pk=source['order_id']).first() if source else None
        payment = (OrderAddonPayment.objects.select_for_update().filter(pk=source['payment_id'],
            intent__order=order).first() if order else None)
        refund = OrderAddonRefund.objects.select_for_update().filter(pk=initial['refund_id'],
            order=order, payment=payment).first() if payment else None
        intent = AddonRefundNotice.objects.select_for_update().filter(pk=intent_id,
            account_id=initial['account_id'], refund_id=initial['refund_id']).first()
        if not intent or intent.completed_at:
            return
        evidence_valid = False
        if refund and payment and refund.amount == intent.amount and refund.amount > 0:
            try:
                verify_receipt(payment)
                tx = refund.buyer_transaction
                evidence_valid = bool(tx.reversal_of_id is None and not tx.reversals.exists()
                    and tx.operator_account_id == refund.operator_id
                    and payment.intent.buyer_id == order.customer_account_id)
            except (ValidationError, ValueError):
                evidence_valid = False
        cycle = intent.business_cycle
        if intent.role == 'BOSS':
            source_valid = bool(evidence_valid and order.customer_account_id == intent.account_id
                                and payment.intent.buyer_id == intent.account_id and order.business_cycle == cycle)
        else:
            source_valid = bool(evidence_valid and intent.role == 'PROVIDER'
                and refund.proposal.participants.filter(account_id=intent.account_id).exists()
                and recipient_cycle(order, intent.account_id) == cycle)
        if (not source_valid or type(cycle) is not int or cycle <= 0 or not account
                or account.account_type != intent.role or not account.license_membership_active
                or account.business_cycle != cycle):
            intent.skipped_reason = '加购退款来源不可核验或收件账号周期已变化'
        else:
            intent.message = Message.objects.create(recipient_account=account, recipient_business_cycle=cycle,
                type='ORDER', title=intent.title, preview=intent.preview, related_order_id=order.pk)
            notify_messages_after_commit(account.pk, business_cycle=cycle)
        intent.completed_at = timezone.now()
        intent.save(update_fields=['message', 'skipped_reason', 'completed_at'])
