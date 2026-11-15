"""Stage verified per-person subsidies after a service transfer, without payout."""
from collections import defaultdict

from stores.db import transaction
from .models import OrderParticipationSegment, OrderPlatformSubsidyShare
from .settlement import SettlementError


def transfer_amounts(order, *, allow_recorded=False):
    """Recompute the still-unpaid entitlement of every participating account."""
    from .subsidy_segment_capture import transfer_testing_enabled
    from .segment_settlement_plan import build_segment_settlement_plan
    if not transfer_testing_enabled():
        raise SettlementError('安全箱分段转单尚未开放')
    plan = build_segment_settlement_plan(order, allow_recorded=allow_recorded,
                                         _allow_subsidy=True, _preview_unstarted=True)
    segments = {row.pk: row for row in OrderParticipationSegment.objects.filter(
        attribution__order=order).order_by('pk')}
    amounts, retained = defaultdict(int), defaultdict(int)
    for line in plan.segments:
        segment = segments.get(line.segment_id)
        if not segment or segment.provider_account_id != line.account_id:
            raise SettlementError('安全箱履约段身份待核对')
        amounts[line.account_id] += line.income
        if segment.released_at:
            retained[line.account_id] += line.income
    return amounts, retained


def sync_transfer_shares(order):
    """Rebuild amounts from original-share allocations and frozen entry rates."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('安全箱转单补贴须在订单事务内核对')
    amounts, retained = transfer_amounts(order)
    shares = list(OrderPlatformSubsidyShare.objects.select_for_update().filter(
        subsidy__order=order).select_related('assignment').order_by('pk'))
    assignments = list(order.providers.select_for_update().order_by('pk'))
    if (not shares or {row.assignment_id for row in shares} != {row.pk for row in assignments}
            or any(row.paid_at or row.assignment.settled_at for row in shares)
            or len({row.assignment.provider_account_id for row in shares}) != len(shares)):
        raise SettlementError('安全箱陪玩份额或已结算记录待核对')
    if set(amounts) - {row.assignment.provider_account_id for row in shares}:
        raise SettlementError('安全箱补贴归属待核对')
    for share in shares:
        account_id = share.assignment.provider_account_id
        share.amount = amounts[account_id]
        share.retained_amount = retained[account_id]
        share.save(update_fields=['amount', 'retained_amount'])
