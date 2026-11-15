"""Durable notifications projected from real order applications."""
import logging

from django.conf import settings
from django.utils import timezone
from stores.db import transaction

log = logging.getLogger(__name__)


def enabled():
    return bool(getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))


def record(application, account_id, kind, *, title, preview, action_url='', assignment_alert=False):
    from .models import OrderApplicationNotice
    if kind == 'BOSS_READY' and account_id == application.order.customer_account_id:
        cycle = application.order.business_cycle
    elif account_id == application.applicant_id:
        cycle = application.applicant_business_cycle
    elif account_id == application.partner_id:
        cycle = application.partner_business_cycle
    else:
        raise ValueError('报名通知收件人不是报名成员')
    intent, created = OrderApplicationNotice.objects.get_or_create(application=application, account_id=account_id,
        kind=kind, defaults={'business_cycle': cycle, 'title': title[:100], 'preview': preview[:255],
                             'action_url': action_url[:255], 'assignment_alert': assignment_alert})
    if created:
        transaction.on_commit(lambda: deliver(intent.pk), robust=True)


def deliver(intent_id):
    from club_accounts.models import ClubAccount
    from orders.models import Order, OrderApplication
    from .models import Message, OrderApplicationNotice
    from .notifier import notify_messages_after_commit

    initial = OrderApplicationNotice.objects.filter(pk=intent_id).values('account_id', 'application_id').first()
    if not initial:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        # Lifecycle account first, then order and application, matching the business lock order.
        order_id = OrderApplication.objects.filter(pk=initial['application_id']).values_list('order_id', flat=True).first()
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        application = OrderApplication.objects.select_for_update().filter(
            pk=initial['application_id'], order=order).first() if order else None
        intent = OrderApplicationNotice.objects.select_for_update().filter(pk=intent_id,
            account_id=initial['account_id'], application_id=initial['application_id']).first()
        if not intent or intent.completed_at:
            return
        cycle = intent.business_cycle
        source_cycle = None
        if application:
            if intent.kind == 'BOSS_READY' and intent.account_id == order.customer_account_id:
                source_cycle = order.business_cycle
            elif intent.account_id == application.applicant_id:
                source_cycle = application.applicant_business_cycle
            elif intent.account_id == application.partner_id:
                source_cycle = application.partner_business_cycle
        expected_type = 'BOSS' if intent.kind == 'BOSS_READY' else 'PROVIDER'
        valid = bool(application and order and type(cycle) is int and cycle > 0 and cycle == source_cycle
                     and account and account.account_type == expected_type and account.license_membership_active
                     and account.business_cycle == cycle)
        if valid and intent.kind == 'BOSS_READY':
            from orders.applications import application_data, audition_recordings_required
            valid = (order.status == Order.Status.PENDING and order.payment_status == Order.PaymentStatus.PAID
                     and (not order.auto_cancel_at or order.auto_cancel_at > timezone.now())
                     and audition_recordings_required(order) and application_data(application, order)['ready'])
        elif valid and intent.kind == 'INVITE':
            valid = (intent.account_id == application.partner_id and application.status == 'PENDING'
                     and not application.partner_confirmed and order.status == Order.Status.PENDING
                     and (not order.auto_cancel_at or order.auto_cancel_at > timezone.now()))
        elif valid and intent.kind == 'CONFIRM':
            valid = (intent.account_id == application.applicant_id and application.partner_confirmed
                     and application.status == 'PENDING' and order.status == Order.Status.PENDING)
        elif valid and intent.kind == 'WITHDRAW':
            valid = application.status == 'WITHDRAWN'
        elif valid and intent.kind == 'NOT_SELECTED':
            valid = application.status == 'NOT_SELECTED'
        elif valid and intent.kind == 'CANCELLED':
            valid = (application.status == 'PENDING' and order.status == Order.Status.CANCELLED
                     and order.status_logs.filter(from_status='PENDING', to_status='CANCELLED').exists())
        elif valid and intent.kind == 'SELECTED':
            latest = order.applications.filter(status='SELECTED').order_by('-pk').values_list('pk', flat=True).first()
            grab = order.status_logs.filter(to_status='GRABBED').order_by('-pk').first()
            active = set(order.providers.filter(released_at__isnull=True).values_list('provider_account_id', flat=True))
            active.discard(None)
            if order.provider_account_id:
                active.add(order.provider_account_id)
            selected = {application.applicant_id} | ({application.partner_id} if application.partner_id else set())
            boss_grab = grab and grab.action == 'GRAB' and (
                grab.operator_account_id == order.customer_account_id if grab.operator_account_id
                else bool(grab.operator_id and grab.operator_id == order.customer_id))
            valid = bool(application.status == 'SELECTED' and latest == application.pk and boss_grab
                         and active == selected and order.status == Order.Status.GRABBED
                         and not order.status_logs.filter(action='TRANSFER', pk__gt=grab.pk).exists())
        elif valid:
            valid = False
        if not valid:
            intent.skipped_reason = '报名来源不可核验、状态已变化或账号已重入'
        else:
            intent.message = Message.objects.create(recipient_account=account, recipient_business_cycle=cycle,
                type='ORDER', title=intent.title, preview=intent.preview, action_url=intent.action_url,
                related_order_id=order.pk, assignment_alert=intent.assignment_alert)
            notify_messages_after_commit(account.pk, business_cycle=cycle,
                assignment_message_id=intent.message_id if intent.assignment_alert else None)
        intent.completed_at = timezone.now()
        intent.save(update_fields=['message', 'skipped_reason', 'completed_at'])
