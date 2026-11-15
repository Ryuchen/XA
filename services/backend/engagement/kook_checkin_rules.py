"""Select the actual store rule at an observed KOOK check-in time."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from .models import KookCheckinRuleVersion, KookPointsConfig

BEIJING = ZoneInfo('Asia/Shanghai')


def points_for_manual_day(day, observed_at=None):
    # Serialize with rule changes and the automatic listener before choosing
    # an amount; the pre-configuration rule was the established five points.
    KookPointsConfig.objects.get_or_create(pk=1, defaults={'guild_id': '', 'checkin_channel_id': ''})
    config = KookPointsConfig.objects.select_for_update().get(pk=1)
    if config.enabled:
        raise ValidationError('KOOK自动计分已开启，请勿人工重复记分')
    start = datetime.combine(day, time.min, tzinfo=BEIJING)
    end = start + timedelta(days=1)
    if observed_at is not None:
        if timezone.is_naive(observed_at) or timezone.localtime(observed_at, BEIJING).date() != day or observed_at > timezone.now():
            raise ValidationError('实际签到时间须属于所选日期且不得晚于当前时间')
        version = KookCheckinRuleVersion.objects.filter(effective_at__lte=observed_at).order_by('-effective_at', '-revision').first()
        return (version.points, version.revision) if version else (5, 0)
    before = KookCheckinRuleVersion.objects.filter(effective_at__lt=start).order_by('-effective_at', '-revision').first()
    baseline = before.points if before else 5
    versions = list(KookCheckinRuleVersion.objects.filter(effective_at__gte=start, effective_at__lt=end).order_by('effective_at', 'revision'))
    if any(item.points != baseline for item in versions):
        raise ValidationError('所选日期的签到规则发生变更，请填写实际签到时间')
    return (baseline, before.revision if before else 0)
