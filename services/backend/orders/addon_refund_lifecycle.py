"""Acquire provider lifecycle locks before add-on refund business locks."""
from functools import wraps

from django.conf import settings
from rest_framework.response import Response
from stores.db import transaction


def protect_addon_refund(fn):
    @wraps(fn)
    def wrapped(view, request, order_id, payment_id, proposal_id):
        if not getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
            return fn(view, request, order_id, payment_id, proposal_id)
        from .models import Order, OrderAddonPayment, OrderAddonRefund
        if OrderAddonRefund.objects.filter(order_id=order_id, payment_id=payment_id,
                                           proposal_id=proposal_id).exists():
            return fn(view, request, order_id, payment_id, proposal_id)
        from club_accounts.models import ClubAccount
        from club_accounts.provider_lifecycle import ProviderLifecycleError
        from engagement.settlement_lifecycle import capture, lock, verify
        try:
            accounts = capture(order_id)
        except (ProviderLifecycleError, ValueError):
            return Response({'code': 409, 'msg': '加购退款参与陪玩账号待核对'}, status=409)
        with transaction.atomic():
            try:
                lock(accounts)
                if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                    from console.payroll_event_barrier import lock_payment_refund_events
                    lock_payment_refund_events()
                Order.objects.select_for_update().get(pk=order_id)
                verify(order_id, accounts)
                cycles = {account.pk: account.business_cycle for account in accounts}
                if any(not ClubAccount.objects.filter(pk=account.pk,
                        account_type=ClubAccount.AccountType.PROVIDER, license_membership_active=True,
                        business_cycle=account.business_cycle).exists() for account in accounts):
                    raise ValueError('加购退款参与陪玩周期已变化')
                payment = OrderAddonPayment.objects.select_related('intent__segment').filter(
                    pk=payment_id, intent__order_id=order_id).first()
                if (not payment or payment.intent.provider_id != payment.intent.segment.provider_account_id
                        or not payment.intent.segment.provider_business_cycle
                        or cycles.get(payment.intent.provider_id) != payment.intent.segment.provider_business_cycle):
                    raise ValueError('加购来源履约段周期待核对')
            except (ProviderLifecycleError, ValueError, Order.DoesNotExist):
                return Response({'code': 409, 'msg': '加购退款来源周期待核对'}, status=409)
            return fn(view, request, order_id, payment_id, proposal_id)
    return wrapped
