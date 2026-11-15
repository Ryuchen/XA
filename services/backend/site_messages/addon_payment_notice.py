"""Recover provider messages from a committed, store-local add-on payment."""
from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def enabled():
    return bool(getattr(settings, 'ADDON_PAYMENT_NOTICE_RECOVERY_ENABLED', False))


def record(payment):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('加购付款通知须与付款同事务保存')
    from .models import AddonPaymentNotice
    intent = payment.intent
    defaults = {'account_id': intent.provider_id, 'segment_id': intent.segment_id,
                'business_cycle': intent.segment.provider_business_cycle}
    notice, created = AddonPaymentNotice.objects.get_or_create(payment=payment, defaults=defaults)
    if not created and any(getattr(notice, key) != value for key, value in defaults.items()):
        raise ValueError('加购付款通知原收件人不一致')
    transaction.on_commit(lambda pk=notice.pk: deliver(pk), robust=True)


def deliver(notice_id):
    from club_accounts.models import ClubAccount
    from orders.models import OrderAddonPayment
    from orders.addon_payment import verify_receipt
    from orders.addon_handover import current_responsibilities
    from .models import AddonPaymentNotice, Message
    from .notifier import notify_messages_after_commit
    original = AddonPaymentNotice.objects.filter(pk=notice_id).values('account_id').first()
    if not original:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=original['account_id']).first()
        notice = AddonPaymentNotice.objects.select_for_update().filter(pk=notice_id).first()
        if not notice or notice.status != 'PENDING':
            return
        payment = OrderAddonPayment.objects.select_related(
            'intent__order', 'intent__segment__attribution', 'buyer_transaction__wallet').filter(pk=notice.payment_id).first()
        cycle = notice.business_cycle
        intent = payment.intent if payment else None
        segment = intent.segment if intent else None
        valid_source = bool(intent and segment and segment.attribution.order_id == intent.order_id
            and intent.provider_id == notice.account_id and notice.segment_id == segment.pk
            and segment.provider_account_id == notice.account_id and segment.provider_business_cycle == cycle
            and type(cycle) is int and cycle > 0)
        if valid_source:
            try:
                verify_receipt(payment)
                _, shares = current_responsibilities(payment)
            except ValidationError:
                valid_source = False
        if not valid_source:
            notice.status, notice.note = 'REVIEW', '加购付款或原收件周期待核对'
        elif not account or account.account_type != 'PROVIDER' or not account.license_membership_active or account.business_cycle != cycle:
            notice.status, notice.note = 'SKIPPED', '原账号业务周期已变化'
        elif not any(row['is_current'] and row['segment_id'] == notice.segment_id
                     and row['provider_id'] == notice.account_id and row['basis'] > 0 for row in shares):
            notice.status, notice.note = 'SKIPPED', '加购责任已转移或退款结清'
        else:
            notice.message = Message.objects.create(recipient_account=account, recipient_business_cycle=cycle,
                type='ORDER', title='收到新加购', preview=f'老板已支付「{intent.addon_name}」，请查看订单详情。'[:255],
                related_order_id=intent.order_id, action_url=f'/pages/orderList/index?orderId={intent.order_id}')
            notice.status = 'SENT'
            notify_messages_after_commit(account.pk, business_cycle=cycle)
        notice.completed_at = timezone.now()
        notice.save(update_fields=['status', 'note', 'message', 'completed_at'])
