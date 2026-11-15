"""Recover a committed audition decision notification exactly once."""
import logging

from stores.db import transaction

log = logging.getLogger(__name__)


def deliver(signup_id):
    from audition.models import AuditionSignup
    from club_accounts.models import ClubAccount
    from .models import AuditionNoticeDelivery, Message
    from .notifier import notify_messages_after_commit

    hint = AuditionSignup.objects.filter(pk=signup_id).values('audit_notice_intent').first()
    intent = hint['audit_notice_intent'] if hint else None
    if not isinstance(intent, dict):
        return
    account_id = intent.get('account_id')
    try:
        with transaction.atomic():
            account = (ClubAccount.objects.select_for_update().filter(pk=account_id).first()
                       if type(account_id) is int and account_id > 0 else None)
            signup = AuditionSignup.objects.select_for_update().filter(pk=signup_id).first()
            if (not signup or signup.audit_notice_intent != intent or signup.audited_at is None
                    or signup.status not in ('APPROVED', 'REJECTED') or signup.status != intent.get('status')):
                return
            delivery, created = AuditionNoticeDelivery.objects.get_or_create(signup=signup)
            if not created:
                return
            cycle = intent.get('business_cycle')
            if (not account or account.account_type != 'PROVIDER' or not account.license_membership_active
                    or type(cycle) is not int or cycle <= 0 or account.business_cycle != cycle
                    or signup.applicant_account_id != account.pk or signup.applicant_business_cycle != cycle
                    or signup.applicant_id != intent.get('user_id')):
                delivery.skipped_reason = '原报名账号已离店、周期变化或来源不可核验'
                delivery.save(update_fields=['skipped_reason'])
                return
            delivery.message = Message.objects.create(recipient_account=account, recipient_id=signup.applicant_id,
                recipient_business_cycle=cycle, title=str(intent.get('title', ''))[:100],
                preview=str(intent.get('preview', ''))[:255], detail=str(intent.get('detail', '')))
            delivery.save(update_fields=['message'])
            notify_messages_after_commit(account.pk, business_cycle=cycle)
    except Exception:
        log.exception('试音审核通知待恢复 signup_id=%s', signup_id)
