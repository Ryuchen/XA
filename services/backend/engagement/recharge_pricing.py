"""Per-store prepaid bonus calculation with a serialized configuration snapshot."""
import hashlib
import json

from stores.db import transaction
from wallet.models import SystemConfig
from .models import RechargePackage

LOCK_KEY = 'recharge_package_config_lock'
MAX_AMOUNT = 2147483647


def lock_recharge_packages():
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('充值档位锁须在事务内使用')
    SystemConfig.objects.get_or_create(key=LOCK_KEY, defaults={'value': '', 'remark': '预存档位并发锁'})
    return SystemConfig.objects.select_for_update().get(key=LOCK_KEY)


def recharge_quote(amount):
    if type(amount) is not int or not 1 <= amount <= MAX_AMOUNT:
        raise ValueError('充值金额必须为正整数且不超过系统范围')
    rows = list(RechargePackage.objects.filter(active=True).order_by('-amount', 'id').values('id', 'amount', 'gift_amount'))
    if any(type(row['amount']) is not int or row['amount'] <= 0 or row['gift_amount'] < 0 for row in rows):
        raise ValueError('门店预存档位无效，请先核对配置')
    version = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    remaining, gift, matches = amount, 0, []
    for row in rows:
        count, remaining = divmod(remaining, row['amount'])
        if count:
            subtotal = count * row['gift_amount']
            gift += subtotal
            if gift > MAX_AMOUNT:
                raise ValueError('预存赠送总额超过系统范围，请核对档位')
            matches.append({**row, 'count': count, 'gift_total': subtotal})
    return {'amount': amount, 'gift_amount': gift, 'remaining': remaining, 'matches': matches,
            'version': version, 'points_rule': 'VIP_AT_PAYMENT'}
