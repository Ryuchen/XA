"""Serialize provider report mutations with store-account retirement."""
from functools import wraps

from django.conf import settings
from rest_framework.response import Response

from club_accounts.models import ClubAccount
from stores.db import transaction


def report_order_cycle_matches(order, account):
    """Check persisted service ownership while the order and lifecycle are locked."""
    if not getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False) or order.is_gift_snapshot:
        return True
    from orders.models import OrderParticipationSegment, OrderProvider
    seats = list(OrderProvider.objects.filter(order=order, provider_account_id=account.pk)
        .values_list('provider_business_cycle', flat=True))
    segments = list(OrderParticipationSegment.objects.filter(attribution__order=order,
        provider_account_id=account.pk).values_list('provider_business_cycle', flat=True))
    cycles = seats + segments
    return bool(cycles) and all(cycle == account.business_cycle for cycle in cycles)


def provider_report_operation(method):
    """Hold the provider lifecycle lock before any report or order row lock."""
    @wraps(method)
    def wrapped(self, request, *args, **kwargs):
        if not getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
            return method(self, request, *args, **kwargs)
        account = getattr(request, 'account', None)
        if not account or account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可操作本人报单'}, status=403)
        from club_accounts.provider_lifecycle import (ProviderLifecycleError,
            ensure_provider_lifecycle, lock_provider_operation)
        try:
            ensure_provider_lifecycle(account)
        except ProviderLifecycleError:
            return Response({'code': 503, 'msg': '陪玩账号状态暂不可核验，请稍后重试'}, status=503)
        with transaction.atomic():
            try:
                lock_provider_operation(account)
            except ProviderLifecycleError:
                return Response({'code': 503, 'msg': '陪玩账号状态暂不可核验，请稍后重试'}, status=503)
            if not ClubAccount.objects.filter(pk=account.pk, account_type=ClubAccount.AccountType.PROVIDER,
                    business_cycle=account.business_cycle, license_membership_active=True).exists():
                return Response({'code': 409, 'msg': '陪玩门店身份已变化，请重新登录'}, status=409)
            return method(self, request, *args, **kwargs)
    return wrapped
