"""Recover messages from a specific committed after-sale closure event."""
import hashlib
import json
import logging

from django.conf import settings
from django.utils import timezone
from stores.db import transaction

log = logging.getLogger(__name__)


def enabled():
    return (getattr(settings, 'AFTERSALE_CLOSURE_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))


def _fingerprint(entry):
    data = json.dumps(entry, sort_keys=True, ensure_ascii=True, separators=(',', ':'))
    return hashlib.sha256(data.encode('ascii')).hexdigest()


def record(case, *, account_id, legacy_user_id, cycle, account_type, title, preview):
    from .models import AfterSaleClosureNotice
    history = case.history
    if (not isinstance(history, list) or not history or not isinstance(history[-1], dict)
            or history[-1].get('status') not in ('RESOLVED', 'REFUNDED', 'PARTIALLY_REFUNDED')
            or not case.resolved_at or history[-1].get('resolved_at') != case.resolved_at.isoformat()):
        raise ValueError('售后结案事件来源不可核验')
    expected = {'history_fingerprint': _fingerprint(history[-1]), 'business_cycle': cycle, 'account_type': account_type,
                'legacy_user_id': legacy_user_id, 'title': title[:100], 'preview': preview[:255]}
    intent, created = AfterSaleClosureNotice.objects.get_or_create(case=case, history_index=len(history) - 1,
        account_id=account_id, defaults=expected)
    if not created and any(getattr(intent, name) != value for name, value in expected.items()):
        raise ValueError('售后结案通知事实不一致')
    transaction.on_commit(lambda: deliver(intent.pk), robust=True)


def deliver(intent_id):
    from club_accounts.models import ClubAccount
    from orders.models import AfterSale, Order
    from .models import AfterSaleClosureNotice, Message
    from .notifier import notify_messages_after_commit
    from .order_cycle import recipient_cycle

    initial = AfterSaleClosureNotice.objects.filter(pk=intent_id).values('account_id', 'case_id').first()
    if not initial:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        order_id = AfterSale.objects.filter(pk=initial['case_id']).values_list('order_id', flat=True).first()
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        case = AfterSale.objects.select_for_update().filter(pk=initial['case_id'], order=order).first() if order else None
        intent = AfterSaleClosureNotice.objects.select_for_update().filter(pk=intent_id,
            account_id=initial['account_id'], case_id=initial['case_id']).first()
        if not intent or intent.completed_at:
            return
        history = case.history if case else None
        source = (history[intent.history_index] if isinstance(history, list)
                  and intent.history_index < len(history) else None)
        cycle = intent.business_cycle
        correct_role = (order and ((intent.account_type == 'BOSS' and order.customer_account_id == intent.account_id)
                        or (intent.account_type == 'PROVIDER' and order.customer_account_id != intent.account_id)))
        valid = bool(case and order and isinstance(source, dict) and _fingerprint(source) == intent.history_fingerprint
                     and intent.history_index == len(history) - 1 and case.status == source.get('status')
                     and case.resolved_at and case.resolved_at.isoformat() == source.get('resolved_at')
                     and source.get('status') in ('RESOLVED', 'REFUNDED', 'PARTIALLY_REFUNDED')
                     and correct_role and type(cycle) is int and cycle > 0
                     and recipient_cycle(order, intent.account_id) == cycle and account
                     and account.account_type == intent.account_type and account.license_membership_active
                     and account.business_cycle == cycle)
        if not valid:
            intent.skipped_reason = '结案来源不可核验或收件账号周期已变化'
        else:
            intent.message = Message.objects.create(recipient_account=account, recipient_id=intent.legacy_user_id,
                recipient_business_cycle=cycle, type='ORDER', title=intent.title, preview=intent.preview,
                related_order_id=order.pk)
            notify_messages_after_commit(account.pk, business_cycle=cycle)
        intent.completed_at = timezone.now()
        intent.save(update_fields=['message', 'skipped_reason', 'completed_at'])
