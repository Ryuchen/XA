"""Accrue verified safety-box agreement shares by their event periods."""
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from orders.models import Order, OrderPlatformSubsidyShare, SubsidyAgreementExecution


@transaction.atomic
def agreement_period_cost(order, execution, start, end):
    from orders.subsidy_execution import verify_execution
    from .payroll_calculation import subsidy_liability

    order = Order.objects.select_for_update().get(pk=order.pk)
    execution = SubsidyAgreementExecution.objects.select_for_update().select_related('proposal__case').get(pk=execution.pk)
    execution.order = order
    shares = list(OrderPlatformSubsidyShare.objects.select_for_update().filter(subsidy__order=order)
                  .select_related('assignment', 'subsidy', 'expense_transaction', 'income_transaction').order_by('pk'))
    if not shares or not isinstance(execution.shares, list) or len(shares) != len(execution.shares):
        return None
    try:
        verify_execution(execution, locked_shares=shares)
    except (ValidationError, DjangoValidationError, ValueError, KeyError, TypeError):
        return None
    if (execution.order_id != order.pk or execution.completion_at != order.completed_at
            or execution.processed_at < execution.accepted_at
            or (start <= execution.processed_at < end) != (start <= execution.created_at < end)):
        return None
    if execution.completed_before_agreement:
        if execution.accepted_at < execution.completion_at:
            return None
    else:
        if execution.completion_at != execution.accepted_at:
            return None
    total = 0
    for share, term in zip(shares, execution.shares):
        # verify_execution checks the complete share list and, for transfers,
        # verifies the immutable segment and agreement evidence under the
        # corresponding capability gate.
        if (share.pk != term['share_id'] or share.amount != term['retained']
                or subsidy_liability(share) != term['retained']):
            return None
        if start <= execution.completion_at < end:
            total += term['original'] if execution.completed_before_agreement else term['retained']
        if execution.completed_before_agreement and start <= execution.processed_at < end:
            total += term['retained'] - term['original']
    return total
