"""Report maturity and financial/benefit processing, safe to retry."""
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from orders.models import Order
from wallet.models import ProviderReport
from .models import ConsumptionConfirmation


def refresh_report_clock(order):
    from orders.services import required_report_providers
    required = required_report_providers(order)
    reports = list(order.provider_reports.filter(provider_id__in=required))
    ready = None
    if required and None not in required and len(reports) == len(required) and all(
        row.submitted_at and row.status in ('PENDING', 'APPROVED') for row in reports):
        ready = max(row.submitted_at for row in reports)
    order.report_ready_at = ready
    order.consumption_ready_at = ready + timedelta(hours=72) if ready else None
    order.save(update_fields=['report_ready_at', 'consumption_ready_at'])
    return order.consumption_ready_at


def aftersale_deadline(order):
    return order.completed_at + timedelta(hours=72) if order.completed_at else None


def settlement_ready(order):
    deadline = aftersale_deadline(order)
    return bool(deadline and timezone.now() >= deadline and not order.aftersales.filter(status='OPEN').exists())


@transaction.atomic
def process_order(order_id):
    from orders.services import required_report_providers, settle_order
    from wallet.services import get_wallet
    from .services import update_vip
    from coupons.benefits import grant_periodic
    order = Order.objects.select_for_update().select_related('customer_account').get(pk=order_id)
    if (order.status != 'COMPLETED' or order.payment_status != 'PAID'
            or not order.customer_account_id):
        return False
    account = order.customer_account
    get_wallet(account_id=account.pk, for_update=True)
    account.refresh_from_db(fields=['business_cycle', 'boss_type'])
    if order.business_cycle != account.business_cycle:
        return False
    ready = refresh_report_clock(order)
    required = required_report_providers(order)
    approved = set(order.provider_reports.filter(status='APPROVED').values_list('provider_id', flat=True))
    if not required or None in required or not required.issubset(approved) or not settlement_ready(order):
        return False
    # Settle after the completion-based aftersale window; confirmation below
    # independently waits 72 hours from the last effective report submission.
    if order.settlement_mode == 'REPORT_APPROVAL' and not order.settled_at:
        settle_order(order)
    if order.is_gift_snapshot or not ready or ready > timezone.now():
        return False
    row, new = ConsumptionConfirmation.objects.get_or_create(order=order, defaults={
        'account': account, 'business_cycle': order.business_cycle, 'amount': order.amount})
    if not order.consumption_confirmed_at:
        order.consumption_confirmed_at = row.confirmed_at
        order.save(update_fields=['consumption_confirmed_at'])
    update_vip(account)
    grant_periodic(account)
    return new
