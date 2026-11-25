from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount, LegacyAccountMap
from club_accounts.permissions import IsClubAccountAuthenticated
from console.permissions import IsConsoleUser, get_account_permissions
from orders.addon_payment_models import OrderAddonPayment
from orders.models import Order, OrderProvider
from orders.settlement_breakdown import build_settlement_breakdown
from stores.context import current_store


class StaffSettlementBreakdownPermission(BasePermission):
    message = '需要订单查看和报单查看权限'

    def has_permission(self, request, view):
        return {'order:view', 'report:view'} <= get_account_permissions(request.account)


def _provider_scope(order):
    account_ids = {
        order.provider_account_id,
        *OrderProvider.objects.filter(order=order).exclude(provider_account_id=None)
        .values_list('provider_account_id', flat=True),
        *OrderAddonPayment.objects.filter(intent__order=order, paid_at__isnull=False)
        .exclude(intent__provider_id=None).values_list('intent__provider_id', flat=True),
    }
    if hasattr(order, 'original_attribution'):
        account_ids.update(order.original_attribution.segments.exclude(provider_account_id=None)
                           .values_list('provider_account_id', flat=True))
    account_ids.discard(None)

    # Add-on intents store a ClubAccount PK in provider_id.  Only the order's
    # legacy provider columns belong in the legacy-user mapping lookup.
    legacy_user_ids = {
        order.provider_id,
        *OrderProvider.objects.filter(order=order).exclude(provider_id=None)
        .values_list('provider_id', flat=True),
    }
    legacy_user_ids.discard(None)
    account_ids.update(LegacyAccountMap.objects.filter(
        legacy_user_id__in=legacy_user_ids, account__account_type=ClubAccount.AccountType.PROVIDER,
    ).values_list('account_id', flat=True))

    accounts = list(ClubAccount.objects.filter(
        pk__in=account_ids, account_type=ClubAccount.AccountType.PROVIDER,
    ).order_by('pk'))
    mapped_users = dict(LegacyAccountMap.objects.filter(
        account_id__in=[account.pk for account in accounts],
        account__account_type=ClubAccount.AccountType.PROVIDER,
    ).values_list('account_id', 'legacy_user_id'))
    legacy_users = {user.pk: user for user in get_user_model().objects.filter(pk__in=mapped_users.values())}
    return accounts, {account_id: legacy_users.get(user_id) for account_id, user_id in mapped_users.items()}, \
        bool(legacy_user_ids - set(mapped_users.values()))


class StaffOrderSettlementBreakdownView(APIView):
    permission_classes = [IsClubAccountAuthenticated, IsConsoleUser, StaffSettlementBreakdownPermission]

    def get(self, request, order_id):
        if current_store() is None or type(order_id) is not int or order_id <= 0:
            return Response({'code': 404, 'msg': '订单不存在或无权查看'}, status=404)
        order = Order.objects.filter(pk=order_id).first()
        if order is None:
            return Response({'code': 404, 'msg': '订单不存在或无权查看'}, status=404)
        accounts, legacy_users, unresolved_legacy = _provider_scope(order)
        providers = [build_settlement_breakdown(order, account, legacy_user=legacy_users.get(account.pk))
                     for account in accounts]
        unknown_items = []
        if unresolved_legacy:
            unknown_items.append({
                'code': 'PROVIDER_ACCOUNT_UNKNOWN',
                'message': '订单存在尚未关联业务账号的历史陪玩记录，相关逐段收益待核对',
                'segment_id': None,
                'payment_id': None,
            })
        response = Response({'code': 0, 'data': {
            'order_id': order.pk,
            'order_no': order.order_no,
            'store_slug': current_store().slug,
            'scope': 'STAFF_ORDER',
            'generated_at': timezone.now().isoformat(),
            'providers': providers,
            'unknown_items': unknown_items,
        }})
        response['Cache-Control'] = 'no-store'
        return response
