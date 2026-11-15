"""Transaction isolation needed for a safe non-locking priority read."""
from datetime import timedelta

from django.db import DatabaseError
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def case_snapshot(case):
    import hashlib
    import json
    body = json.dumps(case.history, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return {'id': case.pk, 'status': case.status, 'created_at': case.created_at.isoformat(),
        'resolved_at': case.resolved_at.isoformat() if case.resolved_at else None,
        'operator_id': case.operator_id, 'scope': case.affected_provider_account_ids,
        'history_sha256': hashlib.sha256(body.encode('utf-8')).hexdigest()}


def episode_snapshot(case, entry):
    """Keep the observed closed episode stable when the case is reopened."""
    import hashlib
    import json
    body = json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return {'id': case.pk, 'episode': entry.get('created_at'),
            'entry_sha256': hashlib.sha256(body.encode('utf-8')).hexdigest()}


def _episode_applies(entry, account_id):
    ids = entry.get('affected_provider_account_ids')
    if (not isinstance(ids, list) or not ids
            or any(type(item) is not int or item <= 0 for item in ids)):
        return True
    return not account_id or account_id in ids


def require_current_priority_read():
    connection = transaction.get_connection()
    if not connection.in_atomic_block:
        raise ValidationError('收益释放顺序须在门店事务内核对')
    if connection.vendor == 'sqlite':
        return  # Isolated functional tests; this does not prove row-lock concurrency.
    if connection.vendor != 'mysql':
        raise ValidationError('当前数据库暂无法核对收益释放顺序')
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT ISOLATION_LEVEL FROM performance_schema.events_transactions_current "
                           "WHERE THREAD_ID = (SELECT THREAD_ID FROM performance_schema.threads "
                           "WHERE PROCESSLIST_ID = CONNECTION_ID()) AND STATE = 'ACTIVE'")
            row = cursor.fetchone()
    except DatabaseError as exc:
        raise ValidationError('收益释放顺序暂无法核对，请稍后重试') from exc
    if not row or str(row[0]).upper().replace('_', '-').replace(' ', '-') != 'READ-COMMITTED':
        raise ValidationError('收益释放顺序暂无法核对，请稍后重试')


def _uncontested_gift_hold(hold, *, now, allow_resolved=False):
    """Recognize only a real, unrefunded gift with no after-sale episode.

    Gift creation holds the store money mutex before taking its payment time,
    and holds it through commit. Thus two such committed gifts can safely be
    ordered by their snapshotted deadlines, including a deferred zero clock.
    """
    from orders.models import Order
    from .income import PRIORITY_GIFT_FREEZE_REMARK
    from .models import Transaction
    from .settlement_clocks import MAX_HOURS
    source, wallet = hold.income_transaction, hold.wallet
    order = source.order
    if not order:
        return False
    if any(value is None or timezone.is_naive(value) for value in
            (now, order.completed_at, order.created_at, order.settled_at,
             hold.release_at, hold.created_at, source.created_at)):
        return False
    clocks = (order.gift_aftersale_hours, order.gift_freeze_hours)
    if (not order.is_gift_snapshot or order.status != Order.Status.COMPLETED
            or order.payment_status != Order.PaymentStatus.PAID or not order.customer_account_id
            or order.provider_id != wallet.user_id or order.provider_account_id != wallet.account_id
            or not order.completed_at or not order.created_at or order.settled_at != order.completed_at
            or order.completed_at > order.created_at or order.created_at > now
            or any(type(value) is not int or not 0 <= value <= MAX_HOURS for value in clocks)
            or hold.release_at != order.completed_at + timedelta(hours=max(clocks))
            or hold.deposit_amount or hold.recovered_amount or hold.amount != order.provider_income
            or hold.created_at > now or source.wallet_id != wallet.pk or source.order_id != order.pk
            or source.tx_type != Transaction.TxType.GIFT or source.status != Transaction.Status.SUCCESS
            or source.amount != hold.amount or source.amount <= 0 or source.created_at < order.created_at
            or source.created_at > hold.created_at or source.reversal_of_id or source.reversals.exists()
            or source.balance_after != source.balance_before + source.amount
            or (not allow_resolved and order.aftersales.exists()) or order.refund_records.exists()):
        return False
    payments = list(Transaction.objects.filter(order=order, tx_type=Transaction.TxType.GIFT,
        amount__lt=0, status=Transaction.Status.SUCCESS).select_related('wallet')[:2])
    freezes = list(Transaction.objects.filter(order=order, wallet=wallet,
        tx_type=Transaction.TxType.EARNINGS_FREEZE, status=Transaction.Status.SUCCESS)[:2])
    if len(payments) != 1 or len(freezes) != 1 or hold.freeze_transaction_id != freezes[0].pk:
        return False
    payment, freeze = payments[0], freezes[0]
    if any(value is None or timezone.is_naive(value) for value in (payment.created_at, freeze.created_at)):
        return False
    return bool(payment.wallet.account_id == order.customer_account_id and payment.wallet.user_id == order.customer_id
        and payment.amount == -order.amount and payment.balance_after == payment.balance_before + payment.amount
        and not payment.reversal_of_id and not payment.reversals.exists()
        and order.created_at <= payment.created_at <= source.created_at <= hold.created_at <= freeze.created_at <= now
        and freeze.amount == -hold.amount and freeze.balance_after == freeze.balance_before + freeze.amount
        and not freeze.reversal_of_id and not freeze.reversals.exists()
        and (max(clocks) != 0 or freeze.remark == PRIORITY_GIFT_FREEZE_REMARK))


def earliest_safe_hold(now, wallet_id, *, recover=False):
    """Return a provable earliest due hold, or a reason to leave this wallet pending."""
    from .income import _due_income_candidates
    from .models import IncomeHold
    # Examine all eligible holds: an earlier raw deadline may have remained
    # in after-sale while a later deadline became eligible first.
    count = IncomeHold.objects.filter(wallet_id=wallet_id, released_at__isnull=True,
        cancelled_at__isnull=True, release_at__lte=now).count()
    candidates = _due_income_candidates(now, count, wallet_id=wallet_id)
    if len(candidates) < 2:
        return (candidates[0] if candidates else None), ''
    holds = {row.pk: row for row in IncomeHold.objects.filter(pk__in=[item[0] for item in candidates])
        .select_related('wallet', 'income_transaction__order')}
    if len(holds) != len(candidates):
        return None, '多笔可释放收益的先后依据待核对'
    if recover:
        from .income_priority_evidence import recover_pending
        recover_pending(list(holds), [hold.income_transaction.order_id for hold in holds.values()
                                      if hold.income_transaction.order_id])
    # Preserve the existing proof for zero-clock gifts: their payment clocks
    # are serialized by the same store money mutex through commit.
    if all(_uncontested_gift_hold(holds[item[0]], now=now) for item in candidates):
        return min(candidates, key=lambda item: (holds[item[0]].release_at, item[0])), ''
    from orders.grab_clock_contract import database_id
    from django.utils.dateparse import parse_datetime
    from .income_priority_evidence import valid_priority_bounds
    from .income_priority_evidence import initial_deposit_verified
    from .models import IncomeAfterSaleClosureEvidence, Transaction
    try:
        identity = database_id() if transaction.get_connection().vendor == 'mysql' else None
    except (DatabaseError, ValueError) as exc:
        raise ValidationError('收益资格时钟暂无法核对') from exc
    candidates_with_bounds = []
    for item in candidates:
        hold = holds[item[0]]
        source, order = hold.income_transaction, hold.income_transaction.order
        if (not order or not hold.wallet.account_id or source.wallet_id != wallet_id
                or source.status != Transaction.Status.SUCCESS or source.amount <= 0
                or source.tx_type not in (Transaction.TxType.GIFT, Transaction.TxType.INCOME)
                or source.reversal_of_id or source.reversals.exists()
                or not initial_deposit_verified(hold) or hold.recovered_amount > hold.amount
                or source.order_id != order.pk
                or timezone.is_naive(hold.release_at) or timezone.is_naive(hold.created_at)):
            return None, '多笔可释放收益的先后依据待核对'
        ready_at = hold.release_at
        if not order.is_gift_snapshot and order.service_clock_version is None:
            from engagement.confirmation import aftersale_deadline
            deadline = aftersale_deadline(order)
            if deadline is None or timezone.is_naive(deadline):
                return None, '服务售后截止时间待核对'
            ready_at = max(ready_at, deadline)
        lower = upper = ready_at
        serial = None
        opening = IncomeAfterSaleClosureEvidence.objects.filter(hold=hold).first()
        if (not opening or not opening.upper_at or opening.upper_at > now or not identity
                or not valid_priority_bounds(opening, identity=identity)):
            return None, '收益入账提交时间待核对'
        evidence_events = [opening]
        for case in order.aftersales.all():
            history = case.history
            if (not isinstance(history, list) or not history or not isinstance(history[-1], dict)
                    or history[-1].get('status') != case.status
                    or history[-1].get('created_at') != case.created_at.isoformat()
                    or history[-1].get('resolved_at') != (case.resolved_at.isoformat() if case.resolved_at else None)):
                return None, '多笔可释放收益的售后历史待核对'
            for entry in history:
                if not isinstance(entry, dict):
                    return None, '多笔可释放收益的售后历史待核对'
                if entry.get('status') not in ('RESOLVED', 'REFUNDED', 'PARTIALLY_REFUNDED'):
                    continue
                if not _episode_applies(entry, hold.wallet.account_id):
                    continue
                try:
                    opened = parse_datetime(entry.get('created_at', ''))
                    resolved = parse_datetime(entry.get('resolved_at', ''))
                except (TypeError, ValueError):
                    return None, '售后结案时间待核对'
                if (not opened or not resolved or timezone.is_naive(opened) or timezone.is_naive(resolved)
                        or resolved < opened or resolved > now or not entry.get('operator_id')):
                    return None, '售后结案时间待核对'
                evidence = IncomeAfterSaleClosureEvidence.objects.filter(
                    pk=entry.get('income_priority_closure_id'), case=case, case_opened_at=opened).first()
                if (not evidence or not evidence.upper_at or evidence.upper_at > now
                        or not valid_priority_bounds(evidence, identity=identity)):
                    observed = opening.observed_cases
                    current_snapshot = (entry is history[-1] and case.status != 'OPEN'
                                        and case_snapshot(case) in observed) if isinstance(observed, list) else False
                    if (not isinstance(observed, list)
                            or (episode_snapshot(case, entry) not in observed and not current_snapshot)):
                        return None, '售后结案提交时间待核对'
                    evidence = opening
                evidence_events.append(evidence)
        lower = max([ready_at, *(event.lower_at for event in evidence_events)])
        upper = max([ready_at, *(event.upper_at for event in evidence_events)])
        # Evidence writers hold the store money mutex, so event IDs provide
        # their commit order when the configured deadline has already passed.
        serial = max(event.pk for event in evidence_events) if ready_at <= max(
            event.lower_at for event in evidence_events) else None
        candidates_with_bounds.append((lower, upper, serial, item))
    possible = []
    for current in candidates_with_bounds:
        if not any(other is not current and (
                other[1] < current[0] or (other[2] is not None and current[2] is not None
                                           and other[2] < current[2])
                or (other[0] == other[1] == current[0] == current[1] and other[3][0] < current[3][0]))
                   for other in candidates_with_bounds):
            possible.append(current)
    if len(possible) != 1:
        return None, '收益到期与售后结案提交时间交叉，待核对'
    return possible[0][3], ''
