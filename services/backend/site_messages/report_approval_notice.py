"""Persist and retry the actual provider report approval notification."""
from django.conf import settings
from django.utils import timezone
from stores.db import transaction


def enabled():
    return (getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
            and getattr(settings, 'REPORT_APPROVAL_NOTICE_RECOVERY_ENABLED', False))


def record(report, order, cycle, preview, *, kind='APPROVED', detail=''):
    from .models import ReportApprovalNotice
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('报单通知须与审核同事务保存')
    if kind not in ('APPROVED', 'REJECTED') or report.status != kind:
        raise ValueError('报单审核通知来源状态无效')
    intent = ReportApprovalNotice.objects.create(report=report, account_id=report.provider_account_id,
        business_cycle=cycle, audited_at=report.audited_at, kind=kind, preview=preview, detail=detail)
    transaction.on_commit(lambda: deliver(intent.pk), robust=True)


def deliver(pk):
    from club_accounts.models import ClubAccount
    from wallet.models import ProviderReport
    from .models import Message, ReportApprovalNotice
    from .notifier import notify_messages_after_commit
    from .order_cycle import recipient_cycle
    info = ReportApprovalNotice.objects.filter(pk=pk).values('account_id', 'report_id').first()
    if not info:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=info['account_id']).first()
        report = ProviderReport.objects.select_for_update().filter(pk=info['report_id']).first()
        intent = ReportApprovalNotice.objects.select_for_update().filter(pk=pk).first()
        if not intent or intent.status != 'PENDING':
            return
        if not report or report.provider_account_id != intent.account_id or not report.order_id:
            intent.status, intent.note = 'REVIEW', '报单来源待核对'
        elif (report.status != intent.kind or report.audited_at != intent.audited_at
              or (intent.kind == 'REJECTED' and intent.detail != f'驳回原因：{report.audit_remark}')):
            intent.status, intent.note = 'SKIPPED', '原审核状态已变化'
        elif not intent.business_cycle or recipient_cycle(report.order, intent.account_id) != intent.business_cycle:
            intent.status, intent.note = 'REVIEW', '报单原业务周期待核对'
        elif not account or not account.license_membership_active or account.business_cycle != intent.business_cycle:
            intent.status, intent.note = 'SKIPPED', '接收人已退出原业务周期'
        else:
            intent.message = Message.objects.create(recipient_account=account, recipient_id=report.provider_id,
                recipient_business_cycle=intent.business_cycle, type='ORDER',
                title='报单审核通过' if intent.kind == 'APPROVED' else '报单审核未通过',
                preview=intent.preview, detail=intent.detail, related_order_id=report.order_id, action_url='/pages/report/index')
            intent.status = 'SENT'
            notify_messages_after_commit(account.pk, business_cycle=intent.business_cycle)
        intent.completed_at = timezone.now()
        intent.save(update_fields=['message', 'status', 'note', 'completed_at'])
