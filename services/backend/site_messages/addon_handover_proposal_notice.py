"""Pending add-on handover decisions for the three actual parties."""
from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def enabled():
    return bool(getattr(settings, 'ADDON_HANDOVER_PROPOSAL_NOTICE_RECOVERY_ENABLED', False))


def record_all(handover):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('加购接替方案通知须与方案同事务保存')
    from .models import AddonHandoverProposalNotice
    order = handover.payment.intent.order
    source, target = handover.source_segment, handover.target_segment
    recipients = [('BOSS', handover.payment.intent.buyer_id, None, order.business_cycle),
                  ('PROVIDER', source.provider_account_id, source.pk, source.provider_business_cycle),
                  ('PROVIDER', target.provider_account_id, target.pk, target.provider_business_cycle)]
    for role, account_id, segment_id, cycle in recipients:
        notice, created = AddonHandoverProposalNotice.objects.get_or_create(handover=handover, account_id=account_id,
            defaults={'role': role, 'segment_id': segment_id, 'business_cycle': cycle})
        if not created and (notice.role != role or notice.segment_id != segment_id or notice.business_cycle != cycle):
            raise ValueError('加购接替方案通知来源不一致')
        transaction.on_commit(lambda pk=notice.pk: deliver(pk), robust=True)


def refresh_delivered(handover):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('加购接替方案消息须与决定同事务更新')
    from .models import AddonHandoverProposalNotice
    from .notifier import notify_messages_after_commit
    decisions = {row.account_id: row.accepted for row in handover.decisions.all()}
    for notice in AddonHandoverProposalNotice.objects.select_for_update().filter(
            handover=handover, status='SENT', message__isnull=False).select_related('message'):
        if handover.status == 'ACCEPTED':
            title, preview = '加购接替已生效', '各方已同意，请查看订单。'
        elif handover.status == 'REJECTED':
            title, preview = '加购接替未通过', '有人未同意，请查看订单。'
        elif handover.status == 'SUPERSEDED':
            title, preview = '加购接替方案已失效', '请查看订单当前状态。'
        elif decisions.get(notice.account_id) is not None:
            title, preview = '已处理加购接替方案', '你的决定已记录，请查看订单。'
        else:
            continue
        notice.message.title, notice.message.preview = title, preview
        notice.message.save(update_fields=['title', 'preview'])
        notify_messages_after_commit(notice.account_id, business_cycle=notice.business_cycle)


def deliver(notice_id):
    # Reconstruct the real handover capability before taking the notice locks;
    # this keeps the same order/money/receipt lock sequence as the business.
    from .models import AddonHandoverProposalNotice
    from orders.models import Order
    from orders.points_service_payment import needs_integrated_processing
    row = AddonHandoverProposalNotice.objects.filter(pk=notice_id).values(
        'handover_id', 'handover__kind', 'handover__status', 'handover__payment_id',
        'handover__payment__intent__order_id').first()
    if not row:
        return
    order = Order.objects.filter(pk=row['handover__payment__intent__order_id']).first()
    if (row['handover__status'] == 'PENDING' and order and needs_integrated_processing(order)
            and order.refund_records.count() >= 4):
        from orders.points_transfer_refund_access import post_third_addon_handover, post_third_standard_handover
        try:
            with transaction.atomic():
                if row['handover__kind'] == 'VOLUNTARY':
                    scope = post_third_addon_handover(order.pk, row['handover__payment_id'],
                                                      handover_id=row['handover_id'])
                else:
                    scope = post_third_standard_handover(order.pk, row['handover__payment_id'],
                                                         handover_id=row['handover_id'])
                with scope:
                    return _deliver_locked(notice_id)
        except ValidationError:
            # Keep the intent pending; a later recovery can recheck real facts.
            return
    return _deliver_locked(notice_id)


def _deliver_locked(notice_id):
    from club_accounts.models import ClubAccount
    from orders.models import Order, OrderAddonPayment, OrderAddonHandover
    from orders.addon_payment import verify_receipt
    from orders.addon_handover import current_responsibilities, _active_target, _target_commission
    from .models import AddonHandoverProposalNotice, Message
    from .notifier import notify_messages_after_commit
    initial = AddonHandoverProposalNotice.objects.filter(pk=notice_id).values('account_id', 'handover_id',
        'handover__payment_id', 'handover__payment__intent__order_id').first()
    if not initial:
        return
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=initial['handover__payment__intent__order_id']).first()
        payment = OrderAddonPayment.objects.select_for_update().select_related('intent', 'buyer_transaction__wallet').filter(
            pk=initial['handover__payment_id'], intent__order=order).first() if order else None
        handover = OrderAddonHandover.objects.select_for_update().select_related('source_segment', 'target_segment').filter(
            pk=initial['handover_id'], payment=payment).first() if payment else None
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        notice = AddonHandoverProposalNotice.objects.select_for_update().filter(pk=notice_id).first()
        if not notice or notice.status != 'PENDING':
            return
        if not handover or handover.status != 'PENDING':
            notice.status, notice.note = 'SKIPPED', '接替方案已结束或失效'
        elif order.status != Order.Status.IN_SERVICE:
            notice.status, notice.note = 'SKIPPED', '订单已不在服务中'
        else:
            source, target = handover.source_segment, handover.target_segment
            decisions = list(handover.decisions.all())
            expected = {(payment.intent.buyer_id, 'BOSS'), (source.provider_account_id, 'PROVIDER'),
                        (target.provider_account_id, 'PROVIDER')}
            own = next((row for row in decisions if row.account_id == notice.account_id and row.role == notice.role), None)
            source_cycle = order.business_cycle if notice.role == 'BOSS' else None
            if notice.role == 'PROVIDER' and notice.segment_id in (source.pk, target.pk):
                segment = source if notice.segment_id == source.pk else target
                if segment.provider_account_id == notice.account_id:
                    source_cycle = segment.provider_business_cycle
            valid = bool(own and len(expected) == 3 and len(decisions) == 3
                and {(row.account_id, row.role) for row in decisions} == expected
                and all((row.accepted is None) == (row.decided_at is None) for row in decisions)
                and payment.intent.buyer_id == order.customer_account_id
                and source.attribution.order_id == order.pk and target.attribution.order_id == order.pk
                and ((notice.role == 'BOSS' and notice.account_id == payment.intent.buyer_id and notice.segment_id is None)
                     or (notice.role == 'PROVIDER' and source_cycle is not None))
                and type(notice.business_cycle) is int and notice.business_cycle > 0
                and source_cycle == notice.business_cycle and handover.accepted_at is None)
            if valid:
                try:
                    verify_receipt(payment)
                    previous, shares = current_responsibilities(payment, lock=True)
                    actual = next((row for row in shares if row['is_current']), None)
                    active_target = _active_target(order, target.provider_account_id)
                    proposed_target = handover.shares.filter(is_current=True).first()
                    valid = bool((previous.pk if previous else None) == handover.previous_id and actual
                        and actual['segment_id'] == source.pk and actual['basis'] == handover.source_basis_before
                        and active_target.pk == target.pk and proposed_target and proposed_target.segment_id == target.pk
                        and proposed_target.commission_snapshot == _target_commission(target))
                except ValidationError:
                    valid = False
            if not valid:
                notice.status, notice.note = 'REVIEW', '接替责任或原业务周期待核对'
            elif not account or account.account_type != notice.role or not account.license_membership_active or account.business_cycle != notice.business_cycle:
                notice.status, notice.note = 'SKIPPED', '原账号业务周期已变化'
            elif own.accepted is not None:
                notice.status, notice.note = 'SKIPPED', '本人已处理该方案'
            else:
                notice.message = Message.objects.create(recipient_account=account, recipient_business_cycle=notice.business_cycle,
                    type='ORDER', title='加购接替方案待确认',
                    preview=f'请查看并确认「{payment.intent.addon_name}」的接替方案。'[:255],
                    related_order_id=order.pk, action_url=f'/pages/orderList/index?orderId={order.pk}')
                notice.status = 'SENT'
                notify_messages_after_commit(account.pk, business_cycle=notice.business_cycle)
        notice.completed_at = timezone.now()
        notice.save(update_fields=['status', 'note', 'message', 'completed_at'])
