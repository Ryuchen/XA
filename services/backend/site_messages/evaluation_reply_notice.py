"""Persistent, cycle-bound notifications for an actual evaluation reply."""
from django.conf import settings
from django.utils import timezone
from stores.db import transaction
import logging

logger = logging.getLogger(__name__)


def enabled():
    return bool(getattr(settings, 'EVALUATION_REPLY_NOTICE_RECOVERY_ENABLED', False))


def record(evaluation, order, *, staff, boss_preview):
    from .models import EvaluationReplyNotice
    from .order_cycle import recipient_cycle
    targets = [('BOSS', evaluation.customer_account_id, evaluation.customer_id,
                '客服回复了你的评价' if staff else '陪玩回复了你的评价', boss_preview)]
    if staff:
        targets.append(('PROVIDER', evaluation.provider_account_id, evaluation.provider_id,
                        '客服更新了评价回复', '客服更新了服务评价的回复，可在订单中查看'))
    for kind, account_id, user_id, title, preview in targets:
        intent = EvaluationReplyNotice.objects.create(evaluation=evaluation, reply_version=evaluation.reply_version,
            account_id=account_id, account_type=kind, business_cycle=recipient_cycle(order, account_id),
            legacy_user_id=user_id, title=title, preview=preview[:255])
        transaction.on_commit(lambda pk=intent.pk: deliver(pk), robust=True)


def schedule_immediate(evaluation, order, *, staff, boss_preview):
    from .order_cycle import recipient_cycle
    from .utils import create_cycle_message
    targets = [(evaluation.customer_account_id, evaluation.customer_id,
                '客服回复了你的评价' if staff else '陪玩回复了你的评价', boss_preview)]
    if staff:
        targets.append((evaluation.provider_account_id, evaluation.provider_id,
                        '客服更新了评价回复', '客服更新了服务评价的回复，可在订单中查看'))
    for account_id, user_id, title, preview in targets:
        cycle = recipient_cycle(order, account_id)
        def send(account_id=account_id, user_id=user_id, title=title, preview=preview, cycle=cycle):
            message = create_cycle_message(recipient_account_id=account_id, recipient_id=user_id,
                recipient_business_cycle=cycle, msg_type='ORDER', title=title, preview=preview,
                related_order_id=order.pk)
            if message is None:
                logger.warning('评价回复通知未投递，来源或接收周期待核对 evaluation=%s account=%s',
                               evaluation.pk, account_id)
        transaction.on_commit(send, robust=True)


def deliver(intent_id):
    from club_accounts.models import ClubAccount
    from orders.models import Evaluation, Order
    from .models import EvaluationReplyNotice, Message
    from .notifier import notify_messages_after_commit
    from .order_cycle import recipient_cycle
    hint = EvaluationReplyNotice.objects.filter(pk=intent_id).values('evaluation_id', 'account_id',
        'evaluation__order_id').first()
    if not hint:
        return
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=hint['evaluation__order_id']).first()
        account = ClubAccount.objects.select_for_update().filter(pk=hint['account_id']).first()
        evaluation = Evaluation.objects.select_for_update().filter(pk=hint['evaluation_id'], order=order).first() if order else None
        intent = EvaluationReplyNotice.objects.select_for_update().filter(pk=intent_id,
            evaluation_id=hint['evaluation_id'], account_id=hint['account_id']).first()
        if not intent or intent.completed_at:
            return
        expected = (evaluation.customer_account_id if intent.account_type == 'BOSS' else
                    evaluation.provider_account_id if intent.account_type == 'PROVIDER' else None) if evaluation else None
        expected_user = (evaluation.customer_id if intent.account_type == 'BOSS' else
                         evaluation.provider_id if intent.account_type == 'PROVIDER' else None) if evaluation else None
        cycle = intent.business_cycle
        valid = bool(evaluation and evaluation.voided_at is None and evaluation.reply_version >= intent.reply_version > 0
            and expected and expected == intent.account_id and account and account.account_type == intent.account_type
            and expected_user == intent.legacy_user_id
            and evaluation.customer_account_id == order.customer_account_id
            and account.license_membership_active and type(cycle) is int and cycle > 0
            and account.business_cycle == cycle and recipient_cycle(order, account.pk) == cycle)
        if not valid:
            intent.skipped_reason = '回复来源或收件账号周期待核对'
        else:
            intent.message = Message.objects.create(recipient_account=account, recipient_id=intent.legacy_user_id,
                recipient_business_cycle=cycle, type='ORDER', title=intent.title, preview=intent.preview,
                related_order_id=order.pk)
            notify_messages_after_commit(account.pk, business_cycle=cycle)
        intent.completed_at = timezone.now()
        intent.save(update_fields=['message', 'skipped_reason', 'completed_at'])
