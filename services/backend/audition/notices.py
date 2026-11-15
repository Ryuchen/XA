"""Snapshot the original audition decision without repeating its audit."""
from django.conf import settings
from stores.db import transaction


def prepare(signup):
    approved = signup.status == signup.Status.APPROVED
    title = signup.link.title
    return {'account_id': signup.applicant_account_id, 'user_id': signup.applicant_id,
        'business_cycle': signup.applicant_business_cycle, 'status': signup.status,
        'title': '试音报名通过' if approved else '试音报名未通过',
        'preview': f'您报名的「{title}」已通过审核' if approved else f'您报名的「{title}」未通过审核',
        'detail': (f'恭喜！您报名的试音活动「{title}」已通过审核，请留意客服后续联系。' if approved
                   else f'很遗憾，您报名的试音活动「{title}」未通过。原因：{signup.audit_remark}')}


def recovery_enabled():
    return (getattr(settings, 'AUDITION_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))


def schedule(signup_id, intent):
    if recovery_enabled():
        from site_messages.audition_notice import deliver
        transaction.on_commit(lambda: deliver(signup_id), robust=True)
    else:
        from site_messages.utils import create_cycle_message
        def deliver_now():
            message = create_cycle_message(recipient_id=intent['user_id'], recipient_account_id=intent['account_id'],
                recipient_business_cycle=intent['business_cycle'], title=intent['title'],
                preview=intent['preview'], detail=intent['detail'])
            if message is None:
                import logging
                logging.getLogger(__name__).warning('试音审核通知未投递，报名原周期待核对 signup=%s', signup_id)
        transaction.on_commit(deliver_now, robust=True)
