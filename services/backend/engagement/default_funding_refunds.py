"""Exact source restoration for first-release default wallet payments."""
from collections import defaultdict

from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .models import DefaultPaymentPointsSnapshot, DefaultPaymentSource, OrderPointAllocation, PointFundingLot


def verified_sources(order, snapshot):
    """Lock and verify the entire original payment, not just this refund."""
    allocations = list(OrderPointAllocation.objects.select_for_update().filter(order=order).select_related('lot').order_by('pk'))
    sources = list(DefaultPaymentSource.objects.select_for_update().filter(snapshot=snapshot)
                   .select_related('allocation__lot').order_by('allocation_id', 'pk'))
    by_allocation = defaultdict(list)
    for source in sources:
        by_allocation[source.allocation_id].append(source)
    if (snapshot.order_id != order.pk or not allocations or len(by_allocation) != len(allocations)
            or sum(row.amount for row in allocations) != order.amount):
        raise ValidationError('订单付款来源不完整，请先核对')
    for allocation in allocations:
        rows = by_allocation.get(allocation.pk, [])
        lot = allocation.lot
        if (lot.account_id != order.customer_account_id or sum(row.amount for row in rows) != allocation.amount
                or sum(row.refunded_amount for row in rows) != allocation.refunded_amount
                or allocation.released != (allocation.refunded_amount == allocation.amount)):
            raise ValidationError('订单付款来源或退款记录不一致，请先核对')
        kinds = {row.kind: row.amount for row in rows}
        if lot.kind == PointFundingLot.Kind.RECHARGE:
            gift = allocation.source_gift_amount
            if (lot.gift_remaining is None or gift is None or kinds.get('GIFT', 0) != gift
                    or kinds.get('PREPAID', 0) != allocation.amount - gift or kinds.get('COMPENSATION', 0)):
                raise ValidationError('充值支付来源快照不一致，请先核对')
        elif kinds != {lot.kind: allocation.amount}:
            raise ValidationError('支付来源类型不一致，请先核对')
    principal = sum(row.amount for row in sources if row.kind == 'PREPAID')
    expected_points = principal * snapshot.points_rate * snapshot.birthday_multiplier // 100
    if (snapshot.points_rate > 1000 or snapshot.birthday_multiplier > 10
            or sum(row.points_amount for row in allocations) != expected_points):
        raise ValidationError('付款积分倍率快照不一致，请先核对')
    return sources


def refund_default_sources(order, amount):
    """Restore exact original sources in principal, gift, compensation order."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('退款来源恢复必须在门店事务内执行')
    snapshot = DefaultPaymentPointsSnapshot.objects.select_for_update().filter(order=order).first()
    if snapshot is None:
        raise ValidationError('订单缺少付款来源快照，请先核对')
    sources = verified_sources(order, snapshot)
    order_index = {'PREPAID': 0, 'GIFT': 1, 'COMPENSATION': 2}
    sources.sort(key=lambda row: (order_index[row.kind], row.allocation_id, row.pk))
    previous = sum(row.refunded_amount for row in sources)
    target = previous + amount
    from django.db.models import Sum
    from orders.models import OrderRefund
    posted_total = OrderRefund.objects.filter(order_id=order.pk).aggregate(total=Sum('amount'))['total'] or 0
    if amount <= 0 or target > order.amount or target != posted_total:
        raise ValidationError('退款来源金额异常，请先核对')
    consumed = 0
    changes = []
    for row in sources:
        expected = min(row.amount, max(0, previous - consumed))
        desired = min(row.amount, max(0, target - consumed))
        if row.refunded_amount != expected:
            raise ValidationError('退款来源顺序不一致，请先核对')
        changes.append((row, desired - expected))
        consumed += row.amount
    lots = {lot.pk: lot for lot in PointFundingLot.objects.select_for_update().filter(
        pk__in={row.allocation.lot_id for row in sources}, account_id=order.customer_account_id).order_by('pk')}
    if len(lots) != len({row.allocation.lot_id for row in sources}):
        raise ValidationError('原付款来源不属于老板账号')
    changed_allocations = {}
    for row, delta in changes:
        if not delta:
            continue
        lot = lots[row.allocation.lot_id]
        lot.remaining += delta
        fields = ['remaining']
        if lot.kind == PointFundingLot.Kind.RECHARGE and row.kind == 'GIFT':
            lot.gift_remaining += delta
            fields.append('gift_remaining')
        lot.save(update_fields=fields)
        row.refunded_amount += delta
        row.save(update_fields=['refunded_amount'])
        allocation = changed_allocations.setdefault(row.allocation_id, row.allocation)
        allocation.refunded_amount += delta
    for allocation in changed_allocations.values():
        allocation.released = allocation.refunded_amount == allocation.amount
        allocation.save(update_fields=['refunded_amount', 'released'])
