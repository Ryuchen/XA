"""Notify the original recipients when staff commit a service transfer."""
from django.conf import settings
from django.utils import timezone
from stores.db import transaction

from .service_transfer_notice import enabled


def record_or_schedule(order, log, events, previous_assignments, *, transferred_at, subsidy_segment):
    """Capture one recipient per staff action, before the order transaction commits."""
    from .models import ServiceTransferAssignmentNotice

    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('客服转单通知必须与转单同事务登记')
    incoming_ids = {event.new_account_id for event in events}
    incoming = {row.provider_account_id: row for row in order.providers.filter(provider_account_id__in=incoming_ids)}
    targets = [(None, 'BOSS', '已为你更换大神', f'你的「{order.service_name_snapshot}」已更换服务大神')]
    for event in events:
        old = previous_assignments[event.old_account_id]
        if subsidy_segment:
            guidance = '请按履约情况提交报单，收益将在整单完成后核算'
        elif old.settlement_base <= 0:
            guidance = '本单无保留份额，无需提交报单'
        elif order.settlement_mode == order.SettlementMode.REPORT_APPROVAL:
            guidance = '保留收益待整单报单通过后结算'
        else:
            guidance = '本次转单份额已结算'
        targets.extend([
            (old, 'OLD', '订单已转出', f'你的「{order.service_name_snapshot}」已转交其他大神，{guidance}'),
            (incoming[event.new_account_id], 'NEW', '客服转单给你',
             f'客服为你转入「{order.service_name_snapshot}」，请接手并继续服务，完成后提交结单截图'),
        ])
    if order.escort_mode == order.EscortMode.DOUBLE:
        changed = {event.old_account_id for event in events} | incoming_ids
        targets.extend((row, 'TEAM', '服务搭档已更换',
                        f'「{order.service_name_snapshot}」的服务搭档已更换，请查看订单并确认服务安排')
                       for row in previous_assignments.values()
                       if row.provider_account_id not in changed and not row.released_at)

    event_ids = [event.pk for event in events]
    persistent = enabled()
    guarded = getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
    for assignment, role, title, preview in targets:
        account_id, user_id, cycle = ((assignment.provider_account_id, assignment.provider_id,
                                       assignment.provider_business_cycle) if assignment else
                                      (order.customer_account_id, order.customer_id, order.business_cycle))
        if persistent and account_id:
            evidence = {'assignment': assignment, 'role': role, 'seat_event_ids': event_ids,
                        'transferred_at': transferred_at, 'business_cycle': cycle, 'legacy_user_id': user_id,
                        'title': title, 'preview': preview[:255]}
            intent, created = ServiceTransferAssignmentNotice.objects.get_or_create(status_log=log,
                account_id=account_id, defaults=evidence)
            if not created and any(getattr(intent, key) != value for key, value in evidence.items()):
                raise ValueError('客服转单通知来源不一致')
            transaction.on_commit(lambda pk=intent.pk: deliver(pk), robust=True)
        else:
            from orders.services import push_message_safe
            transaction.on_commit(lambda aid=account_id, uid=user_id, original_cycle=cycle, subject=title,
                body=preview, alert=role == 'NEW': push_message_safe(recipient_account_id=aid, recipient_id=uid,
                    recipient_business_cycle=original_cycle, require_cycle=guarded or bool(original_cycle),
                    title=subject, preview=body, msg_type='ORDER', related_order_id=order.pk,
                    assignment_alert=alert), robust=True)


def _source(order, intent):
    from orders.models import OrderProvider, OrderStatusLog, OrderTransferSeatEvent

    log = intent.status_log
    ids = intent.seat_event_ids
    if (log.order_id != order.pk or log.action != OrderStatusLog.Action.TRANSFER
            or log.from_status != order.Status.IN_SERVICE or log.to_status != order.Status.IN_SERVICE
            or not log.operator_account_id or log.operator_account.account_type != 'STAFF'
            or intent.transferred_at > log.created_at or not isinstance(ids, list) or not 1 <= len(ids) <= 2
            or any(type(pk) is not int or pk <= 0 for pk in ids) or len(ids) != len(set(ids))):
        return None, None
    events = list(OrderTransferSeatEvent.objects.filter(pk__in=ids, seat__order=order,
        created_at__lte=intent.transferred_at).select_related('seat'))
    if len(events) != len(ids) or len({event.seat_id for event in events}) != len(ids):
        return None, None
    if intent.role == 'BOSS':
        if (not intent.assignment_id and order.customer_account_id == intent.account_id
                and order.customer_id == intent.legacy_user_id and order.business_cycle == intent.business_cycle):
            return 'BOSS', True
        return None, None
    assignment = OrderProvider.objects.filter(pk=intent.assignment_id, order=order,
        provider_account_id=intent.account_id, provider_id=intent.legacy_user_id,
        provider_business_cycle=intent.business_cycle).first()
    if not assignment:
        return None, None
    if intent.role == 'OLD' and any(event.old_account_id == intent.account_id for event in events):
        return 'PROVIDER', assignment.released_at == intent.transferred_at
    active = not assignment.released_at and order.status == order.Status.IN_SERVICE
    if intent.role == 'NEW':
        event = next((row for row in events if row.new_account_id == intent.account_id), None)
        if event:
            return 'PROVIDER', bool(active and event.seat.current_account_id == intent.account_id
                                    and event.seat.transfer_count == event.sequence)
    if intent.role == 'TEAM' and order.escort_mode == order.EscortMode.DOUBLE and len(events) == 1:
        moved = events[0]
        teammate_seat = order.transfer_limit_seats.exclude(pk=moved.seat_id).first()
        if teammate_seat:
            previous = teammate_seat.events.filter(pk__lt=moved.pk).order_by('-sequence').first()
            teammate_id = previous.new_account_id if previous else teammate_seat.initial_account_id
            if teammate_id == intent.account_id:
                return 'PROVIDER', active
    return None, None


def deliver(intent_id):
    from club_accounts.models import ClubAccount
    from orders.models import Order
    from .models import Message, ServiceTransferAssignmentNotice
    from .notifier import notify_messages_after_commit

    initial = ServiceTransferAssignmentNotice.objects.filter(pk=intent_id).values('account_id', 'status_log__order_id').first()
    if not initial:
        return
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=initial['status_log__order_id'])
        account = ClubAccount.objects.select_for_update().get(pk=initial['account_id'])
        intent = ServiceTransferAssignmentNotice.objects.select_for_update().get(pk=intent_id)
        if intent.completed_at:
            return
        cycle = intent.business_cycle
        account_type, current_assignment = _source(order, intent)
        if not account_type or type(cycle) is not int or cycle <= 0:
            intent.skipped_reason = '客服转单来源或原业务周期待核对'
        elif (not current_assignment or account.account_type != account_type or not account.license_membership_active
                or not account.is_active or not account.can_login or account.business_cycle != cycle):
            intent.skipped_reason = '订单、席位或收件账号周期已变化'
        else:
            alert = intent.role == 'NEW'
            intent.message = Message.objects.create(recipient_account=account, recipient_id=intent.legacy_user_id,
                recipient_business_cycle=cycle, type=Message.Type.ORDER, title=intent.title, preview=intent.preview,
                related_order_id=order.pk, action_url=f'/pages/orderList/index?orderId={order.pk}', assignment_alert=alert)
            notify_messages_after_commit(account.pk, business_cycle=cycle,
                                         assignment_message_id=intent.message_id if alert else None)
        intent.completed_at = timezone.now()
        intent.save(update_fields=['message', 'skipped_reason', 'completed_at'])
