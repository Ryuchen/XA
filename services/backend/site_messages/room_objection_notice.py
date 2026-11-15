"""Project store-scoped notices from actual room objection events."""
from django.conf import settings
from django.utils import timezone
from stores.db import transaction


def enabled():
    return (getattr(settings, 'ROOM_OBJECTION_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))


def record(objection, *, account_id, kind, cycle, title, preview):
    from .models import RoomObjectionNotice
    if not account_id:
        return
    expected = {'business_cycle': cycle, 'title': title[:100], 'preview': preview[:255]}
    intent, created = RoomObjectionNotice.objects.get_or_create(objection=objection, account_id=account_id,
                                                                 kind=kind, defaults=expected)
    if not created and any(getattr(intent, key) != value for key, value in expected.items()):
        raise ValueError('房间异议通知来源不一致')
    transaction.on_commit(lambda: deliver(intent.pk), robust=True)


def record_open(objection):
    from club_accounts.models import ClubAccount
    from console.models import AdminMembership
    from orders.reservation_permissions import staff_can
    memberships = AdminMembership.objects.filter(is_active=True, account__is_active=True,
        account__can_login=True, account__account_type=ClubAccount.AccountType.STAFF).select_related('account')
    for membership in memberships:
        account = membership.account
        if staff_can(account, 'order:view') and staff_can(account, 'support:edit'):
            record(objection, account_id=account.pk, kind='OPEN', cycle=account.business_cycle,
                title='房间异议待处理', preview='本店有订单房间异议，请在后台查看处理。')


def deliver(intent_id):
    from club_accounts.models import ClubAccount
    from console.models import AdminMembership
    from orders.models import Order, OrderRoomObjection
    from orders.reservation_permissions import staff_can
    from .models import RoomObjectionNotice, Message
    from .notifier import notify_messages_after_commit

    initial = RoomObjectionNotice.objects.filter(pk=intent_id).values('account_id', 'objection_id').first()
    if not initial:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        order_id = OrderRoomObjection.objects.filter(pk=initial['objection_id']).values_list('room__order_id', flat=True).first()
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        objection = (OrderRoomObjection.objects.select_for_update().filter(pk=initial['objection_id'],
            room__order=order).first() if order else None)
        intent = RoomObjectionNotice.objects.select_for_update().filter(pk=intent_id,
            account_id=initial['account_id'], objection_id=initial['objection_id']).first()
        if not intent or intent.completed_at:
            return
        cycle = intent.business_cycle
        valid = bool(account and type(cycle) is int and cycle > 0 and account.business_cycle == cycle
                     and account.license_membership_active and order and objection
                     and objection.customer_id == order.customer_account_id)
        if valid and intent.kind == 'OPEN':
            valid = bool(objection.status == 'OPEN' and account.account_type == 'STAFF'
                         and account.is_active and account.can_login
                         and AdminMembership.objects.filter(account=account, is_active=True).exists()
                         and staff_can(account, 'order:view') and staff_can(account, 'support:edit'))
        elif valid and intent.kind == 'RESOLVED':
            valid = bool(objection.status == 'RESOLVED' and objection.resolved_at and objection.resolved_by_id
                         and objection.resolution and objection.resolution_request_id and account.account_type == 'BOSS'
                         and account.pk == objection.customer_id and order.business_cycle == cycle)
        else:
            valid = False
        if not valid:
            intent.skipped_reason = '异议来源、处理权限或收件账号周期已变化'
        else:
            intent.message = Message.objects.create(recipient_account=account, recipient_business_cycle=cycle,
                type='SUPPORT' if intent.kind == 'OPEN' else 'ORDER', title=intent.title, preview=intent.preview,
                related_order_id=order.pk if intent.kind == 'RESOLVED' else None)
            notify_messages_after_commit(account.pk, business_cycle=cycle)
        intent.completed_at = timezone.now()
        intent.save(update_fields=['message', 'skipped_reason', 'completed_at'])
