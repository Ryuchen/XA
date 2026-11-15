"""Project the real final decision of an add-on handover to its parties."""
from django.conf import settings
from django.utils import timezone
from stores.db import transaction


def enabled():
    return (getattr(settings, 'ADDON_HANDOVER_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))


def record_all(handover):
    if not enabled() or handover.status not in ('ACCEPTED', 'REJECTED'):
        return
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('接替结果通知必须与确认事实同事务保存')
    from .models import AddonHandoverResultNotice
    payment, source, target = handover.payment, handover.source_segment, handover.target_segment
    order = payment.intent.order
    recipients = [(payment.intent.buyer_id, 'BOSS', None, order.business_cycle),
                  (source.provider_account_id, 'PROVIDER', source.pk, source.provider_business_cycle),
                  (target.provider_account_id, 'PROVIDER', target.pk, target.provider_business_cycle)]
    title = '加购接替已生效' if handover.status == 'ACCEPTED' else '加购接替未通过'
    preview = ('各方已同意，加购接替已生效，请查看订单。' if handover.status == 'ACCEPTED'
               else '有人未同意，加购接替未生效，请查看订单。')
    for account_id, role, segment_id, cycle in recipients:
        if not account_id:
            continue
        from .models import AddonHandoverProposalNotice
        if AddonHandoverProposalNotice.objects.filter(handover=handover, account_id=account_id,
                                                       status='SENT', message__isnull=False).exists():
            continue
        expected = {'role': role, 'segment_id': segment_id, 'business_cycle': cycle,
                    'result': handover.status, 'title': title, 'preview': preview}
        intent, created = AddonHandoverResultNotice.objects.get_or_create(handover=handover,
            account_id=account_id, defaults=expected)
        if not created and any(getattr(intent, key) != value for key, value in expected.items()):
            raise ValueError('加购接替通知来源不一致')
        transaction.on_commit(lambda pk=intent.pk: deliver(pk), robust=True)


def deliver(intent_id):
    from club_accounts.models import ClubAccount
    from orders.models import Order, OrderAddonHandover, OrderAddonPayment, OrderParticipationSegment
    from .models import AddonHandoverResultNotice, Message
    from .notifier import notify_messages_after_commit

    initial = AddonHandoverResultNotice.objects.filter(pk=intent_id).values('account_id', 'handover_id').first()
    if not initial:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        source = OrderAddonHandover.objects.filter(pk=initial['handover_id']).values(
            'payment_id', 'payment__intent__order_id').first()
        order = Order.objects.select_for_update().filter(pk=source['payment__intent__order_id']).first() if source else None
        payment = (OrderAddonPayment.objects.select_for_update().filter(pk=source['payment_id'],
            intent__order=order).first() if order else None)
        handover = OrderAddonHandover.objects.select_for_update().filter(pk=initial['handover_id'], payment=payment).first() if payment else None
        intent = AddonHandoverResultNotice.objects.select_for_update().filter(pk=intent_id,
            account_id=initial['account_id'], handover_id=initial['handover_id']).first()
        if not intent or intent.completed_at:
            return
        cycle = intent.business_cycle
        source_segment = handover.source_segment if handover else None
        target_segment = handover.target_segment if handover else None
        source_valid = bool(order and payment and handover and source_segment and target_segment
            and handover.status == intent.result and intent.result in ('ACCEPTED', 'REJECTED')
            and payment.intent.buyer_id == order.customer_account_id
            and source_segment.attribution.order_id == order.pk and target_segment.attribution.order_id == order.pk
            and source_segment.provider_account_id != target_segment.provider_account_id)
        if source_valid:
            expected = {(payment.intent.buyer_id, 'BOSS'), (source_segment.provider_account_id, 'PROVIDER'),
                        (target_segment.provider_account_id, 'PROVIDER')}
            decisions = list(handover.decisions.values_list('account_id', 'role', 'accepted', 'decided_at'))
            source_valid = bool(len(expected) == 3 and len(decisions) == 3
                and {(pk, role) for pk, role, _, _ in decisions} == expected
                and all((value is None) == (when is None) and (when is None or when >= handover.created_at)
                        for _, _, value, when in decisions))
            if source_valid and intent.result == 'ACCEPTED':
                source_valid = (all(value is True for _, _, value, _ in decisions) and handover.accepted_at
                                and handover.accepted_at == max(when for _, _, _, when in decisions))
            elif source_valid:
                source_valid = bool(handover.accepted_at is None and any(value is False for _, _, value, _ in decisions))
        if intent.role == 'BOSS':
            own_source = bool(source_valid and intent.account_id == order.customer_account_id
                              and intent.segment_id is None and cycle == order.business_cycle)
        else:
            segment = OrderParticipationSegment.objects.filter(pk=intent.segment_id,
                attribution__order=order, provider_account_id=intent.account_id).first() if order else None
            own_source = bool(source_valid and intent.role == 'PROVIDER' and segment
                and segment.pk in (handover.source_segment_id, handover.target_segment_id)
                and segment.provider_business_cycle == cycle)
        if (not own_source or type(cycle) is not int or cycle <= 0 or not account
                or account.account_type != intent.role or not account.license_membership_active
                or account.business_cycle != cycle):
            intent.skipped_reason = '接替结果不可核验、已变化或账号周期已变化'
        else:
            intent.message = Message.objects.create(recipient_account=account, recipient_business_cycle=cycle,
                type='ORDER', title=intent.title, preview=intent.preview, related_order_id=order.pk)
            notify_messages_after_commit(account.pk, business_cycle=cycle)
        intent.completed_at = timezone.now()
        intent.save(update_fields=['message', 'skipped_reason', 'completed_at'])
