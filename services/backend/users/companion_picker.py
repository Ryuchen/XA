"""Live service picker: four configured slots, four recent leaders, then levels."""
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db.models import BigIntegerField, Case, Q, Sum, When
from django.db.models.functions import Coalesce
from django.utils import timezone

from orders.models import Order, OrderProvider


RECENT_ACCEPTANCE_DAYS = 30


def _service_amounts(profiles, periods):
    """Public amounts use the same original-share facts as grab ranking."""
    from orders.grab_original_facts import original_amount_facts
    account_ids = [p.account_id for p in profiles if p.account_id]
    totals = {}
    for name, (start, end) in periods.items():
        facts = original_amount_facts(account_ids, since=start, as_of=end)
        totals[name] = {p.account_id or p.user_id:
                        facts[p.account_id].amount if p.account_id else None for p in profiles}
    return totals


def recent_service_amounts(profiles):
    now = timezone.now()
    return _service_amounts(profiles, {'recent': (now - timedelta(days=RECENT_ACCEPTANCE_DAYS), now)})['recent']


def companion_service_amounts(profiles, *, include_recent=False):
    now = timezone.now()
    month_start = timezone.localtime(now, ZoneInfo('Asia/Shanghai')).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    periods = {'monthly': (month_start, now)}
    if include_recent:
        periods['recent'] = (now - timedelta(days=RECENT_ACCEPTANCE_DAYS), now)
    return _service_amounts(profiles, periods)


def order_service_companions(items, profiles, amounts=None):
    if not items:
        return []
    levels = {p.account_id or p.user_id: p.level_rank for p in profiles}
    if amounts is None:
        amounts = recent_service_amounts(profiles)
    recommended = sorted(
        (item for item in items if item['recommendation_slot']),
        key=lambda item: item['recommendation_slot'],
    )[:4]
    selected = {item['id'] for item in recommended}
    popular = sorted(
        (item for item in items if item['id'] not in selected and (amounts.get(item['id']) or 0) > 0),
        key=lambda item: (-amounts[item['id']], -levels[item['id']], item['id']),
    )[:4]
    selected.update(item['id'] for item in popular)
    remaining = sorted(
        (item for item in items if item['id'] not in selected),
        key=lambda item: (-levels[item['id']], item['id']),
    )
    for group, rows in (('recommended', recommended), ('popular', popular), ('', remaining)):
        for item in rows:
            item['picker_group'] = group
    return recommended + popular + remaining
