"""Pending add-on completion decisions, tied to their original store cycles."""
from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def enabled():
    return bool(getattr(settings, 'ADDON_COMPLETION_NOTICE_RECOVERY_ENABLED', False))


def record_all(completion):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('加购完成确认通知须与请求同事务保存')
    from .models import AddonCompletionNotice
    order = completion.payment.intent.order
    recipients = [('BOSS', completion.buyer_id, order.business_cycle),
                  ('PROVIDER', completion.provider_id, completion.segment.provider_business_cycle)]
    for role, account_id, cycle in recipients:
        notice, created = AddonCompletionNotice.objects.get_or_create(completion=completion, role=role,
            defaults={'account_id': account_id, 'business_cycle': cycle})
        if not created and (notice.account_id != account_id or notice.business_cycle != cycle):
            raise ValueError('加购完成确认通知来源不一致')
        transaction.on_commit(lambda pk=notice.pk: deliver(pk), robust=True)


def refresh_delivered(completion):
    """Keep an already-created prompt truthful without sending another one."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('加购确认消息须与状态变更同事务更新')
    from .models import AddonCompletionNotice
    from .notifier import notify_messages_after_commit
    for notice in AddonCompletionNotice.objects.select_for_update().filter(
            completion=completion, status='SENT', message__isnull=False).select_related('message'):
        decision = completion.buyer_accepted if notice.role == 'BOSS' else completion.provider_accepted
        if completion.status == 'ACCEPTED':
            title, preview = '加购完成情况已确认', '各方已确认，请查看订单。'
        elif completion.status == 'REJECTED':
            title, preview = '加购完成确认未通过', '确认未通过，请查看订单。'
        elif completion.status == 'SUPERSEDED':
            title, preview = '加购完成确认已更新', '原确认已失效，请查看订单当前状态。'
        elif decision is not None:
            title, preview = '已处理加购完成确认', '你的确认已记录，请查看订单。'
        else:
            continue
        notice.message.title, notice.message.preview = title, preview
        notice.message.save(update_fields=['title', 'preview'])
        notify_messages_after_commit(notice.account_id, business_cycle=notice.business_cycle)


def deliver(notice_id):
    from club_accounts.models import ClubAccount
    from orders.models import Order, OrderAddonCompletion, OrderAddonPayment, OrderSegmentPersonSettlement
    from orders.addon_completion import _digest
    from orders.addon_handover import current_responsibilities
    from orders.addon_payment import verify_receipt
    from .models import AddonCompletionNotice, Message
    from .notifier import notify_messages_after_commit
    initial = AddonCompletionNotice.objects.filter(pk=notice_id).values(
        'account_id', 'completion_id', 'completion__payment_id', 'completion__payment__intent__order_id').first()
    if not initial:
        return
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=initial['completion__payment__intent__order_id']).first()
        payment = OrderAddonPayment.objects.select_for_update().select_related(
            'intent__segment__attribution', 'buyer_transaction__wallet').filter(pk=initial['completion__payment_id'],
            intent__order=order).first() if order else None
        completion = OrderAddonCompletion.objects.select_for_update().select_related(
            'segment', 'execution', 'points_posting').filter(pk=initial['completion_id'], payment=payment).first() if payment else None
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        notice = AddonCompletionNotice.objects.select_for_update().filter(pk=notice_id).first()
        if not notice or notice.status != 'PENDING':
            return
        if not completion or completion.status != 'PENDING':
            notice.status, notice.note = 'SKIPPED', '确认请求已结束或作废'
        else:
            cycle = notice.business_cycle
            snapshot = completion.snapshot
            segment = completion.segment
            role_id = completion.buyer_id if notice.role == 'BOSS' else completion.provider_id if notice.role == 'PROVIDER' else None
            source_cycle = order.business_cycle if notice.role == 'BOSS' else segment.provider_business_cycle
            origin = completion.execution or completion.points_posting
            valid = bool(isinstance(snapshot, dict) and completion.snapshot_hash == _digest(snapshot, completion.completed_work)
                and snapshot.get('payment_id') == payment.pk and snapshot.get('buyer_id') == completion.buyer_id
                and snapshot.get('provider_id') == completion.provider_id and snapshot.get('segment_id') == segment.pk
                and payment.intent.buyer_id == completion.buyer_id and segment.attribution.order_id == order.pk
                and segment.provider_account_id == completion.provider_id and notice.account_id == role_id
                and type(cycle) is int and cycle > 0 and source_cycle == cycle
                and origin and origin.termination_processed_at and origin.termination_processed_at <= completion.created_at
                and snapshot.get('execution_id' if completion.execution_id else 'points_posting_id') == origin.pk)
            if valid:
                try:
                    verify_receipt(payment)
                    handover, shares = current_responsibilities(payment)
                except ValidationError:
                    valid = False
            if not valid:
                notice.status, notice.note = 'REVIEW', '确认请求或原业务周期待核对'
            elif not account or account.account_type != notice.role or not account.license_membership_active or account.business_cycle != cycle:
                notice.status, notice.note = 'SKIPPED', '原账号业务周期已变化'
            elif (completion.buyer_accepted if notice.role == 'BOSS' else completion.provider_accepted) is not None:
                notice.status, notice.note = 'SKIPPED', '本人已处理该确认'
            elif (order.settled_at or payment.settlements.exists()
                  or payment.segment_settlements.filter(segment_id=segment.pk).exists()
                  or any(segment.pk in (row.segment_ids or []) for row in OrderSegmentPersonSettlement.objects.filter(
                      manifest__order=order, account_id=completion.provider_id))):
                notice.status, notice.note = 'SKIPPED', '订单或该加购履约段已结算'
            elif payment.handovers.filter(status='PENDING').exists() or payment.refund_proposals.filter(
                    status__in=('PENDING', 'ACCEPTED'), refund__isnull=True).exists():
                return
            elif (snapshot.get('handover_id') != (handover.pk if handover else None)
                  or snapshot.get('refunds') != [list(item) for item in payment.refund_records.order_by('pk').values_list('pk', 'amount')]
                  or not any(row['is_current'] and row['segment_id'] == segment.pk
                         and row['provider_id'] == completion.provider_id and row['basis'] == snapshot.get('basis')
                         and row['income'] == snapshot.get('income')
                         and row['commission_snapshot'] == snapshot.get('commission') for row in shares)):
                notice.status, notice.note = 'SKIPPED', '加购责任或金额已变化'
            else:
                notice.message = Message.objects.create(recipient_account=account, recipient_business_cycle=cycle,
                    type='ORDER', title='加购完成情况待确认',
                    preview=f'请确认「{payment.intent.addon_name}」的完成情况。'[:255], related_order_id=order.pk,
                    action_url=f'/pages/orderList/index?orderId={order.pk}')
                notice.status = 'SENT'
                notify_messages_after_commit(account.pk, business_cycle=cycle)
        notice.completed_at = timezone.now()
        notice.save(update_fields=['status', 'note', 'message', 'completed_at'])
