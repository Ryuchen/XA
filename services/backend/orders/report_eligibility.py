"""Shared eligibility for opening a provider's order-linked report draft."""

from django.db.models import Q

from .models import Order


def report_entry_error(order, account, assignment=None, *, legacy_user=None):
    """Return the reason the report creation/open endpoint cannot be used.

    Existing reports remain available through the provider's report history.
    """
    if order.status not in (Order.Status.GRABBED, Order.Status.IN_SERVICE, Order.Status.COMPLETED):
        return '订单不存在、未完成或不属于当前陪玩'
    if assignment is None:
        if hasattr(order, 'requester_assignments'):
            assignment = next(iter(order.requester_assignments), None)
        else:
            owner = Q(provider_account=account)
            if legacy_user is not None:
                owner |= Q(provider_account__isnull=True, provider=legacy_user)
            assignment = order.providers.filter(owner).first()
    legacy_owner = (legacy_user is not None and not order.provider_account_id
                    and order.provider_id == legacy_user.pk)
    if order.provider_account_id != account.pk and not legacy_owner and assignment is None:
        return '订单不存在、未完成或不属于当前陪玩'
    if order.source_order_id or order.is_gift_snapshot:
        return '礼物打赏不需要服务报单'
    if assignment and assignment.released_at and assignment.settlement_base <= 0:
        return '转单后本人无结算份额，无需提交报单'
    return ''
