"""Resolve one historical KOOK License decision, retaining source evidence."""
import hashlib
import json
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError

from club_accounts.models import ClubAccount
from console.models import AdminAuditLog
from .models import KookBinding, KookLicensePointsReview
from .services import change_points, locked_profile
from .kook_online_rules import initial_rule, earned

WINDOW = 7200000


def _digest(facts):
    return hashlib.sha256(json.dumps(facts, sort_keys=True, ensure_ascii=True,
                                     separators=(',', ':')).encode()).hexdigest()


def _time(value):
    try:
        return parse_datetime(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _facts(source):
    facts = source.request_body
    if (source.resource != 'kook-license-points' or source.method != 'SYSTEM'
            or not source.object_id.isascii() or not source.object_id.isdigit() or int(source.object_id) < 1
            or not isinstance(facts, dict) or facts.get('outcome') != 'UNKNOWN'):
        raise ValidationError('该记录不是授权未知的原始 KOOK 事件')
    return facts


def _validated(facts):
    integer_keys = ('account_id', 'business_cycle', 'online_milliseconds', 'previous_online_base',
                    'previous_awarded_points', 'cycle_awarded_base')
    version = facts.get('evidence_version')
    has_certificate = 'business_certificate_interval' in facts or 'business_certificate' in facts
    if (version not in (2, 3) or (version == 3 and not has_certificate)
            or any(type(facts.get(k)) is not int or facts[k] < 0 for k in integer_keys)
            or facts.get('account_type') not in ('BOSS', 'PROVIDER')
            or not isinstance(facts.get('channel_id'), str) or not facts.get('channel_id')
            or facts.get('membership_active') is not True
            or type(facts.get('points_enabled')) is not bool
            or facts['previous_online_base'] > facts['online_milliseconds']
            or facts['cycle_awarded_base'] > facts['previous_awarded_points']):
        raise ValidationError('原始证据不完整，不能推算补发')
    config_time = _time(facts.get('points_config_updated_at'))
    if not config_time or config_time.utcoffset() is None:
        raise ValidationError('积分开关的原始时间证据不完整')
    if facts.get('stage') == 'INTERVAL':
        start, end = _time(facts.get('from')), _time(facts.get('to'))
        elapsed = facts.get('elapsed_ms')
        if (not start or not end or start.utcoffset() is None or end.utcoffset() is None
                or type(elapsed) is not int or not 0 < elapsed <= 90000
                or int((end - start).total_seconds() * 1000) != elapsed or config_time > start):
            raise ValidationError('原始在线区间证据不完整')
    elif facts.get('stage') == 'AWARD':
        amount = facts.get('points_not_awarded')
        observed = _time(facts.get('observed_at'))
        rule = initial_rule()
        total = earned(facts['online_milliseconds'], facts['previous_online_base'], facts['cycle_awarded_base'], rule)
        prior = facts.get('prior_online_milliseconds')
        interval = facts.get('eligible_interval_ms')
        pending = facts.get('prior_pending_points')
        remainder = facts.get('prior_remainder_ms')
        if (not observed or observed.utcoffset() is None or config_time > observed
                or type(amount) is not int or amount <= 0 or total - facts['previous_awarded_points'] != amount
                or any(type(v) is not int or v < 0 for v in (prior, interval, pending, remainder))
                or interval > 90000 or prior + interval != facts['online_milliseconds']
                or prior < facts['previous_online_base']
                or remainder != (prior - facts['previous_online_base']) % rule.window_ms
                or pending != max(0, earned(prior, facts['previous_online_base'], facts['cycle_awarded_base'], rule)
                                  - facts['previous_awarded_points'])):
            raise ValidationError('原始待发积分凭证不一致')
    else:
        raise ValidationError('原始事件类型无效')
    return facts



def _verify_recorded_interval(source, facts):
    """Cross-check new, transactionally recorded evidence when available."""
    from .models import KookOnlineInterval
    rows = list(KookOnlineInterval.objects.select_for_update().filter(decision_source=source).order_by('pk')[:2])
    if not rows:
        if facts.get('stage') == 'AWARD' and facts.get('eligible_interval_ms') == 0:
            return  # A decision on previously accumulated points needs no new interval.
        raise ValidationError('缺少真实在线区间，不能推算补发')
    if len(rows) != 1:
        raise ValidationError('原始在线区间与授权记录不唯一，请人工核对')
    row = rows[0]
    if (row.binding_id != int(source.object_id) or row.account_id != facts.get('account_id')
            or row.account_cycle != facts.get('business_cycle')
            or row.binding_cycle != facts.get('binding_cycle')
            or row.channel_id != facts.get('channel_id')
            or row.rule_version_id != 0 or row.rule_revision != 0
            or row.rule_window_ms != WINDOW or row.rule_points != 1
            or row.rule_version.window_ms != WINDOW or row.rule_version.points != 1
            or row.rule_version.effective_at is not None
            or row.award_outcome != 'UNKNOWN'):
        raise ValidationError('在线区间身份、规则或授权决定不一致，请人工核对')
    if (row.online_base_before != facts.get('previous_online_base')
            or row.awarded_before != facts.get('previous_awarded_points')
            or row.award_base_before != facts.get('cycle_awarded_base')):
        raise ValidationError('在线区间的原累计基线不一致，请人工核对')
    if facts.get('stage') == 'INTERVAL':
        if (row.started_at != _time(facts.get('from')) or row.ended_at != _time(facts.get('to'))
                or row.elapsed_ms != facts.get('elapsed_ms') or row.interval_outcome != 'UNKNOWN'
                or row.online_before != facts.get('online_milliseconds')
                or row.online_after != row.online_before or row.awarded_after != row.awarded_before
                or row.online_base_after != row.online_base_before
                or row.award_base_after != row.award_base_before or row.points_ledger_id is not None):
            raise ValidationError('在线区间与原始待核对事件不一致')
    elif facts.get('stage') == 'AWARD':
        if (row.ended_at != _time(facts.get('checkpoint_at'))
                or row.elapsed_ms != facts.get('eligible_interval_ms')
                or row.online_before != facts.get('prior_online_milliseconds')
                or row.online_after != facts.get('online_milliseconds')
                or row.awarded_after != row.awarded_before
                or row.award_base_after != row.award_base_before
                or row.online_base_after != (row.online_after - facts.get('prior_remainder_ms')
                    - (row.awarded_before - row.award_base_before) * WINDOW)
                or row.points_ledger_id is not None or row.points_enabled_used is not True):
            raise ValidationError('在线区间与原始发放事件不一致')
    else:
        raise ValidationError('原始事件类型无效')


def _resolve_versioned_interval(source, facts, evidence_digest, context, *, request_id, actor_id, outcome, reason,
                                eligible_ms):
    """Resolve one original UNKNOWN interval against its recorded rule."""
    from django.utils import timezone
    from .kook_online_queue import align_rule, replay_waiting
    from .kook_online_rules import interval_segments
    from .kook_online_balances import accrue_interval, locked_pending, consume_all_pending
    from .models import KookOnlineProcessing
    binding, cursor, interval, before_online, before_awarded = context
    rule = interval.rule_version
    segments = interval_segments(interval.started_at, interval.ended_at)
    if (facts.get('stage') != 'INTERVAL' or interval.decision_source_id != source.pk
            or interval.interval_outcome != 'UNKNOWN' or interval.award_outcome != 'UNKNOWN'
            or len(segments) != 1 or segments[0][2].pk != rule.pk
            or interval.rule_revision != rule.revision or interval.rule_window_ms != rule.window_ms
            or interval.rule_points != rule.points or facts.get('rule_revision') != rule.revision
            or facts.get('rule_window_ms') != rule.window_ms or facts.get('rule_points') != rule.points
            or interval.account_id != facts.get('account_id') or interval.account_cycle != facts.get('business_cycle')
            or interval.binding_id != int(source.object_id) or interval.binding_cycle != facts.get('binding_cycle')
            or interval.started_at != _time(facts.get('from')) or interval.ended_at != _time(facts.get('to'))
            or interval.elapsed_ms != facts.get('elapsed_ms')):
        raise ValidationError('原授权区间与规则片段不一致')
    ledger, points, milliseconds = None, 0, 0
    align_rule(cursor, rule)
    if outcome == 'ALLOW':
        _validated(facts)
        from .kook_review_lifecycle import require as require_provider_lifecycle
        require_provider_lifecycle(binding.account)
        milliseconds = interval.elapsed_ms if eligible_ms is None else eligible_ms
        accrue_interval(cursor, rule, milliseconds)
        binding.online_milliseconds += milliseconds
        pending = sum(row.pending_points for row in locked_pending(cursor))
        if pending and facts['points_enabled']:
            points = pending * 100
            ledger = change_points(locked_profile(binding.account), points,
                f'kook-license-review:{source.pk}', 'KOOK_LICENSE_REVIEW', 'KOOK 在线原规则授权核对补发')
            consume_all_pending(cursor, pending, ledger)
            binding.awarded_points += pending
        binding.save(update_fields=['online_milliseconds', 'awarded_points'])
    review = KookLicensePointsReview.objects.create(source=source, request_id=request_id, platform_actor_id=actor_id,
        evidence_digest=evidence_digest, outcome=outcome, reason=reason, awarded_points=points,
        credited_milliseconds=milliseconds, points_ledger=ledger)
    KookOnlineProcessing.objects.create(interval=interval, online_before=before_online,
        online_after=binding.online_milliseconds, awarded_before=before_awarded,
        awarded_after=binding.awarded_points, points_ledger=ledger)
    interval.processing_status, interval.processed_at = 'DONE', timezone.now()
    interval.save(update_fields=['processing_status', 'processed_at'])
    cursor.last_ended_at = interval.ended_at
    cursor.save(update_fields=['last_ended_at', 'updated_at'])
    replay_waiting(binding, cursor)
    return review


def resolve_locked(source_id, *, request_id, actor_id, outcome, reason):
    source = AdminAuditLog.objects.select_for_update().filter(pk=source_id).first()
    if source is None:
        raise ValidationError('原始事件不存在')
    facts = _facts(source)
    evidence_digest = _digest(facts)
    existing = KookLicensePointsReview.objects.select_for_update().filter(source=source).first()
    if existing:
        if (existing.request_id != request_id or existing.outcome != outcome or existing.reason != reason
                or existing.evidence_digest != evidence_digest):
            raise ValidationError('该事件已核对，请使用原请求查询结果')
        ledger = existing.points_ledger
        if ((existing.awarded_points and (not ledger or ledger.account_id != facts.get('account_id')
                or ledger.event_key != f'kook-license-review:{source.pk}'
                or ledger.kind != 'KOOK_LICENSE_REVIEW' or ledger.amount != existing.awarded_points))
                or (not existing.awarded_points and ledger is not None)):
            raise ValidationError('积分流水与核对记录不一致')
        return existing
    if KookLicensePointsReview.objects.filter(request_id=request_id).exists():
        raise ValidationError('请求编号已用于其他事件')
    points, milliseconds, ledger, skip = 0, 0, None, ''
    # Discover the source without taking an interval lock; a live listener
    # holds account and binding before its per-cycle cursor and interval.
    from .models import KookOnlineInterval, KookOnlineProcessing
    source_interval = KookOnlineInterval.objects.filter(decision_source_id=source.pk).first()
    eligible_ms = None
    if outcome == 'ALLOW':
        from stores.certificate_admission import original_interval_milliseconds, require_original_certificate
        require_original_certificate(facts)
        if 'business_certificate_interval' in facts:
            if source_interval is None:
                raise ValidationError('缺少原在线区间的证照依据')
            eligible_ms = original_interval_milliseconds(facts['business_certificate_interval'],
                source_interval.started_at, source_interval.ended_at, source_interval.elapsed_ms)
    queue_context = None
    if source_interval is not None:
        account_for_queue = ClubAccount.objects.select_for_update().filter(pk=source_interval.account_id).first()
        binding_for_queue = KookBinding.objects.select_for_update().filter(pk=source_interval.binding_id,
            account_id=source_interval.account_id).first()
        if (account_for_queue and binding_for_queue and account_for_queue.license_membership_active
                and account_for_queue.business_cycle == source_interval.account_cycle
                and binding_for_queue.business_cycle == source_interval.binding_cycle == source_interval.account_cycle):
            binding_for_queue.account = account_for_queue
            from .kook_online_queue import lock_cursor, verify_cursor
            cursor = lock_cursor(binding_for_queue)
            verify_cursor(cursor, binding_for_queue)
            first = KookOnlineInterval.objects.select_for_update().filter(binding=binding_for_queue,
                binding_cycle=cursor.business_cycle, processing_status='WAITING').order_by('started_at', 'ended_at', 'pk').first()
            if first is None or first.pk != source_interval.pk:
                raise ValidationError('请先处理此账号更早的 KOOK 授权待核对区间')
            from .models import KookManualOnlineReview
            if KookManualOnlineReview.objects.filter(cursor=cursor, status='PENDING_REVIEW',
                                                      started_at__lt=first.started_at).exists():
                raise ValidationError('请先处理此账号更早的人工在线区间')
            queue_context = (binding_for_queue, cursor, first,
                             binding_for_queue.online_milliseconds, binding_for_queue.awarded_points)
    if queue_context is not None and source_interval.rule_version_id != 0:
        return _resolve_versioned_interval(source, facts, evidence_digest, queue_context,
            request_id=request_id, actor_id=actor_id, outcome=outcome, reason=reason, eligible_ms=eligible_ms)
    if outcome == 'ALLOW':
        facts = _validated(facts)
        account = ClubAccount.objects.select_for_update().filter(pk=facts['account_id']).first()
        binding = KookBinding.objects.select_for_update().filter(pk=source.object_id, account_id=facts['account_id']).first()
        if not account or not binding:
            raise ValidationError('原账号或 KOOK 绑定不存在，需人工核对')
        _verify_recorded_interval(source, facts)
        if account.account_type != facts['account_type']:
            raise ValidationError('原账号类型已变化，不能补发')
        from .kook_review_lifecycle import require as require_provider_lifecycle
        require_provider_lifecycle(account)
        if (account.business_cycle != facts['business_cycle'] or not account.license_membership_active
                or facts.get('binding_cycle') != facts['business_cycle']
                or binding.business_cycle != facts['business_cycle']):
            skip = 'CYCLE_CHANGED'
        elif not facts['points_enabled']:
            skip = 'POINTS_DISABLED'
        else:
            if eligible_ms is None:
                milliseconds = facts['elapsed_ms'] if facts['stage'] == 'INTERVAL' else facts['eligible_interval_ms']
            else:
                milliseconds = eligible_ms
            binding.online_milliseconds += milliseconds
            if (binding.cycle_online_base > binding.online_milliseconds
                    or binding.cycle_awarded_base > binding.awarded_points):
                raise ValidationError('当前 KOOK 累计凭证异常')
            total = earned(binding.online_milliseconds, binding.cycle_online_base, binding.cycle_awarded_base, initial_rule())
            additional = max(0, total - binding.awarded_points)
            if queue_context is not None and milliseconds:
                from .kook_online_balances import accrue_interval
                balance_pending = accrue_interval(queue_context[1], initial_rule(), milliseconds)
                if balance_pending != additional:
                    raise ValidationError('原授权区间与规则余额不一致，请人工核对')
            pending = facts['prior_pending_points'] if facts['stage'] == 'AWARD' else 0
            points = (additional + pending) * 100
            if points:
                ledger = change_points(locked_profile(account), points, f'kook-license-review:{source.pk}',
                                       'KOOK_LICENSE_REVIEW', 'KOOK 在线时长授权核对补发')
            if queue_context is not None and milliseconds:
                from .kook_online_balances import consume_current_pending
                consume_current_pending(queue_context[1], additional)
            if queue_context is not None and ledger is not None:
                from .models import KookOnlinePointAllocation
                KookOnlinePointAllocation.objects.create(cursor=queue_context[1], ledger=ledger,
                    rule_version=initial_rule(), points=points // 100)
            binding.awarded_points += additional
            binding.save(update_fields=['online_milliseconds', 'awarded_points'])
    review = KookLicensePointsReview.objects.create(source=source, request_id=request_id, platform_actor_id=actor_id,
        evidence_digest=evidence_digest,
        outcome=outcome, reason=reason, awarded_points=points, credited_milliseconds=milliseconds,
        points_ledger=ledger, skip_reason=skip)
    if queue_context is not None:
        binding_for_queue, cursor, interval, before_online, before_awarded = queue_context
        # Re-read the binding changed above in this same transaction; the
        # separate instance avoids silently replaying on stale counters.
        binding_for_queue.refresh_from_db(fields=['online_milliseconds', 'awarded_points',
            'cycle_online_base', 'cycle_awarded_base', 'business_cycle'])
        KookOnlineProcessing.objects.create(interval=interval, online_before=before_online,
            online_after=binding_for_queue.online_milliseconds, awarded_before=before_awarded,
            awarded_after=binding_for_queue.awarded_points, points_ledger=ledger)
        from django.utils import timezone
        interval.processing_status, interval.processed_at = 'DONE', timezone.now()
        interval.save(update_fields=['processing_status', 'processed_at'])
        from .kook_online_queue import sync_cursor
        sync_cursor(cursor, binding_for_queue)
        cursor.last_ended_at = interval.ended_at
        cursor.save(update_fields=['last_ended_at', 'updated_at'])
        from .kook_online_queue import replay_waiting
        replay_waiting(binding_for_queue, cursor)
    return review
