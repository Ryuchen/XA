"""Report maturity and financial/benefit processing, safe to retry."""
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from orders.models import Order
from orders.notifier import notify_order_update
from wallet.models import ProviderReport
from .models import ConsumptionConfirmation


def notify_committed_order(order_id):
    """Send the latest order state after the surrounding transaction commits."""
    def send():
        order = Order.objects.filter(pk=order_id).first()
        if order:
            notify_order_update(order)
    transaction.on_commit(send)


def refresh_report_clock(order):
    previous_clock = (order.report_ready_at, order.consumption_ready_at)
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
    if previous_clock != (order.report_ready_at, order.consumption_ready_at):
        # Order screens only consume order updates; report updates are private
        # to the provider and reviewers. Read after commit so all recipients
        # see the final committed state, including any settlement in this txn.
        notify_committed_order(order.pk)
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
    # Lock only the order before the cross-wallet mutex. A joined account row
    # would reverse the account/mutex order against refund VIP reconciliation.
    order = Order.objects.select_for_update().get(pk=order_id)
    if (order.status != 'COMPLETED' or order.payment_status not in ('PAID','PARTIALLY_REFUNDED')
            or not order.customer_account_id):
        return False
    # Direct payment has no trusted settlement/benefit accounting yet. Even
    # a COMPLETION-mode order bypassing its usual transition cannot mature
    # consumption or issue VIP benefits until that integration exists.
    from orders.direct_payment_models import DirectPaymentIntent
    if DirectPaymentIntent.objects.filter(order_id=order.pk).exists():
        return False
    account = order.customer_account
    from orders.financial_lock import lock_order_money
    lock_order_money()
    get_wallet(account_id=account.pk, for_update=True)
    account.refresh_from_db(fields=['business_cycle', 'boss_type'])
    if order.business_cycle != account.business_cycle:
        return False
    previous_clock = (order.report_ready_at, order.consumption_ready_at)
    ready = refresh_report_clock(order)
    clock_changed = previous_clock != (order.report_ready_at, order.consumption_ready_at)
    required = required_report_providers(order)
    approved = set(order.provider_reports.filter(status='APPROVED').values_list('provider_id', flat=True))
    if not required or None in required or not required.issubset(approved) or not settlement_ready(order):
        return False
    previous_state = (order.settled_at, order.consumption_confirmed_at)

    def notify_changed_order():
        if not clock_changed and (order.settled_at, order.consumption_confirmed_at) != previous_state:
            transaction.on_commit(lambda: notify_order_update(order))

    # Settle after the completion-based aftersale window; confirmation below
    # independently waits 72 hours from the last effective report submission.
    if order.settlement_mode == 'REPORT_APPROVAL' and not order.settled_at:
        settle_order(order)
    if order.is_gift_snapshot or not ready or ready > timezone.now():
        notify_changed_order()
        return False
    from orders.refunds import refunded_amount
    row, new = ConsumptionConfirmation.objects.get_or_create(order=order, defaults={
        'account': account, 'business_cycle': order.business_cycle, 'amount': order.amount,'refunded_amount':refunded_amount(order)})
    if not order.consumption_confirmed_at:
        order.consumption_confirmed_at = row.confirmed_at
        order.save(update_fields=['consumption_confirmed_at'])
    update_vip(account)
    grant_periodic(account)
    notify_changed_order()
    return new
