"""Per-store game/service account-field dictionary and final validation."""
import json

from rest_framework.exceptions import ValidationError
from stores.db import transaction
from wallet.models import SystemConfig

FIELDS = ('game_region', 'game_nickname', 'game_uid')
LABELS = {'game_region': '游戏大区', 'game_nickname': '游戏昵称', 'game_uid': '游戏 UID'}
GUARD = 'order_account_fields_guard'
PREFIX = 'order_account_fields_'


def key(scope, pk):
    if scope not in ('game', 'service') or type(pk) is not int or pk < 1:
        raise ValueError('游戏或服务标识无效')
    return f'{PREFIX}{scope}_{pk}'


def parse(raw, scope):
    if raw is None:
        return {'version': 0, 'fields': None if scope == 'service' else []}
    try:
        data = json.loads(raw)
        if (not isinstance(data, dict) or set(data) != {'version', 'fields'}
                or type(data['version']) is not int or not 1 <= data['version'] <= 2147483647):
            raise ValueError()
        fields = data['fields']
        if fields is None and scope == 'service':
            return data
        if (not isinstance(fields, list) or any(not isinstance(item, str) or item not in FIELDS for item in fields)
                or len(fields) != len(set(fields))):
            raise ValueError()
        return {'version': data['version'], 'fields': [field for field in FIELDS if field in fields]}
    except (TypeError, ValueError, KeyError):
        raise ValueError('本店游戏资料配置异常，请联系管理员核对') from None


def lock_guard():
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('游戏资料规则必须在门店事务中锁定')
    row, _ = SystemConfig.objects.get_or_create(key=GUARD, defaults={'value': '1', 'remark': '订单资料规则锁'})
    return SystemConfig.objects.select_for_update().get(pk=row.pk)


def current(scope, pk, *, lock=False):
    rows = SystemConfig.objects.filter(key=key(scope, pk))
    if lock:
        if not transaction.get_connection().in_atomic_block:
            raise RuntimeError('游戏资料规则当前读须在持锁事务内执行')
        rows = rows.select_for_update()
    raw = rows.values_list('value', flat=True).first()
    return parse(raw, scope)


def effective(service, *, lock=False):
    own = current('service', service.pk, lock=lock)
    if own['fields'] is not None:
        return {'fields': own['fields'], 'source': 'service'}
    if service.game_category_id:
        game = current('game', service.game_category_id, lock=lock)
        return {'fields': game['fields'], 'source': 'game'}
    return {'fields': [], 'source': 'default'}


def validate(service, values, *, lock=False):
    try:
        rules = effective(service, lock=lock)
    except ValueError as exc:
        raise ValidationError(str(exc)) from None
    missing = [LABELS[field] for field in rules['fields']
               if not isinstance(values.get(field), str) or not values[field].strip()]
    if missing:
        raise ValidationError('请填写' + '、'.join(missing))
    return rules


def lock_and_validate(service, values):
    lock_guard()
    # A prior read in this transaction may have established a MySQL
    # repeatable-read snapshot before a concurrent configuration committed.
    # Rule rows must be current reads after acquiring their shared guard.
    return validate(service, values, lock=True)
