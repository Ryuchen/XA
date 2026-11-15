"""Serialize ordinary and gift refund accounting with provider retirement."""
from functools import wraps

from django.conf import settings
from rest_framework.response import Response

from stores.db import transaction
from .models import Order, OrderRefund


def protect_gift_refund(kind):
    def decorate(method):
        @wraps(method)
        def wrapped(self, request, *args, **kwargs):
            if not getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
                return method(self, request, *args, **kwargs)
            if kind == 'case':
                order_id = self.get_object().order_id
            elif kind == 'order':
                order_id = self.get_object().pk
            else:
                order_id = kwargs.get('order_id') or (args[0] if args else None)
            order = Order.objects.filter(pk=order_id).first()
            if not order:
                return method(self, request, *args, **kwargs)
            request_id = request.data.get('client_request_id')
            if isinstance(request_id, str) and OrderRefund.objects.filter(
                    order_id=order_id, client_request_id=request_id).exists():
                return method(self, request, *args, **kwargs)
            from club_accounts.models import ClubAccount
            from club_accounts.provider_lifecycle import ProviderLifecycleError
            from engagement.settlement_lifecycle import capture, lock, verify
            try:
                accounts = capture(order_id)
            except (ProviderLifecycleError, ValueError):
                return Response({'code': 409, 'msg': '退款陪玩或推荐人来源周期待核对'}, status=409)
            with transaction.atomic():
                try:
                    lock(accounts)
                    if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                        from console.payroll_event_barrier import lock_payment_refund_events
                        lock_payment_refund_events()
                    locked = Order.objects.select_for_update().get(pk=order_id)
                    verify(order_id, accounts)
                    cycles = {account.pk: account.business_cycle for account in accounts}
                    if any(not ClubAccount.objects.filter(pk=account.pk,
                            account_type=ClubAccount.AccountType.PROVIDER, license_membership_active=True,
                            business_cycle=account.business_cycle).exists() for account in accounts):
                        raise ValueError('退款陪玩账号周期待核对')
                    if (locked.is_gift_snapshot and (not locked.provider_account_id
                            or not locked.gift_provider_business_cycle
                            or cycles.get(locked.provider_account_id) != locked.gift_provider_business_cycle)):
                        raise ValueError('礼物收礼陪玩来源周期待核对')
                    from .models import OrderParticipationSegment
                    if (not locked.is_gift_snapshot and locked.provider_account_id
                            and not locked.providers.filter(provider_account_id=locked.provider_account_id).exists()
                            and not OrderParticipationSegment.objects.filter(attribution__order=locked,
                                provider_account_id=locked.provider_account_id).exists()):
                        raise ValueError('退款陪玩履约来源缺失')
                    if (locked.inviter_account_id and ClubAccount.objects.filter(
                            pk=locked.inviter_account_id, account_type=ClubAccount.AccountType.PROVIDER).exists()
                            and not hasattr(locked, 'referral_cycle_snapshot')):
                        raise ValueError('推荐人来源周期待核对')
                    from django.db.models import Q
                    from wallet.models import Transaction
                    provider_credits = Transaction.objects.filter(order=locked, status='SUCCESS',
                        reversal_of__isnull=True, amount__gt=0, tx_type__in=('INCOME', 'GIFT')).filter(
                            Q(wallet__account__account_type=ClubAccount.AccountType.PROVIDER)
                            | Q(wallet__account__isnull=True, wallet__user__role='PROVIDER'))
                    if any(account_id not in cycles for account_id in provider_credits.values_list(
                            'wallet__account_id', flat=True)):
                        raise ValueError('退款原入账陪玩归属待核对')
                except (ProviderLifecycleError, ValueError):
                    return Response({'code': 409, 'msg': '退款陪玩或推荐人来源周期待核对'}, status=409)
                return method(self, request, *args, **kwargs)
        return wrapped
    return decorate
