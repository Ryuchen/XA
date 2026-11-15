"""Protect a new staff refund execution before its order/financial scope."""
from functools import wraps

from django.conf import settings
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from stores.db import transaction


def _completed(case_id, proposal_id, request_id, operator_id):
    from .models import NegotiationExecution, PointsNegotiationPosting, PointsZeroFollowupTermination
    if NegotiationExecution.objects.filter(proposal_id=proposal_id, proposal__case_id=case_id,
            operator_id=operator_id, termination_processed_at__isnull=False).filter(
            Q(refund__client_request_id=request_id) | Q(zero_request_id=request_id)).exists():
        return True
    if PointsNegotiationPosting.objects.filter(proposal_id=proposal_id, proposal__case_id=case_id,
            operator_id=operator_id, termination_processed_at__isnull=False,
            points_refund__refund__client_request_id=request_id).exists():
        return True
    return PointsZeroFollowupTermination.objects.filter(proposal_id=proposal_id, proposal__case_id=case_id,
        operator_id=operator_id, request_id=request_id, terminated_at__isnull=False).exists()


def protect_execution(method):
    @wraps(method)
    def wrapped(self, request, *args, **kwargs):
        if request.method != 'POST' or not getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
            return method(self, request, *args, **kwargs)
        case = self.get_object()
        proposal_id = request.data.get('proposal_id')
        request_id = request.data.get('client_request_id')
        if not isinstance(proposal_id, int) or not isinstance(request_id, str):
            return method(self, request, *args, **kwargs)
        if _completed(case.pk, proposal_id, request_id, request.account.pk):
            return method(self, request, *args, **kwargs)
        from club_accounts.provider_lifecycle import ProviderLifecycleError
        from engagement.settlement_lifecycle import capture, lock, verify
        from .models import Order
        try:
            accounts = capture(case.order_id)
            with transaction.atomic():
                lock(accounts)
                if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                    from console.payroll_event_barrier import lock_payment_refund_events
                    lock_payment_refund_events()
                Order.objects.select_for_update().get(pk=case.order_id)
                if not _completed(case.pk, proposal_id, request_id, request.account.pk):
                    from .financial_lock import lock_order_money
                    lock_order_money()
                    verify(case.order_id, accounts)
                    from .models import OrderProvider
                    seats = list(OrderProvider.objects.select_for_update().filter(order_id=case.order_id)
                                 .values_list('provider_account_id', 'provider_business_cycle'))
                    cycles = {account.pk: account.business_cycle for account in accounts}
                    order = Order.objects.get(pk=case.order_id)
                    if ((order.provider_account_id and not seats)
                            or any(not account_id or not cycle or cycles.get(account_id) != cycle
                                   for account_id, cycle in seats)):
                        raise ValueError('订单陪玩席位来源周期缺失或已变化')
                return method(self, request, *args, **kwargs)
        except (ProviderLifecycleError, ValueError) as exc:
            raise ValidationError('结算陪玩或推荐人周期已变化，请核对后重试') from exc
    return wrapped
