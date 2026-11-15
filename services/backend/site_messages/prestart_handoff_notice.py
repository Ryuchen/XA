"""Store-local notices for actual staff pre-service reassignment."""
from django.conf import settings
from django.utils import timezone
from stores.db import transaction


def enabled():
    return (getattr(settings, 'PRESTART_HANDOFF_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))


def record_all(order, status_log, handoffs):
    if not enabled():
        return
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('改派通知必须与改派事实同事务保存')
    from .models import PrestartHandoffNotice
    if not status_log or not handoffs or any(h.order_id != order.pk for h in handoffs):
        raise ValueError('改派通知缺少实际改派记录')
    ids = [h.pk for h in handoffs]
    recipients = [(order.customer_account_id, 'BOSS', None, order.business_cycle)]
    for handoff in handoffs:
        recipients.extend([(handoff.old_provider_id, 'OLD', handoff.pk, handoff.old_snapshot.get('provider_business_cycle')),
                           (handoff.new_provider_id, 'NEW', handoff.pk, handoff.new_snapshot.get('provider_business_cycle'))])
    for account_id, role, handoff_id, cycle in recipients:
        if not account_id:
            continue
        expected = {'role': role, 'handoff_id': handoff_id, 'business_cycle': cycle, 'handoff_ids': ids}
        intent, created = PrestartHandoffNotice.objects.get_or_create(status_log=status_log,
            account_id=account_id, defaults=expected)
        if not created and any(getattr(intent, key) != value for key, value in expected.items()):
            raise ValueError('改派通知来源不一致')
        transaction.on_commit(lambda pk=intent.pk: deliver(pk), robust=True)


def deliver(intent_id):
    from club_accounts.models import ClubAccount
    from orders.models import Order, OrderPrestartHandoff, OrderStatusLog
    from .models import Message, PrestartHandoffNotice
    from .notifier import notify_messages_after_commit
    initial = PrestartHandoffNotice.objects.filter(pk=intent_id).values('account_id', 'status_log_id').first()
    if not initial:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        order_id = OrderStatusLog.objects.filter(pk=initial['status_log_id']).values_list('order_id', flat=True).first()
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        log = OrderStatusLog.objects.select_for_update().filter(pk=initial['status_log_id'], order=order).first() if order else None
        intent = PrestartHandoffNotice.objects.select_for_update().filter(pk=intent_id,
            account_id=initial['account_id'], status_log_id=initial['status_log_id']).first()
        if not intent or intent.completed_at:
            return
        ids = intent.handoff_ids
        ids_valid = isinstance(ids, list) and 1 <= len(ids) <= 2 and all(type(x) is int and x > 0 for x in ids) and len(set(ids)) == len(ids)
        events = list(OrderPrestartHandoff.objects.filter(order=order, pk__in=ids).order_by('pk')) if order and ids_valid else []
        proof = bool(log and ids_valid and len(events) == len(ids) and log.action == OrderStatusLog.Action.TRANSFER
            and log.from_status == Order.Status.GRABBED and log.to_status == Order.Status.GRABBED
            and all(e.operator_id == log.operator_account_id and e.created_at <= log.created_at and e.old_provider_id != e.new_provider_id
                    and isinstance(e.old_snapshot, dict) and isinstance(e.new_snapshot, dict)
                    and e.old_snapshot.get('provider_account_id') == e.old_provider_id
                    and e.new_snapshot.get('provider_account_id') == e.new_provider_id for e in events))
        cycle = intent.business_cycle
        if intent.role == 'BOSS':
            own_source = bool(proof and intent.handoff_id is None and order.customer_account_id == intent.account_id
                              and order.business_cycle == cycle)
        else:
            event = next((e for e in events if e.pk == intent.handoff_id), None)
            snapshot = (event.old_snapshot if intent.role == 'OLD' else event.new_snapshot) if event else {}
            owner = (event.old_provider_id if intent.role == 'OLD' else event.new_provider_id) if event else None
            own_source = bool(proof and intent.role in ('OLD', 'NEW') and owner == intent.account_id
                              and snapshot.get('provider_business_cycle') == cycle)
        stale = bool(events and OrderPrestartHandoff.objects.filter(order=order, pk__gt=max(ids)).exists())
        expected_type = 'BOSS' if intent.role == 'BOSS' else 'PROVIDER'
        if (not own_source or stale or order.status not in (Order.Status.GRABBED, Order.Status.IN_SERVICE)
                or type(cycle) is not int or cycle <= 0 or not account or account.account_type != expected_type
                or not account.license_membership_active or account.business_cycle != cycle):
            intent.skipped_reason = '改派来源不可核验、安排已变化或收件账号周期已变化'
        else:
            preview = ('客服已调整服务前陪玩，请查看订单当前安排。' if intent.role == 'BOSS'
                       else '客服已调整服务安排，请查看订单详情。')
            intent.message = Message.objects.create(recipient_account=account, recipient_business_cycle=cycle,
                type='ORDER', title='服务安排已调整', preview=preview, related_order_id=order.pk)
            notify_messages_after_commit(account.pk, business_cycle=cycle)
        intent.completed_at = timezone.now()
        intent.save(update_fields=['message', 'skipped_reason', 'completed_at'])
