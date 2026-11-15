"""Integer arithmetic shared by KOOK live, manual, and review flows.

Only the immutable initial rule is currently selectable. Persisted points and
intervals remain authoritative; this module does not enable rule changes.
"""
from rest_framework.exceptions import ValidationError


def initial_rule():
    from .models import KookOnlineRuleVersion
    rule = KookOnlineRuleVersion.objects.filter(pk=0).first()
    if not rule or rule.window_ms != 7200000 or rule.points != 1 or rule.effective_at is not None:
        raise ValidationError('KOOK 在线积分规则凭证异常')
    return rule


def active_rule(at=None):
    from django.utils import timezone
    from .models import KookOnlineRuleVersion
    at = at or timezone.now()
    return (KookOnlineRuleVersion.objects.filter(effective_at__lte=at)
            .order_by('-effective_at', '-revision').first() or initial_rule())


def earned(total_ms, online_base, award_base, rule):
    if (any(type(value) is not int or value < 0 for value in (total_ms, online_base, award_base))
            or total_ms < online_base or type(rule.window_ms) is not int or rule.window_ms <= 0
            or type(rule.points) is not int or rule.points <= 0):
        raise ValidationError('KOOK 在线积分累计凭证异常')
    return award_base + ((total_ms - online_base) // rule.window_ms) * rule.points


def remainder(total_ms, online_base, rule):
    if (type(total_ms) is not int or type(online_base) is not int
            or online_base < 0 or total_ms < online_base
            or type(rule.window_ms) is not int or rule.window_ms <= 0):
        raise ValidationError('KOOK 在线积分余量凭证异常')
    return (total_ms - online_base) % rule.window_ms


def base_after_unawarded(total_ms, prior_remainder, awarded, award_base, rule):
    paid = awarded - award_base
    if (paid < 0 or paid % rule.points or not 0 <= prior_remainder < rule.window_ms):
        raise ValidationError('KOOK 在线积分发放凭证异常')
    base = total_ms - prior_remainder - (paid // rule.points) * rule.window_ms
    if base < 0:
        raise ValidationError('KOOK 在线积分余量凭证异常')
    return base


def interval_segments(started_at, ended_at):
    """Partition a real observed interval at persisted rule effective instants."""
    from .models import KookOnlineRuleVersion
    if started_at >= ended_at:
        raise ValidationError('在线区间时间无效')
    initial_rule()
    versions = list(KookOnlineRuleVersion.objects.filter(effective_at__isnull=False,
        effective_at__lt=ended_at).order_by('effective_at', 'revision'))
    active = initial_rule()
    segments = []
    position = started_at
    for rule in versions:
        if rule.effective_at <= started_at:
            active = rule
            continue
        if rule.effective_at <= position:
            raise ValidationError('在线积分规则生效时点重复')
        segments.append((position, rule.effective_at, active))
        position, active = rule.effective_at, rule
    segments.append((position, ended_at, active))
    return segments
