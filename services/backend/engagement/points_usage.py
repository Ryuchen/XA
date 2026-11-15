"""Store-local policy for using points, independent of awarding or refunds.

One lock order is used for every policy writer and spender: operation config,
then the versioned system dictionary row. Call spenders after their existing
wallet/profile locks; configuration writes never acquire a customer lock.
"""
import json

from django.conf import settings
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from wallet.models import SystemConfig
from .models import ClubOperationConfig

CONFIG_KEY = 'points_usage_switches'
FIELDS = ('redemption_enabled', 'shop_enabled', 'lottery_enabled')
DEFAULT = {**dict.fromkeys(FIELDS, True), 'version': 0}


def enabled():
    return bool(getattr(settings, 'POINTS_USAGE_SWITCHES_ENABLED', False))


def parse_rules(raw):
    if raw is None:
        return dict(DEFAULT)
    try:
        values = json.loads(raw)
        if (not isinstance(values, dict) or set(values) != {*FIELDS, 'version'}
                or any(type(values[field]) is not bool for field in FIELDS)
                or type(values['version']) is not int or not 0 <= values['version'] <= 2147483647):
            raise ValueError()
        return values
    except (ValueError, TypeError):
        raise ValueError('本店积分使用开关异常，请联系管理员核对') from None


def current_usage():
    operation = ClubOperationConfig.objects.filter(pk=1).values_list('points_enabled', flat=True).first()
    raw = SystemConfig.objects.filter(key=CONFIG_KEY).values_list('value', flat=True).first()
    return operation, parse_rules(raw)


def lock_usage():
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('积分使用开关必须在门店事务内锁定')
    from .services import config
    config()
    operation = ClubOperationConfig.objects.select_for_update().get(pk=1)
    row, _ = SystemConfig.objects.get_or_create(key=CONFIG_KEY, defaults={
        'value': json.dumps(DEFAULT, separators=(',', ':')), 'remark': '本店积分使用联动开关'})
    row = SystemConfig.objects.select_for_update().get(pk=row.pk)
    return operation, row, parse_rules(row.value)


def assert_usage_allowed():
    # The feature switch controls configuration exposure, not enforcement of
    # store rules already saved. Disabling it must not reopen points spending.
    try:
        operation, _, switches = lock_usage()
        if not operation.points_enabled or not all(switches[field] for field in FIELDS):
            raise ValidationError('本店积分使用暂时暂停，请稍后再试')
    except ValueError as exc:
        raise ValidationError(str(exc)) from None
