"""Shared pass purchase eligibility; caller locks the profile for writes."""
from django.db.models import Q
from django.utils import timezone
from stores.db import transaction

from .models import ProviderPassPurchase

PASS_SALES_ENABLED_KEY = 'provider_pass_sales_enabled'


def pass_sales_enabled(*, lock=False):
    """No explicit choice preserves the existing availability of new passes."""
    from wallet.models import SystemConfig
    rows = SystemConfig.objects.filter(key=PASS_SALES_ENABLED_KEY)
    if lock:
        if not transaction.get_connection().in_atomic_block:
            raise RuntimeError('通行证销售开关当前读须在持锁事务内执行')
        rows = rows.select_for_update()
    value = rows.values_list('value', flat=True).first()
    if value is None or value == '1':
        return True
    if value == '0':
        return False
    raise ValueError('通行证销售开关无效，请联系管理员核对')


def lock_pass_config():
    """Serialize price changes with purchases, including first configuration."""
    from wallet.models import SystemConfig
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('通行证规则锁须在事务内使用')
    key = 'provider_pass_config_lock'
    # A read-then-insert get_or_create can deadlock on an absent unique key
    # when two first purchases run in MySQL's repeatable-read transactions.
    # Insert directly and tolerate an existing key before taking the row lock.
    SystemConfig.objects.bulk_create(
        [SystemConfig(key=key, value='', remark='通行证配置并发锁')], ignore_conflicts=True,
    )
    return SystemConfig.objects.select_for_update().get(key=key)


def pass_purchase_block_reason(profile, *, now=None):
    now = now or timezone.now()
    if profile.pass_expires_at and profile.pass_expires_at > now:
        return '当前通行证尚未到期，请到期后再购买'
    # Historical queued purchases still own their time; a new immediate pass
    # must never overlap or silently rewrite those previously paid rights.
    owner = Q(account__isnull=True, provider_id=profile.user_id)
    if profile.account_id:
        owner |= Q(account_id=profile.account_id)
    pending = ProviderPassPurchase.objects.filter(
        owner,
        starts_at__gt=now, expires_at__gt=now,
        refund__isnull=True,
    ).exists()
    if pending:
        return '存在历史待生效通行证，请先核对或处理后再购买'
    return ''
