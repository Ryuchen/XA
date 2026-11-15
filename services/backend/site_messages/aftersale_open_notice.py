"""Recover messages from a specific committed after-sale opening event."""
import hashlib
import json
import logging

from django.conf import settings
from django.utils import timezone
from stores.db import transaction

log = logging.getLogger(__name__)


def enabled():
    return (getattr(settings, 'AFTERSALE_OPEN_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))


def _fingerprint(entry):
    data = json.dumps(entry, sort_keys=True, ensure_ascii=True, separators=(',', ':'))
    return hashlib.sha256(data.encode('ascii')).hexdigest()


def record(case, *, account_id, legacy_user_id, cycle, account_type, title, preview):
    from .models import AfterSaleOpenNotice
    history = case.history
    if (not isinstance(history, list) or not history or not isinstance(history[-1], dict)
            or history[-1].get('status') != 'OPEN' or not case.created_at):
        raise ValueError('售后登记事件来源不可核验')
    entry = history[-1]
    low_rating = entry.get('event') == 'LOW_RATING'
    if ((low_rating and (type(entry.get('evaluation_id')) is not int or entry['evaluation_id'] <= 0))
            or (not low_rating and entry.get('created_at') != case.created_at.isoformat())):
        raise ValueError('售后登记事件来源不可核验')
    expected = {'history_fingerprint': _fingerprint(history[-1]), 'case_opened_at': case.created_at,
                'business_cycle': cycle, 'account_type': account_type,
                'legacy_user_id': legacy_user_id, 'title': title[:100], 'preview': preview[:255]}
    intent, created = AfterSaleOpenNotice.objects.get_or_create(case=case, history_index=len(history) - 1,
        account_id=account_id, defaults=expected)
    if not created and any(getattr(intent, name) != value for name, value in expected.items()):
        raise ValueError('售后登记通知事实不一致')
    transaction.on_commit(lambda: deliver(intent.pk), robust=True)


def deliver(intent_id):
    from club_accounts.models import ClubAccount
    from orders.models import AfterSale, Order
    from .models import AfterSaleOpenNotice, Message
    from .notifier import notify_messages_after_commit
    from .order_cycle import recipient_cycle

    initial = AfterSaleOpenNotice.objects.filter(pk=intent_id).values('account_id', 'case_id').first()
    if not initial:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        order_id = AfterSale.objects.filter(pk=initial['case_id']).values_list('order_id', flat=True).first()
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        case = AfterSale.objects.select_for_update().filter(pk=initial['case_id'], order=order).first() if order else None
        intent = AfterSaleOpenNotice.objects.select_for_update().filter(pk=intent_id,
            account_id=initial['account_id'], case_id=initial['case_id']).first()
        if not intent or intent.completed_at:
            return
        history = case.history if case else None
        source = (history[intent.history_index] if isinstance(history, list)
                  and intent.history_index < len(history) else None)
        cycle = intent.business_cycle
        correct_role = (order and ((intent.account_type == 'BOSS' and order.customer_account_id == intent.account_id)
                        or (intent.account_type == 'PROVIDER' and order.customer_account_id != intent.account_id)))
        event_valid = False
        if case and order and isinstance(source, dict):
            if source.get('event') == 'LOW_RATING':
                from orders.models import Evaluation
                evaluation_id = source.get('evaluation_id')
                evaluation = (Evaluation.objects.filter(pk=evaluation_id, order=order, low_rating_aftersale=case,
                    low_rating_aftersale_state__in=('OPENED', 'ATTACHED')).first()
                    if type(evaluation_id) is int and evaluation_id > 0 else None)
                event_valid = bool(evaluation and evaluation.provider_account_id == source.get('provider_account_id')
                    and min(evaluation.score, evaluation.skill_score, evaluation.attitude_score,
                            evaluation.communication_score) <= 2)
            else:
                event_valid = source.get('created_at') == case.created_at.isoformat()
        valid = bool(case and order and isinstance(source, dict) and _fingerprint(source) == intent.history_fingerprint
                     and source.get('status') == 'OPEN' and case.status == 'OPEN'
                     and case.created_at == intent.case_opened_at and event_valid
                     and correct_role and type(cycle) is int and cycle > 0
                     and recipient_cycle(order, intent.account_id) == cycle and account
                     and account.account_type == intent.account_type and account.license_membership_active
                     and account.business_cycle == cycle)
        if not valid:
            intent.skipped_reason = '售后登记已结束或收件账号周期已变化'
        else:
            intent.message = Message.objects.create(recipient_account=account, recipient_id=intent.legacy_user_id,
                recipient_business_cycle=cycle, type='ORDER', title=intent.title, preview=intent.preview,
                related_order_id=order.pk)
            notify_messages_after_commit(account.pk, business_cycle=cycle)
        intent.completed_at = timezone.now()
        intent.save(update_fields=['message', 'skipped_reason', 'completed_at'])
