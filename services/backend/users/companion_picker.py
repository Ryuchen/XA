"""Live service picker: four configured slots, four recent leaders, then levels."""
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db.models import BigIntegerField, Case, Q, Sum, When
from django.db.models.functions import Coalesce
from django.utils import timezone

from orders.models import Order, OrderProvider


RECENT_ACCEPTANCE_DAYS = 30


def _service_amounts(profiles, periods):
    """Aggregate paid service shares for several periods in two roster-wide queries."""
    totals = {name: {} for name in periods}
    if not profiles:
        return totals
    account_ids = [p.account_id for p in profiles if p.account_id]
    user_ids = [p.user_id for p in profiles]
    by_account = {p.account_id: p.account_id or p.user_id for p in profiles if p.account_id}
    by_user = {p.user_id: p.account_id or p.user_id for p in profiles}
    since = min(start for start, _ in periods.values())
    until = max(end for _, end in periods.values())
    orders = Order.objects.filter(
        payment_status__in=[Order.PaymentStatus.PAID,Order.PaymentStatus.PARTIALLY_REFUNDED], source_order__isnull=True,
    ).exclude(status=Order.Status.CANCELLED).exclude(
        Q(is_gift_snapshot=True) | Q(service__service_category__is_gift=True),
    )
    shares = OrderProvider.objects.filter(order__in=orders, created_at__gte=since, created_at__lte=until).filter(
        Q(provider_account_id__in=account_ids)
        | Q(provider_account__isnull=True, provider_id__in=user_ids),
    ).filter(Q(released_at__isnull=True) | Q(settlement_base__gt=0))
    legacy = orders.filter(providers__isnull=True).annotate(
        accepted_at=Coalesce('grabbed_at', 'created_at'),
    ).filter(accepted_at__gte=since, accepted_at__lte=until).filter(
        Q(provider_account_id__in=account_ids)
        | Q(provider_account__isnull=True, provider_id__in=user_ids),
    )
    partial_ids = set()
    for queryset, field, date_field, order_prefix, order_id in (
        (shares, 'settlement_base', 'created_at', 'order__', 'order_id'),
        (legacy, 'amount', 'accepted_at', '', 'pk'),
    ):
        aggregates = {
            name: Sum(field, filter=Q(**{date_field + '__gte': start, date_field + '__lte': end}))
            for name, (start, end) in periods.items()
        }
        queryset = queryset.annotate(partial_order_id=Case(
            When(**{order_prefix + 'payment_status': Order.PaymentStatus.PARTIALLY_REFUNDED}, then=order_id),
            output_field=BigIntegerField(),
        ))
        for row in queryset.order_by().values('provider_account_id', 'provider_id', 'partial_order_id').annotate(**aggregates):
            if row['partial_order_id'] is not None:
                partial_ids.add(row['partial_order_id'])
                continue
            pk = by_account.get(row['provider_account_id']) if row['provider_account_id'] else by_user.get(row['provider_id'])
            if pk is not None:
                for name in periods:
                    if row[name] is not None:
                        totals[name][pk] = totals[name].get(pk, 0) + row[name]
    from orders.analytics import partial_share_rows
    # Reconcile only partially refunded orders found in the two scoped reads.
    # Ordinary rosters keep their two-query cost and never scan unrelated history.
    partial_rows = partial_share_rows(orders.filter(pk__in=partial_ids)) if partial_ids else ()
    for row in partial_rows:
        pk = by_account.get(row['provider_account_id']) if row['provider_account_id'] else by_user.get(row['provider_id'])
        if pk is not None:
            for name,(start,end) in periods.items():
                if start <= row['accepted_at'] <= end:
                    totals[name][pk] = totals[name].get(pk,0)+row['amount']
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
        (item for item in items if item['id'] not in selected and amounts.get(item['id'], 0) > 0),
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
