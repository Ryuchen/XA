"""Project committed full-attendance facts without repeating the reward."""
import logging

from stores.db import transaction

log = logging.getLogger(__name__)


def deliver(progress_id):
    from club_accounts.models import ClubAccount
    from users.models import CheckinMonthProgress
    from .models import CheckinNoticeDelivery, Message
    from .notifier import notify_messages_after_commit

    hint = CheckinMonthProgress.objects.filter(pk=progress_id, full_attendance_notice_intent=True).values(
        'account_id', 'full_attendance_notice_cycle').first()
    if not hint or not hint['account_id'] or not hint['full_attendance_notice_cycle']:
        return
    try:
        with transaction.atomic():
            account = ClubAccount.objects.select_for_update().filter(pk=hint['account_id']).first()
            progress = CheckinMonthProgress.objects.select_for_update().filter(pk=progress_id,
                account_id=hint['account_id'], full_attendance_notice_intent=True,
                full_attendance_notice_cycle=hint['full_attendance_notice_cycle'],
                full_attendance_awarded=True, full_attendance_awarded_at__isnull=False).first()
            if not progress:
                return
            delivery, created = CheckinNoticeDelivery.objects.get_or_create(progress=progress)
            if not created:
                return
            if (not account or account.account_type != 'BOSS' or not account.license_membership_active
                    or account.business_cycle != progress.full_attendance_notice_cycle):
                delivery.skipped_reason = '原签到账号已离店或周期已变化'
                delivery.save(update_fields=['skipped_reason'])
                return
            delivery.message = Message.objects.create(recipient_id=progress.user_id, recipient_account=account,
                recipient_business_cycle=progress.full_attendance_notice_cycle,
                title='月度全勤奖励已获得', preview=f'恭喜获得 {progress.full_attendance_reward_name}'[:255],
                detail=progress.full_attendance_notice_detail, action_url='/pages/checkin/index')
            delivery.save(update_fields=['message'])
            notify_messages_after_commit(account.pk, business_cycle=progress.full_attendance_notice_cycle)
    except Exception:
        # No result is committed on failure; the periodic store scan retries.
        log.exception('签到奖励消息待恢复 progress_id=%s', progress_id)
