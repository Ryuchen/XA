"""Deliver a low-rating reminder from the original completed-order evaluation."""
from django.utils import timezone
from stores.db import transaction


def record(evaluation, order):
    from .models import LowRatingNotice
    from .order_cycle import recipient_cycle
    intent = LowRatingNotice.objects.create(evaluation=evaluation, account_id=evaluation.provider_account_id,
        business_cycle=recipient_cycle(order, evaluation.provider_account_id), title='收到新的服务评价',
        preview=f'「{order.service_name_snapshot}」收到评价，可在订单中查看并回复'[:255])
    transaction.on_commit(lambda: deliver(intent.pk), robust=True)


def deliver(intent_id):
    from club_accounts.models import ClubAccount
    from orders.models import Evaluation, Order
    from .models import LowRatingNotice, Message
    from .notifier import notify_messages_after_commit
    from .order_cycle import recipient_cycle

    initial = LowRatingNotice.objects.filter(pk=intent_id).values('account_id', 'evaluation_id').first()
    if not initial:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        order_id = Evaluation.objects.filter(pk=initial['evaluation_id']).values_list('order_id', flat=True).first()
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        evaluation = Evaluation.objects.select_for_update().filter(pk=initial['evaluation_id'], order=order).first() if order else None
        intent = LowRatingNotice.objects.select_for_update().filter(pk=intent_id,
            evaluation_id=initial['evaluation_id'], account_id=initial['account_id']).first()
        if not intent or intent.completed_at:
            return
        cycle = intent.business_cycle
        valid = bool(order and evaluation and evaluation.voided_at is None
                     and min(evaluation.score, evaluation.skill_score, evaluation.attitude_score,
                             evaluation.communication_score) <= 2
                     and evaluation.provider_account_id == intent.account_id and intent.account_id
                     and type(cycle) is int and cycle > 0 and recipient_cycle(order, intent.account_id) == cycle
                     and account and account.account_type == 'PROVIDER' and account.license_membership_active
                     and account.business_cycle == cycle)
        if not valid:
            intent.skipped_reason = '评价来源不可核验或收件账号周期已变化'
        else:
            intent.message = Message.objects.create(recipient_account=account, recipient_id=evaluation.provider_id,
                recipient_business_cycle=cycle, type='ORDER', title=intent.title, preview=intent.preview,
                related_order_id=order.pk)
            notify_messages_after_commit(account.pk, business_cycle=cycle)
        intent.completed_at = timezone.now()
        intent.save(update_fields=['message', 'skipped_reason', 'completed_at'])
