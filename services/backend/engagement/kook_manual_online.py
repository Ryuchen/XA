"""Capture manual KOOK intervals that cannot safely follow issued history."""
from zoneinfo import ZoneInfo
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from .models import KookManualOnlineReview, KookOnlineCursor, KookPointsConfig


def check_times(day, units, started_at, ended_at):
    if not started_at or not ended_at or timezone.is_naive(started_at) or timezone.is_naive(ended_at):
        raise ValidationError('请填写实际在线开始及结束时间')
    if (timezone.localtime(started_at, ZoneInfo('Asia/Shanghai')).date() != day
            or ended_at > timezone.now() or ended_at <= started_at
            or (ended_at - started_at).total_seconds() != units):
        raise ValidationError('实际在线时间须与所选日期和秒数一致，且不能晚于当前时间')


def existing(account, source_id, started_at, ended_at, units):
    row = KookManualOnlineReview.objects.filter(account=account, business_cycle=account.business_cycle,
                                                source_id=source_id).first()
    if row and (row.started_at != started_at or row.ended_at != ended_at or row.online_seconds != units):
        raise ValidationError('原来源编号已用于不同的在线区间，请核对')
    return row


def record_segments(review):
    """Persist the actual event-time terms in the source transaction."""
    from .models import KookManualOnlineSegment
    from .kook_online_rules import interval_segments
    parts = interval_segments(review.started_at, review.ended_at)
    for start, end, rule in parts:
        KookManualOnlineSegment.objects.create(review=review, rule_version=rule,
            started_at=start, ended_at=end, elapsed_ms=int((end - start).total_seconds() * 1000),
            rule_window_ms=rule.window_ms, rule_points=rule.points)


def verify_segments(review):
    from .kook_online_rules import interval_segments
    rows = list(review.rule_segments.select_related('rule_version').order_by('started_at', 'pk'))
    parts = interval_segments(review.started_at, review.ended_at)
    if len(rows) != len(parts):
        raise ValidationError('人工在线规则片段缺失，请核对')
    for row, (start, end, rule) in zip(rows, parts):
        if (row.started_at != start or row.ended_at != end or row.rule_version_id != rule.pk
                or row.elapsed_ms != int((end - start).total_seconds() * 1000)
                or row.rule_window_ms != rule.window_ms or row.rule_points != rule.points):
            raise ValidationError('人工在线规则片段与原始来源不一致')


def defer_if_needed(account, operator, source_id, started_at, ended_at, units):
    KookPointsConfig.objects.get_or_create(pk=1, defaults={'guild_id': '', 'checkin_channel_id': ''})
    config = KookPointsConfig.objects.select_for_update().get(pk=1)
    if config.enabled:
        raise ValidationError('KOOK自动计分已开启，请勿人工重复记分')
    from .models import KookBinding
    binding = KookBinding.objects.select_for_update().filter(account=account).first()
    if binding and binding.business_cycle == account.business_cycle:
        binding.account = account
        from .kook_online_queue import lock_cursor
        cursor = lock_cursor(binding)
    else:
        _, created = KookOnlineCursor.objects.get_or_create(account=account, business_cycle=account.business_cycle)
        cursor = KookOnlineCursor.objects.select_for_update().get(account=account, business_cycle=account.business_cycle)
        if created:
            from .kook_online_balances import initialize
            initialize(cursor)
    if cursor.blocked or (cursor.last_ended_at and started_at < cursor.last_ended_at):
        row = KookManualOnlineReview.objects.create(account=account, business_cycle=account.business_cycle,
            cursor=cursor, source_id=source_id, started_at=started_at, ended_at=ended_at,
            online_seconds=units, operator=operator,
            reason='授权区间待核对' if cursor.blocked else '早于已处理在线区间')
        record_segments(row)
        cursor.blocked = True
        cursor.save(update_fields=['blocked', 'updated_at'])
        return row, cursor
    return None, cursor


def record_versioned(account, cursor, binding, profile, *, source_id, started_at, ended_at, units, operator, key):
    """Commit a chronologically recorded source against its original rule slices."""
    from .kook_online_rules import interval_segments
    from .kook_online_queue import align_rule
    from .kook_online_balances import accrue_interval, locked_pending, consume_all_pending
    from .services import change_points
    parts = interval_segments(started_at, ended_at)
    for start, end, rule in parts:
        align_rule(cursor, rule)
        elapsed_ms = int((end - start).total_seconds() * 1000)
        if elapsed_ms:
            accrue_interval(cursor, rule, elapsed_ms)
        cursor.last_ended_at = end
        cursor.save(update_fields=['last_ended_at', 'updated_at'])
    pending = sum(row.pending_points for row in locked_pending(cursor))
    ledger = change_points(profile, pending * 100, key, 'KOOK_ONLINE',
        f'KOOK 在线来源 {source_id[:80]}，按原规则分段', operator=operator)
    if pending:
        consume_all_pending(cursor, pending, ledger)
    source = KookManualOnlineReview.objects.create(account=account, business_cycle=account.business_cycle,
        cursor=cursor, source_id=source_id, started_at=started_at, ended_at=ended_at,
        online_seconds=units, operator=operator, status='COMPLETED', points_ledger=ledger)
    record_segments(source)
    if binding and binding.business_cycle == account.business_cycle:
        binding.online_milliseconds += units * 1000
        binding.awarded_points += pending
        binding.save(update_fields=['online_milliseconds', 'awarded_points'])
    else:
        cursor.unbound_online_ms += units * 1000
        cursor.unbound_awarded_points += pending
        cursor.save(update_fields=['unbound_online_ms', 'unbound_awarded_points', 'updated_at'])
    return ledger


def resolve_versioned_chronological(row, account, binding, cursor, *, request_id, actor_id, reason, points):
    """Settle a source still in chronological order without rewriting old ledgers."""
    from .kook_online_queue import align_rule, replay_waiting
    from .kook_online_balances import accrue_interval, locked_pending, consume_all_pending
    from .services import change_points, locked_profile
    if cursor.last_ended_at and row.started_at < cursor.last_ended_at:
        raise ValidationError('该来源早于已处理流水，跨规则差额仍需人工核对')
    for part in row.rule_segments.select_related('rule_version').order_by('started_at', 'pk'):
        align_rule(cursor, part.rule_version)
        if part.elapsed_ms:
            accrue_interval(cursor, part.rule_version, part.elapsed_ms)
        cursor.last_ended_at = part.ended_at
        cursor.save(update_fields=['last_ended_at', 'updated_at'])
    pending = sum(balance.pending_points for balance in locked_pending(cursor))
    if points != pending:
        raise ValidationError('所填补发积分与原规则顺序计算结果不一致，请继续核对')
    ledger = None
    if points:
        ledger = change_points(locked_profile(account), points * 100,
            f'kook-manual-review:{row.pk}', 'KOOK_ONLINE', '平台核对原规则在线积分补发')
        consume_all_pending(cursor, points, ledger)
    if binding and binding.pk == cursor.binding_id and binding.business_cycle == row.business_cycle:
        binding.online_milliseconds += row.online_seconds * 1000
        binding.awarded_points += points
        binding.save(update_fields=['online_milliseconds', 'awarded_points'])
    elif cursor.binding_id is None:
        cursor.unbound_online_ms += row.online_seconds * 1000
        cursor.unbound_awarded_points += points
        cursor.save(update_fields=['unbound_online_ms', 'unbound_awarded_points', 'updated_at'])
    else:
        raise ValidationError('原绑定与业务周期不一致')
    row.status, row.resolution_request_id, row.resolution_outcome = 'COMPLETED', request_id, 'ALLOW'
    row.resolution_points, row.resolution_reason, row.platform_actor_id = points, reason, actor_id
    row.resolved_at, row.points_ledger = timezone.now(), ledger
    row.save(update_fields=['status', 'resolution_request_id', 'resolution_outcome', 'resolution_points',
        'resolution_reason', 'platform_actor_id', 'resolved_at', 'points_ledger'])
    if binding and cursor.binding_id == binding.pk:
        binding.account = account
        replay_waiting(binding, cursor)
    else:
        cursor.blocked = KookManualOnlineReview.objects.filter(cursor=cursor, status='PENDING_REVIEW').exists()
        cursor.save(update_fields=['blocked', 'last_ended_at', 'updated_at'])
    return row


def resolve_versioned_historical(row, account, binding, cursor, *, request_id, actor_id, reason, points, allocations):
    """Record a platform-verified difference without replaying later history."""
    from .models import KookOnlinePointAllocation
    from .services import change_points, locked_profile
    from .kook_online_queue import replay_waiting
    if not isinstance(allocations, list) or (points and not allocations):
        raise ValidationError('请逐规则填写已核实积分及依据')
    limits = {}
    for part in row.rule_segments.select_related('rule_version').order_by('started_at', 'pk'):
        maximum = ((part.elapsed_ms + part.rule_window_ms - 1) // part.rule_window_ms) * part.rule_points
        limits[part.rule_version_id] = limits.get(part.rule_version_id, 0) + maximum
    normalized = []
    seen = set()
    for item in allocations:
        if (not isinstance(item, dict) or set(item) != {'rule_revision', 'points', 'reason'}
                or type(item['rule_revision']) is not int or type(item['points']) is not int
                or item['points'] <= 0 or item['rule_revision'] in seen
                or not isinstance(item['reason'], str) or not 1 <= len(item['reason'].strip()) <= 500
                or item['rule_revision'] not in limits or item['points'] > limits[item['rule_revision']]):
            raise ValidationError('逐规则积分或来源依据无效，请重新核对')
        seen.add(item['rule_revision'])
        normalized.append((item['rule_revision'], item['points'], item['reason'].strip()))
    if sum(item[1] for item in normalized) != points:
        raise ValidationError('各规则补发之和须等于本次差额')
    ledger = (change_points(locked_profile(account), points * 100, f'kook-manual-review:{row.pk}',
        'KOOK_ONLINE', '平台核对历史在线积分差额', operator=None) if points else None)
    for revision, value, item_reason in normalized:
        KookOnlinePointAllocation.objects.create(cursor=cursor, ledger=ledger, rule_version_id=revision,
                                                  points=value, review_reason=item_reason)
    if binding and binding.pk == cursor.binding_id and binding.business_cycle == row.business_cycle:
        binding.online_milliseconds += row.online_seconds * 1000
        binding.awarded_points += points
        binding.save(update_fields=['online_milliseconds', 'awarded_points'])
    elif cursor.binding_id is None:
        cursor.unbound_online_ms += row.online_seconds * 1000
        cursor.unbound_awarded_points += points
        cursor.save(update_fields=['unbound_online_ms', 'unbound_awarded_points', 'updated_at'])
    else:
        raise ValidationError('原绑定与业务周期不一致')
    row.status, row.resolution_request_id, row.resolution_outcome = 'COMPLETED', request_id, 'ALLOW'
    row.resolution_points, row.resolution_reason, row.platform_actor_id = points, reason, actor_id
    row.resolved_at, row.points_ledger = timezone.now(), ledger
    row.save(update_fields=['status', 'resolution_request_id', 'resolution_outcome', 'resolution_points',
        'resolution_reason', 'platform_actor_id', 'resolved_at', 'points_ledger'])
    if binding and cursor.binding_id == binding.pk:
        binding.account = account
        replay_waiting(binding, cursor)
    else:
        cursor.blocked = KookManualOnlineReview.objects.filter(cursor=cursor, status='PENDING_REVIEW').exists()
        cursor.save(update_fields=['blocked', 'last_ended_at', 'updated_at'])
    return row


def resolve_locked(review_id, *, request_id, actor_id, outcome, reason, points, allocations=None):
    """Resolve one manual source under the caller's tenant transaction."""
    from club_accounts.models import ClubAccount
    from .models import ClubOperationConfig, KookBinding, KookOnlineInterval
    from .kook_online_queue import replay_waiting, sync_cursor, verify_cursor
    from .kook_online_rules import earned, initial_rule
    from .services import change_points, locked_profile
    head = KookManualOnlineReview.objects.filter(pk=review_id).values('account_id').first()
    if not head:
        raise ValidationError('人工在线来源不存在')
    account = ClubAccount.objects.select_for_update().filter(pk=head['account_id']).first()
    config = KookPointsConfig.objects.select_for_update().filter(pk=1).first()
    binding = KookBinding.objects.select_for_update().filter(account_id=head['account_id']).first()
    row_head = KookManualOnlineReview.objects.filter(pk=review_id).values('cursor_id').first()
    cursor = KookOnlineCursor.objects.select_for_update().get(pk=row_head['cursor_id'])
    row = KookManualOnlineReview.objects.select_for_update().get(pk=review_id)
    if row.resolution_request_id:
        if (row.resolution_request_id != request_id or row.resolution_outcome != outcome
                or row.resolution_reason != reason or row.resolution_points != points):
            raise ValidationError('该来源已裁决，请使用原请求查看')
        ledger = row.points_ledger
        if ((points > 0 and (not ledger or ledger.account_id != row.account_id
                or ledger.event_key != f'kook-manual-review:{row.pk}' or ledger.amount != points * 100))
                or (points == 0 and ledger is not None)):
            raise ValidationError('人工核对与积分流水不一致')
        if allocations is not None:
            stored = list(ledger.kook_rule_allocations.order_by('rule_version_id').values(
                'rule_version_id', 'points', 'review_reason')) if ledger else []
            expected = sorted([{'rule_version_id': item.get('rule_revision'), 'points': item.get('points'),
                'review_reason': item.get('reason', '').strip()} for item in allocations], key=lambda item: item['rule_version_id'])
            if stored != expected:
                raise ValidationError('请使用原逐规则核对依据重试')
        return row
    if KookManualOnlineReview.objects.filter(resolution_request_id=request_id).exists():
        raise ValidationError('请求编号已用于其他人工来源')
    if row.status != 'PENDING_REVIEW' or outcome not in ('ALLOW', 'DENY'):
        raise ValidationError('该人工来源不再待处理')
    if row.account_id != cursor.account_id or row.business_cycle != cursor.business_cycle:
        raise ValidationError('人工来源与业务周期不一致')
    same_cycle = bool(account and account.license_membership_active and account.business_cycle == row.business_cycle)
    ledger = None
    if outcome == 'ALLOW':
        verify_segments(row)
        if not same_cycle:
            raise ValidationError('原账号周期已变化，不能向新周期补发')
        if type(points) is not int or points < 0:
            raise ValidationError('请填写核实后的非负整数积分差额')
        if KookManualOnlineReview.objects.filter(cursor=cursor, status='PENDING_REVIEW',
                started_at__lt=row.started_at).exclude(pk=row.pk).exists():
            raise ValidationError('请先处理更早的人工在线来源')
        if KookOnlineInterval.objects.filter(account=account, account_cycle=row.business_cycle,
                processing_status='WAITING', started_at__lt=row.started_at).exists():
            raise ValidationError('请先处理更早的授权待核对区间')
        if KookOnlineInterval.objects.filter(account=account, account_cycle=row.business_cycle,
                started_at__lt=row.ended_at, ended_at__gt=row.started_at).exists():
            raise ValidationError('人工来源与已记录的在线区间重叠')
        if KookManualOnlineReview.objects.filter(account=account, business_cycle=row.business_cycle,
                status='COMPLETED', started_at__lt=row.ended_at, ended_at__gt=row.started_at).exclude(pk=row.pk).exists():
            raise ValidationError('人工来源与已处理的人工区间重叠')
        operation = ClubOperationConfig.objects.select_for_update().filter(pk=1).first()
        if not operation or not operation.points_enabled or operation.updated_at > row.started_at:
            raise ValidationError('原时段积分开关无法证明，请继续核对')
        if (row.ended_at - row.started_at).total_seconds() != row.online_seconds:
            raise ValidationError('原在线秒数与起止时间不一致')
        if cursor.rule_version_id != 0 or row.rule_segments.exclude(rule_version_id=0).exists():
            if cursor.last_ended_at and row.started_at < cursor.last_ended_at:
                return resolve_versioned_historical(row, account, binding, cursor, request_id=request_id,
                    actor_id=actor_id, reason=reason, points=points, allocations=allocations)
            return resolve_versioned_chronological(row, account, binding, cursor, request_id=request_id,
                actor_id=actor_id, reason=reason, points=points)
        rule = initial_rule()
        if cursor.rule_version_id != 0:
            raise ValidationError('原规则版本尚不能核验')
        from .kook_online_balances import verify as verify_balance
        verify_balance(cursor)
        if binding and binding.pk == cursor.binding_id and binding.business_cycle == row.business_cycle:
            binding.account = account
            verify_cursor(cursor, binding)
        total_ms = cursor.remainder_ms + row.online_seconds * 1000
        max_points = (total_ms // rule.window_ms) * rule.points
        if points > max_points:
            raise ValidationError('补发积分超过该时段及已有余量可产生的上限')
        from .kook_online_balances import accrue_interval, consume_current_pending
        prior_pending = cursor.pending_points
        after_pending = accrue_interval(cursor, rule, row.online_seconds * 1000)
        generated = after_pending - prior_pending
        if generated != max_points:
            raise ValidationError('人工在线片段与规则余额不一致')
        if points:
            ledger = change_points(locked_profile(account), points * 100,
                f'kook-manual-review:{row.pk}', 'KOOK_ONLINE', '平台人工核对在线积分补发')
        # The platform decision settles the newly computed difference, including
        # any deliberately rejected portion; older pending groups stay intact.
        if points:
            consume_current_pending(cursor, points, ledger)
        consume_current_pending(cursor, generated - points)
        new_remainder = total_ms % rule.window_ms
        if binding and binding.pk == cursor.binding_id and binding.business_cycle == row.business_cycle:
            binding.account = account
            binding.online_milliseconds += row.online_seconds * 1000
            binding.awarded_points += points
            binding.cycle_awarded_base = binding.awarded_points
            binding.cycle_online_base = (binding.online_milliseconds - new_remainder
                                         - cursor.pending_points * rule.window_ms)
            if binding.cycle_online_base < 0:
                raise ValidationError('原余量与待发事实不一致')
            binding.save(update_fields=['online_milliseconds', 'awarded_points', 'cycle_online_base', 'cycle_awarded_base'])
            sync_cursor(cursor, binding)
        elif cursor.binding_id is None:
            cursor.unbound_online_ms += row.online_seconds * 1000
            cursor.unbound_awarded_points += points
            cursor.remainder_ms = new_remainder
            cursor.save(update_fields=['unbound_online_ms', 'unbound_awarded_points', 'remainder_ms', 'updated_at'])
            from .kook_online_balances import save as save_balance
            save_balance(cursor)
        else:
            raise ValidationError('原绑定与周期不一致，不能补发')
        if cursor.last_ended_at is None or row.ended_at > cursor.last_ended_at:
            cursor.last_ended_at = row.ended_at
    elif points != 0:
        raise ValidationError('不发放时积分差额须为零')
    row.status = 'COMPLETED' if outcome == 'ALLOW' else 'REJECTED'
    row.resolution_request_id, row.resolution_outcome = request_id, outcome
    row.resolution_points = points
    row.resolution_reason, row.platform_actor_id = reason, actor_id
    row.resolved_at, row.points_ledger = timezone.now(), ledger
    row.save(update_fields=['status', 'resolution_request_id', 'resolution_outcome',
        'resolution_reason', 'resolution_points', 'platform_actor_id', 'resolved_at', 'points_ledger'])
    if same_cycle and binding and cursor.binding_id == binding.pk:
        binding.account = account
        replay_waiting(binding, cursor)
    else:
        cursor.blocked = KookManualOnlineReview.objects.filter(cursor=cursor, status='PENDING_REVIEW').exists()
        cursor.save(update_fields=['blocked', 'last_ended_at', 'updated_at'])
    return row
