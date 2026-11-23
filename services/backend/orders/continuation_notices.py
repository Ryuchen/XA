"""Recover a notice only from the actual continuation reward receipt."""
from django.utils import timezone
from stores.db import transaction


def deliver(reward_id):
    from club_accounts.models import ClubAccount
    from site_messages.models import Message
    from site_messages.notifier import notify_messages_after_commit
    from .models import OrderContinuationReward

    hint = OrderContinuationReward.objects.filter(pk=reward_id).values('provider_id').first()
    if not hint:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=hint['provider_id']).first()
        reward = (OrderContinuationReward.objects.select_for_update()
                  .select_related('continuation__continuation_order', 'transaction__wallet').filter(pk=reward_id).first())
        if not reward or reward.notice_completed_at:
            return
        if not reward.has_verified_payment():
            reward.notice_error = '续单奖励原流水或收件来源待核对'
            reward.save(update_fields=['notice_error'])
            return
        if (not account or account.account_type != 'PROVIDER'
                or not account.license_membership_active or account.business_cycle != reward.provider_business_cycle):
            reward.notice_error = '陪玩已离店或业务周期变化，旧通知不再发送'
            reward.notice_completed_at = timezone.now()
            reward.save(update_fields=['notice_error', 'notice_completed_at'])
            return
        from stores.context import current_currency_name
        message = Message.objects.create(recipient_account=account,
            recipient_business_cycle=reward.provider_business_cycle, type='ORDER',
            title='续单奖励已到账', preview=f'续单奖励 50 {current_currency_name()}已到账，可在钱包流水中查看。',
            related_order_id=reward.continuation.continuation_order_id)
        reward.notice_message = message
        reward.notice_completed_at, reward.notice_error = timezone.now(), ''
        reward.save(update_fields=['notice_message', 'notice_completed_at', 'notice_error'])
        notify_messages_after_commit(account.pk, business_cycle=reward.provider_business_cycle)
