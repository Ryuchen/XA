"""Per-store staff notices for actual provider deposit applications."""
from django.conf import settings
from django.utils import timezone
from stores.db import transaction


def enabled():
    return bool(getattr(settings, 'DEPOSIT_APPLICATION_NOTICE_RECOVERY_ENABLED', False))


def record_all(application, *, retry=False):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('押金申请通知须与申请同事务保存')
    from club_accounts.models import ClubAccount
    from console.permissions import get_account_permissions, is_console_account
    from .models import DepositApplicationNotice
    recipients = [row for row in ClubAccount.objects.filter(account_type='STAFF', is_active=True, can_login=True)
                  if is_console_account(row) and 'escort:deposit_refund' in get_account_permissions(row)]
    queued, already_sent = 0, 0
    for account in recipients:
        notice, created = DepositApplicationNotice.objects.get_or_create(application=application, account=account,
            defaults={'business_cycle': account.business_cycle})
        if not created and notice.business_cycle != account.business_cycle:
            if retry:
                continue
            raise ValueError('押金申请通知接收周期不一致')
        if notice.status == 'SENT':
            already_sent += 1
            continue
        if (retry and notice.status == 'SKIPPED'
                and notice.note == '客服账号周期或处理权限已变化'):
            notice.status, notice.note, notice.completed_at = 'PENDING', '', None
            notice.save(update_fields=['status', 'note', 'completed_at'])
        if notice.status == 'PENDING':
            queued += 1
            transaction.on_commit(lambda pk=notice.pk: deliver(pk), robust=True)
    application.notification_state = 'QUEUED' if queued or already_sent else 'NO_STAFF'
    application.save(update_fields=['notification_state'])
    return {'queued': queued, 'already_recorded': already_sent, 'state': application.notification_state}


def record_rejection(application):
    if not transaction.get_connection().in_atomic_block or application.status != 'REJECTED':
        raise RuntimeError('拒绝通知须与申请处理同事务保存')
    from .models import DepositApplicationNotice
    notice, created = DepositApplicationNotice.objects.get_or_create(application=application,
        account_id=application.account_id, defaults={'business_cycle': application.business_cycle})
    if not created and notice.business_cycle != application.business_cycle:
        raise ValueError('申请人通知周期不一致')
    transaction.on_commit(lambda pk=notice.pk: deliver(pk), robust=True)


def deliver(notice_id):
    from club_accounts.models import ClubAccount
    from console.permissions import get_account_permissions, is_console_account
    from wallet.models import DepositRefundApplication
    from .models import DepositApplicationNotice, Message
    from .notifier import notify_messages_after_commit
    hint = DepositApplicationNotice.objects.filter(pk=notice_id).values('account_id', 'application_id').first()
    if not hint:
        return
    with transaction.atomic():
        application = DepositRefundApplication.objects.select_for_update().filter(pk=hint['application_id']).first()
        account = ClubAccount.objects.select_for_update().filter(pk=hint['account_id']).first()
        notice = DepositApplicationNotice.objects.select_for_update().filter(pk=notice_id).first()
        if not notice or notice.status != 'PENDING':
            return
        owner_notice = bool(application and notice.account_id == application.account_id)
        if not application or (owner_notice and application.status != 'REJECTED') or (not owner_notice and application.status != 'PENDING'):
            notice.status, notice.note = 'SKIPPED', '申请已处理或取消'
        elif (application.profile.account_id != application.account_id
              or application.account.business_cycle != application.business_cycle
              or not application.account.license_membership_active):
            notice.status, notice.note = 'SKIPPED', '申请人原业务周期已变化'
        elif type(notice.business_cycle) is not int or notice.business_cycle <= 0:
            notice.status, notice.note = 'REVIEW', '客服原业务周期待核对'
        elif (not account or account.business_cycle != notice.business_cycle
              or (owner_notice and (account.account_type != 'PROVIDER' or not account.license_membership_active))
              or (not owner_notice and (account.account_type != 'STAFF' or not is_console_account(account)
                  or 'escort:deposit_refund' not in get_account_permissions(account)))):
            notice.status, notice.note = 'SKIPPED', '客服账号周期或处理权限已变化'
        else:
            notice.message = Message.objects.create(recipient_account=account,
                recipient_business_cycle=notice.business_cycle, type='SYSTEM',
                title='退押金申请未通过' if owner_notice else '有退押金申请',
                preview='请查看申请处理原因。' if owner_notice else '陪玩提交了退押金申请，请核验资格后处理。')
            notice.status = 'SENT'
            notify_messages_after_commit(account.pk, business_cycle=notice.business_cycle)
        notice.completed_at = timezone.now()
        notice.save(update_fields=['status', 'note', 'message', 'completed_at'])


def refresh_delivered(application):
    from .models import DepositApplicationNotice
    from .notifier import notify_messages_after_commit
    for notice in DepositApplicationNotice.objects.select_for_update().filter(
            application=application, status='SENT', message__isnull=False).select_related('message'):
        notice.message.title = '退押金申请已处理' if application.status == 'REFUNDED' else '退押金申请已结束'
        notice.message.preview = '请查看申请处理记录。'
        notice.message.save(update_fields=['title', 'preview'])
        notify_messages_after_commit(notice.account_id, business_cycle=notice.business_cycle)
