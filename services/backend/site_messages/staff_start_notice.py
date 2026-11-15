"""Recover existing staff-start messages from the committed start event."""
from django.conf import settings
from django.utils import timezone
from stores.db import transaction


def enabled():
    return (getattr(settings, 'STAFF_START_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))


def record_all(order, status_log):
    from .models import StaffStartNotice
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('客服开工通知必须与开始服务同事务保存')
    rows = list(order.providers.filter(released_at__isnull=True).order_by('pk'))
    for row in rows:
        if not row.provider_id:
            continue
        intent, created = StaffStartNotice.objects.get_or_create(status_log=status_log, assignment=row,
            defaults={'account_id': row.provider_account_id, 'business_cycle': row.provider_business_cycle,
                      'legacy_user_id': row.provider_id,
                      'preview': f'「{order.service_name_snapshot}」已进入服务中'[:255]})
        if not created and (intent.account_id != row.provider_account_id
                or intent.business_cycle != row.provider_business_cycle or intent.legacy_user_id != row.provider_id):
            raise ValueError('客服开工通知来源不一致')
        transaction.on_commit(lambda pk=intent.pk: deliver(pk), robust=True)


def deliver(intent_id):
    from club_accounts.models import ClubAccount, LegacyAccountMap
    from orders.models import Order, OrderProvider, OrderStatusLog
    from .models import Message, StaffStartNotice
    from .notifier import notify_messages_after_commit
    initial = StaffStartNotice.objects.filter(pk=intent_id).values('account_id', 'status_log_id').first()
    if not initial:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        order_id = OrderStatusLog.objects.filter(pk=initial['status_log_id']).values_list('order_id', flat=True).first()
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        log = OrderStatusLog.objects.filter(pk=initial['status_log_id'], order=order).first() if order else None
        intent = StaffStartNotice.objects.select_for_update().filter(pk=intent_id,
            status_log_id=initial['status_log_id'], account_id=initial['account_id']).first()
        if not intent or intent.status != 'PENDING':
            return
        cycle = intent.business_cycle
        if not intent.account_id or type(cycle) is not int or cycle <= 0:
            intent.status, intent.note = 'REVIEW', '原陪玩账号或业务周期缺失，待核对'
        else:
            assignment = OrderProvider.objects.filter(pk=intent.assignment_id, order=order).first() if order else None
            mapping = (intent.legacy_user_id is None or LegacyAccountMap.objects.filter(
                account_id=intent.account_id, legacy_user_id=intent.legacy_user_id).exists())
            proof = bool(order and log and log.action == 'START' and log.from_status == 'GRABBED'
                and log.to_status == 'IN_SERVICE' and log.operator_account_id
                and log.operator_account.account_type == 'STAFF' and assignment
                and assignment.provider_account_id == intent.account_id
                and assignment.provider_business_cycle == cycle and assignment.provider_id == intent.legacy_user_id)
            if not proof:
                intent.status, intent.note = 'REVIEW', '开工事件或席位来源待核对'
            elif (order.status != 'IN_SERVICE' or assignment.released_at or not mapping or not account
                    or account.account_type != 'PROVIDER' or not account.license_membership_active
                    or account.business_cycle != cycle):
                intent.status, intent.note = 'SKIPPED', '订单、席位或原账号周期已变化'
            else:
                intent.message = Message.objects.create(recipient_account=account, recipient_id=intent.legacy_user_id,
                    recipient_business_cycle=cycle, type='ORDER', title='订单已开始服务', preview=intent.preview,
                    related_order_id=order.pk, action_url=f'/pages/orderList/index?orderId={order.pk}')
                notify_messages_after_commit(account.pk, business_cycle=cycle)
                intent.status = 'SENT'
        intent.completed_at = timezone.now()
        intent.save(update_fields=['status', 'note', 'message', 'completed_at'])
