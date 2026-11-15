"""Scoped, store-by-store read model for pass-refund funding shortfalls."""
from django.db import DatabaseError
from django.db.models import Count, Max, Sum
from django.utils import timezone

from users.models import ProviderPassRefund
from .context import store_context
from .platform_permissions import platform_stores_for

ACTION = 'pass_refund_shortfall:view'


def _store_summary(store):
    with store_context(store):
        from users.models import ProviderPassPointRedemption
        # The points evidence table is also part of this read model. Missing
        # schema is a store availability error, including when it has no rows.
        ProviderPassPointRedemption.objects.values_list(
            'id', 'purchase_id', 'ledger_id', 'points_hundredths', 'discount_units').first()
        values = ProviderPassRefund.objects.filter(shortfall_amount__gt=0).aggregate(
            count=Count('id'), shortfall=Sum('shortfall_amount'),
            # Probe both new columns, even for an empty store: an older store
            # must show unavailable instead of a misleading complete result.
            schema_points=Max('points_returned'), schema_ledger=Max('points_return_ledger_id'),
            schema_discount=Max('purchase__point_discount_amount'),
        )
    return {'count': values['count'], 'shortfall_amount': values['shortfall'] or 0}


def summary(account, *, token=None):
    from django.conf import settings
    from .platform_permissions import platform_may
    rows = []
    for store in platform_stores_for(account, ACTION).order_by('id'):
        row = {'slug': store.slug, 'name': store.name, 'currency_name': store.currency_name,
               'store_status': store.status, 'read_at': None, 'status': 'UNAVAILABLE', 'data': None}
        if not store.initialized_at:
            row['status'] = 'NOT_INITIALIZED'
        else:
            try:
                from .dynamic_database import discover_store_connection
                if discover_store_connection(store):
                    row['data'] = _store_summary(store)
                    row['status'], row['read_at'] = 'OK', timezone.now().isoformat()
            except (DatabaseError, ValueError):
                # Missing migrations and unavailable stores are not zero.
                pass
        # Original shortfalls above are historical facts, not unpaid balances.
        # The latter needs both directory and tenant evidence, and is available
        # only to the independently authorized platform repayment role.
        remaining = {'status': 'NOT_AUTHORIZED', 'amount': None, 'allocated_amount': None, 'refund_count': None}
        if token is not None and platform_may(account, 'pass_refund_repayment:register', store_slug=store.slug):
            if not getattr(settings, 'PLATFORM_PASS_REPAYMENT_ENABLED', False):
                remaining['status'] = 'NOT_ENABLED'
            elif row['status'] != 'OK':
                remaining['status'] = 'UNAVAILABLE'
            else:
                from .platform_pass_repayment import RepaymentError
                from .platform_pass_repayment_read import read
                try:
                    reconciliation = read(token, store.slug)
                    remaining['status'] = reconciliation['status']
                    if reconciliation['status'] == 'OK':
                        refunds = reconciliation['refunds']
                        remaining['amount'] = sum(item['remaining_amount'] for item in refunds)
                        remaining['allocated_amount'] = sum(item['allocated_amount'] for item in refunds)
                        remaining['refund_count'] = sum(item['remaining_amount'] > 0 for item in refunds)
                except (RepaymentError, DatabaseError, ValueError):
                    remaining['status'] = 'UNAVAILABLE'
        row['remaining_shortfall'] = remaining
        rows.append(row)
    visible = [row for row in rows if row['remaining_shortfall']['status'] != 'NOT_AUTHORIZED']
    return {'stores': rows, 'complete': all(row['status'] == 'OK' for row in rows),
            'reconciliation_complete': (all(row['remaining_shortfall']['status'] == 'OK' for row in visible)
                                        if visible else None),
            'generated_at': timezone.now().isoformat()}


def _points_facts(item):
    """Identify recorded points without treating inconsistent rows as paid."""
    purchase = item.purchase
    redemption = getattr(purchase, 'point_redemption', None)
    returned = item.points_return_ledger
    if not redemption and not purchase.point_discount_amount and not item.points_returned and not returned:
        return {'points_status': 'NOT_USED', 'points_redeemed': 0,
                'points_returned': 0, 'point_discount_amount': 0}
    spend = redemption.ledger if redemption else None
    valid = bool(redemption and spend and returned
        and purchase.account_id == item.account_id and item.amount == purchase.paid_amount
        and purchase.point_discount_amount == redemption.discount_units
        and redemption.points_hundredths == item.points_returned
        and spend.account_id == item.account_id and spend.kind == 'PASS_POINTS'
        and spend.amount == -redemption.points_hundredths and spend.event_key == f'pass:{purchase.pk}:points'
        and returned.account_id == item.account_id and returned.kind == 'PASS_POINTS_RETURN'
        and returned.amount == item.points_returned and returned.event_key == f'pass:{purchase.pk}:points-refund')
    return {'points_status': 'VERIFIED' if valid else 'REVIEW',
            'points_redeemed': redemption.points_hundredths if redemption else 0,
            'points_returned': item.points_returned, 'point_discount_amount': purchase.point_discount_amount}


def detail(store, page, page_size=30):
    with store_context(store):
        qs = (ProviderPassRefund.objects.filter(shortfall_amount__gt=0)
              .select_related('account', 'purchase', 'purchase__point_redemption__ledger', 'points_return_ledger')
              .order_by('-created_at', '-id'))
        total = qs.count()
        items = list(qs[(page - 1) * page_size:page * page_size])
        rows = [{'refund_id': item.pk, 'purchase_id': item.purchase_id,
                 'account_no': item.account.account_no, 'tier': item.purchase.get_tier_display(),
                 'refund_amount': item.amount, 'funded_amount': item.funded_amount,
                 'shortfall_amount': item.shortfall_amount, **_points_facts(item), 'created_at': item.created_at.isoformat()}
                for item in items]
    return {'slug': store.slug, 'name': store.name, 'currency_name': store.currency_name,
            'page': page, 'page_size': page_size, 'total': total, 'results': rows,
            'read_at': timezone.now().isoformat()}
