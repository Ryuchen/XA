"""Account-scoped recent selections; at most four distinct companions."""
from django.db.models import Max
from orders.models import Order, OrderProvider


def recent_provider_ids(account):
    orders = Order.objects.filter(customer_account=account, business_cycle=account.business_cycle, payment_status=Order.PaymentStatus.PAID,
        source_order__isnull=True).exclude(status=Order.Status.CANCELLED).exclude(service__service_category__is_gift=True)
    single = orders.exclude(provider_account__isnull=True).values('provider_account_id').annotate(
        last_order=Max('id')).order_by('-last_order')[:4]
    teams = OrderProvider.objects.filter(order__in=orders).exclude(provider_account__isnull=True).values(
        'provider_account_id').annotate(last_order=Max('order_id')).order_by('-last_order')[:4]
    last = {}
    for row in [*single, *teams]:
        pk = row['provider_account_id']
        last[pk] = max(last.get(pk, 0), row['last_order'])
    return sorted(last, key=last.get, reverse=True)[:4]
