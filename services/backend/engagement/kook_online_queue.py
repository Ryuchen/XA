"""Per-binding, per-cycle ordered KOOK accrual recovery."""
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from .models import KookManualOnlineReview, KookOnlineCursor, KookOnlineInterval, KookOnlineProcessing
from .kook_online_rules import earned, initial_rule, remainder


def lock_cursor(binding):
    if not binding.business_cycle or binding.business_cycle != binding.account.business_cycle:
        raise ValidationError('KOOK 绑定业务周期待核对')
    _, created = KookOnlineCursor.objects.get_or_create(account_id=binding.account_id,
        business_cycle=binding.business_cycle, defaults={'binding': binding})
    cursor = KookOnlineCursor.objects.select_for_update().get(account_id=binding.account_id,
        business_cycle=binding.business_cycle)
    if cursor.binding_id not in (None, binding.pk):
        raise ValidationError('KOOK 在线累计绑定来源不一致')
    if cursor.binding_id is None:
        # Preserve actual manually recorded time and remaining whole groups
        # when this account later binds, without inventing a KOOK identity.
        if cursor.rule_version_id == 0:
            rule = initial_rule()
            consumed = ((cursor.unbound_awarded_points + cursor.pending_points) * rule.window_ms
                        + cursor.remainder_ms)
            if consumed > cursor.unbound_online_ms:
                raise ValidationError('人工在线累计与原记录不一致，请先核对')
            binding.online_milliseconds += cursor.unbound_online_ms
            binding.awarded_points += cursor.unbound_awarded_points
            binding.cycle_online_base = binding.online_milliseconds - consumed
            binding.cycle_awarded_base = binding.awarded_points - cursor.unbound_awarded_points
        else:
            from .kook_online_balances import verify
            verify(cursor)
            # Preserve the previous binding's history outside this cycle;
            # per-rule balances remain authoritative for earnings.
            binding.cycle_online_base = binding.online_milliseconds
            binding.cycle_awarded_base = binding.awarded_points
            binding.online_milliseconds += cursor.unbound_online_ms
            binding.awarded_points += cursor.unbound_awarded_points
        binding.save(update_fields=['online_milliseconds', 'awarded_points', 'cycle_online_base', 'cycle_awarded_base'])
        cursor.binding = binding
        cursor.unbound_online_ms = cursor.unbound_awarded_points = 0
        cursor.save(update_fields=['binding', 'unbound_online_ms', 'unbound_awarded_points', 'updated_at'])
    if created:
        from .kook_online_balances import initialize
        initialize(cursor)
        sync_cursor(cursor, binding)
    return cursor


def sync_cursor(cursor, binding):
    rule = initial_rule()
    total = earned(binding.online_milliseconds, binding.cycle_online_base, binding.cycle_awarded_base, rule)
    cursor.rule_version = rule
    cursor.remainder_ms = remainder(binding.online_milliseconds, binding.cycle_online_base, rule)
    cursor.pending_points = max(0, total - binding.awarded_points)
    cursor.save(update_fields=['rule_version', 'remainder_ms', 'pending_points', 'updated_at'])
    from .kook_online_balances import save
    save(cursor)


def verify_cursor(cursor, binding):
    if cursor.rule_version_id != 0:
        if (cursor.binding_id != binding.pk or cursor.account_id != binding.account_id
                or cursor.business_cycle != binding.business_cycle):
            raise ValidationError('KOOK 在线积分账号周期不一致')
        from .kook_online_balances import verify
        verify(cursor)
        return
    rule = initial_rule()
    total = earned(binding.online_milliseconds, binding.cycle_online_base, binding.cycle_awarded_base, rule)
    if (cursor.rule_version_id != 0
            or cursor.remainder_ms != remainder(binding.online_milliseconds, binding.cycle_online_base, rule)
            or cursor.pending_points != max(0, total - binding.awarded_points)):
        raise ValidationError('KOOK 在线积分余量或待发凭证不一致')
    from .kook_online_balances import verify
    verify(cursor)


def align_rule(cursor, rule):
    from .kook_online_balances import advance_rule
    from .models import KookOnlineRuleVersion
    while cursor.rule_version_id != rule.pk:
        current_rule = KookOnlineRuleVersion.objects.get(pk=cursor.rule_version_id)
        candidates = KookOnlineRuleVersion.objects.filter(effective_at__isnull=False)
        if current_rule.effective_at:
            candidates = candidates.filter(effective_at__gt=current_rule.effective_at)
        next_rule = candidates.order_by('effective_at', 'revision').first()
        if not next_rule or not rule.effective_at or next_rule.effective_at > rule.effective_at:
            raise ValidationError('KOOK 待处理区间规则顺序不一致')
        advance_rule(cursor, next_rule)
    return advance_rule(cursor, rule)


def replay_waiting(binding, cursor):
    """Replay only original definite decisions; never infer an unknown grant."""
    from .services import change_points, locked_profile
    rows = KookOnlineInterval.objects.select_for_update().filter(binding=binding,
        binding_cycle=cursor.business_cycle, processing_status='WAITING').order_by('started_at', 'ended_at', 'pk')
    for row in rows:
        if KookManualOnlineReview.objects.filter(cursor=cursor, status='PENDING_REVIEW',
                                                 started_at__lt=row.started_at).exists():
            cursor.blocked = True
            cursor.save(update_fields=['blocked', 'last_ended_at', 'updated_at'])
            return
        if row.award_outcome == 'UNKNOWN' or row.interval_outcome == 'UNKNOWN':
            cursor.blocked = True
            cursor.save(update_fields=['blocked', 'last_ended_at', 'updated_at'])
            return
        if (cursor.last_ended_at and row.started_at < cursor.last_ended_at):
            raise ValidationError('KOOK 在线区间顺序冲突，请人工核对')
        from .kook_online_rules import interval_segments
        segments = interval_segments(row.started_at, row.ended_at)
        rule = row.rule_version
        if (row.account_id != binding.account_id or row.account_cycle != cursor.business_cycle
                or row.binding_cycle != cursor.business_cycle or not rule
                or row.rule_revision != rule.revision or row.rule_window_ms != rule.window_ms
                or row.rule_points != rule.points or len(segments) != 1 or segments[0][2].pk != rule.pk
                or row.elapsed_ms <= 0 or row.elapsed_ms > 90000
                or int((row.ended_at - row.started_at).total_seconds() * 1000) != row.elapsed_ms):
            raise ValidationError('KOOK 待处理区间来源或规则不一致')
        align_rule(cursor, rule)
        before_online, before_awarded = binding.online_milliseconds, binding.awarded_points
        ledger = None
        if row.interval_outcome == 'ALLOW' and row.award_outcome == 'ALLOW':
            if type(row.points_enabled_used) is not bool:
                raise ValidationError('KOOK 待处理区间缺少原积分开关事实')
            from .kook_online_balances import accrue_interval, consume_all_pending, locked_pending
            pending = accrue_interval(cursor, row.rule_version, row.elapsed_ms)
            binding.online_milliseconds += row.elapsed_ms
            all_pending = sum(balance.pending_points for balance in locked_pending(cursor))
            if rule.revision == 0:
                total = earned(binding.online_milliseconds, binding.cycle_online_base,
                               binding.cycle_awarded_base, initial_rule())
                if total - binding.awarded_points != all_pending:
                    raise ValidationError('KOOK 待处理区间与规则余额不一致')
                event_key = f'KOOK_ONLINE_TOTAL:{binding.pk}:{total}'
            else:
                event_key = f'KOOK_ONLINE_INTERVAL:{row.pk}'
            if all_pending and row.points_enabled_used:
                ledger = change_points(locked_profile(binding.account), all_pending * 100,
                    event_key, 'KOOK_ONLINE', 'KOOK 语音原时段积分结转')
                binding.awarded_points += all_pending
                consume_all_pending(cursor, all_pending, ledger)
            binding.save(update_fields=['online_milliseconds', 'awarded_points'])
        elif row.interval_outcome != 'DENY' and row.award_outcome != 'DENY':
            raise ValidationError('KOOK 待处理区间授权决定无效')
        KookOnlineProcessing.objects.create(interval=row, online_before=before_online,
            online_after=binding.online_milliseconds, awarded_before=before_awarded,
            awarded_after=binding.awarded_points, points_ledger=ledger)
        row.processing_status, row.processed_at = 'DONE', timezone.now()
        row.save(update_fields=['processing_status', 'processed_at'])
        cursor.last_ended_at = row.ended_at
        if cursor.rule_version_id == 0:
            sync_cursor(cursor, binding)
    cursor.blocked = KookManualOnlineReview.objects.filter(cursor=cursor, status='PENDING_REVIEW').exists()
    cursor.save(update_fields=['blocked', 'last_ended_at', 'updated_at'])
