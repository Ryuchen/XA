"""Locked rule balances; callers hold the account and cursor transaction locks."""
from rest_framework.exceptions import ValidationError
from .models import KookOnlineRuleBalance


def initialize(cursor):
    if cursor.remainder_ms or cursor.pending_points:
        raise ValidationError('新在线积分游标包含无法核验的余额')
    KookOnlineRuleBalance.objects.create(cursor=cursor, rule_version_id=cursor.rule_version_id)


def locked(cursor):
    row = KookOnlineRuleBalance.objects.select_for_update().filter(
        cursor=cursor, rule_version_id=cursor.rule_version_id).first()
    if row is None:
        raise ValidationError('在线积分规则余额缺失，请人工核对')
    return row


def verify(cursor):
    row = locked(cursor)
    if row.remainder_ms != cursor.remainder_ms or row.pending_points != cursor.pending_points:
        raise ValidationError('在线积分规则余额与游标不一致')
    return row


def save(cursor):
    row = locked(cursor)
    row.remainder_ms, row.pending_points = cursor.remainder_ms, cursor.pending_points
    row.save(update_fields=['remainder_ms', 'pending_points'])


def pending_after(cursor, elapsed_ms, rule):
    row = verify(cursor)
    if elapsed_ms < 0 or rule.pk != cursor.rule_version_id or not rule.window_ms:
        raise ValidationError('在线积分时长或规则不一致')
    return row.pending_points + ((row.remainder_ms + elapsed_ms) // rule.window_ms) * rule.points


def advance_rule(cursor, new_rule):
    """Carry the fraction once; earned groups stay with their original rule.

    Caller holds the cycle cursor and invokes this only at the next rule's
    effective boundary inside the enclosing event transaction.
    """
    from .models import KookOnlineRuleTransition, KookOnlineRuleVersion
    if cursor.rule_version_id == new_rule.pk:
        return verify(cursor)
    previous = KookOnlineRuleVersion.objects.get(pk=cursor.rule_version_id)
    next_rule = KookOnlineRuleVersion.objects.filter(effective_at__isnull=False).filter(
        effective_at__gt=previous.effective_at) if previous.effective_at else KookOnlineRuleVersion.objects.filter(effective_at__isnull=False)
    next_rule = next_rule.order_by('effective_at', 'revision').first()
    if (not next_rule or next_rule.pk != new_rule.pk or not new_rule.window_ms or not new_rule.points
            or (cursor.last_ended_at and cursor.last_ended_at > new_rule.effective_at)):
        raise ValidationError('在线规则切换时序无法核验')
    old = verify(cursor)
    if KookOnlineRuleTransition.objects.filter(cursor=cursor, to_rule=new_rule).exists():
        raise ValidationError('在线规则结转记录与游标不一致')
    carry = old.remainder_ms
    # Carry raw authorized time. Conversion into new groups happens on the
    # next ALLOW interval, not at a potentially DENY/UNKNOWN boundary.
    new = KookOnlineRuleBalance.objects.create(cursor=cursor, rule_version=new_rule,
        remainder_ms=carry, pending_points=0)
    KookOnlineRuleTransition.objects.create(cursor=cursor, from_rule=previous, to_rule=new_rule,
        effective_at=new_rule.effective_at, carried_ms=carry, old_pending_points=old.pending_points,
        new_remainder_ms=new.remainder_ms, new_pending_points=new.pending_points)
    old.remainder_ms = 0
    old.save(update_fields=['remainder_ms'])
    cursor.rule_version = new_rule
    cursor.remainder_ms, cursor.pending_points = new.remainder_ms, new.pending_points
    cursor.save(update_fields=['rule_version', 'remainder_ms', 'pending_points', 'updated_at'])
    return new


def accrue_interval(cursor, rule, elapsed_ms):
    """Apply one ordered authorized interval to its locked rule balance."""
    row = verify(cursor)
    if rule.pk != cursor.rule_version_id or type(elapsed_ms) is not int or elapsed_ms <= 0:
        raise ValidationError('在线区间与当前规则不一致')
    total_ms = row.remainder_ms + elapsed_ms
    row.remainder_ms = total_ms % rule.window_ms
    row.pending_points += (total_ms // rule.window_ms) * rule.points
    row.save(update_fields=['remainder_ms', 'pending_points'])
    cursor.remainder_ms, cursor.pending_points = row.remainder_ms, row.pending_points
    cursor.save(update_fields=['remainder_ms', 'pending_points', 'updated_at'])
    return row.pending_points


def consume_current_pending(cursor, amount, ledger=None):
    """Mark only the current rule's actually issued points as consumed."""
    row = verify(cursor)
    if type(amount) is not int or amount < 0 or amount > row.pending_points:
        raise ValidationError('在线积分流水与待发余额不一致')
    if ledger is not None and amount:
        if ledger.account_id != cursor.account_id or ledger.amount != amount * 100:
            raise ValidationError('在线积分流水与规则分配不一致')
        from .models import KookOnlinePointAllocation
        KookOnlinePointAllocation.objects.create(cursor=cursor, ledger=ledger,
            rule_version_id=row.rule_version_id, points=amount)
    row.pending_points -= amount
    row.save(update_fields=['pending_points'])
    cursor.pending_points = row.pending_points
    cursor.save(update_fields=['pending_points', 'updated_at'])


def locked_pending(cursor):
    """Lock pending groups by original rule; caller already holds cursor."""
    rows = list(KookOnlineRuleBalance.objects.select_for_update().filter(cursor=cursor)
                .select_related('rule_version').order_by('rule_version__revision'))
    if not any(row.rule_version_id == cursor.rule_version_id for row in rows):
        raise ValidationError('当前在线规则余额缺失')
    return rows


def consume_all_pending(cursor, expected, ledger):
    """Consume the exact groups represented by a successfully written ledger."""
    rows = locked_pending(cursor)
    total = sum(row.pending_points for row in rows)
    if (type(expected) is not int or expected <= 0 or total != expected
            or ledger.account_id != cursor.account_id or ledger.amount != expected * 100):
        raise ValidationError('在线积分流水与各规则待发余额不一致')
    from .models import KookOnlinePointAllocation
    for row in rows:
        if row.pending_points:
            KookOnlinePointAllocation.objects.create(cursor=cursor, ledger=ledger,
                rule_version_id=row.rule_version_id, points=row.pending_points)
            row.pending_points = 0
            row.save(update_fields=['pending_points'])
    cursor.pending_points = 0
    cursor.save(update_fields=['pending_points', 'updated_at'])
    return total
