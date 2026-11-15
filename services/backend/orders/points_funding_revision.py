"""Pure planning of a new funding reservation after an additional refund.

Inputs must already be verified under the order and receipt locks. No receipt,
provider payout, refund or release is created here. In particular, the new
provider recovery may still be hypothetical until its wallet/deposit posting.
"""
from dataclasses import dataclass

from .points_funding_allocation import PointsFundingAllocationError, plan_release


@dataclass(frozen=True)
class FundingRevision:
    paid_provider_units: int
    remaining_provider_units: int
    allocated_units: int
    required_units: int
    release_units: int
    shortfall_units: int
    # Allocation id, receipt id, original allocation, previously released.
    allocations: tuple
    # Allocation id, receipt id, amount proposed for release.
    releases: tuple


def plan_unpaid_funding_revision(*, allocations, previous_required, original_reservation, projected):
    """Plan a second refund before either provider has been paid.

    The first refund can have either no reservation (including a partially
    funded order) or an existing fully funded reservation without payouts.
    The caller must verify the actual first refund and every allocation before
    invoking this pure calculation; planned provider recovery is not cash.
    """
    if (type(previous_required) is not int or previous_required < 0
            or not isinstance(allocations, (list, tuple))
            or (original_reservation is not None and not isinstance(original_reservation, (list, tuple)))):
        raise PointsFundingAllocationError('未结算退款的补资依据无效')
    rows = []
    for item in allocations:
        if not isinstance(item, (list, tuple)) or len(item) != 4:
            raise PointsFundingAllocationError('未结算退款的补资分配无效')
        allocation_id, receipt_id, amount, released = item
        if (type(allocation_id) is not int or allocation_id <= 0
                or type(receipt_id) is not int or receipt_id <= 0
                or type(amount) is not int or amount <= 0
                or type(released) is not int or not 0 <= released <= amount):
            raise PointsFundingAllocationError('未结算退款的补资金额无效')
        rows.append((allocation_id, receipt_id, amount, released))
    rows.sort()
    if len({row[0] for row in rows}) != len(rows):
        raise PointsFundingAllocationError('未结算退款的补资分配重复')
    net = sum(amount - released for _, _, amount, released in rows)
    reservation = [(pk, amount - released) for pk, _, amount, released in rows if amount > released]
    if net > previous_required or (original_reservation is not None and
            (list(original_reservation) != reservation or net != previous_required)):
        raise PointsFundingAllocationError('未结算退款原补资或预留不一致')
    values = (projected.net_coin_units, projected.net_principal_units, projected.net_gift_units,
              projected.net_compensation_units, projected.provider_income_units,
              projected.planned_provider_external_units, projected.inviter_income_units,
              projected.store_income_units, projected.required_units, projected.allocated_units,
              projected.release_units, projected.remaining_units)
    if any(type(value) is not int or value < 0 for value in values):
        raise PointsFundingAllocationError('未结算退款资金版本金额无效')
    required = max(projected.provider_income_units + projected.inviter_income_units
                   - projected.net_principal_units - projected.planned_provider_external_units, 0)
    store = max(projected.net_principal_units + projected.planned_provider_external_units
                - projected.provider_income_units - projected.inviter_income_units, 0)
    if (projected.net_coin_units != projected.net_principal_units + projected.net_gift_units + projected.net_compensation_units
            or projected.allocated_units != net or projected.required_units != required
            or projected.store_income_units != store or projected.release_units != max(net - required, 0)
            or projected.remaining_units != max(required - net, 0)
            or projected.net_principal_units + projected.planned_provider_external_units + net + projected.remaining_units
               != projected.provider_income_units + projected.inviter_income_units + projected.store_income_units
                  + projected.release_units):
        raise PointsFundingAllocationError('未结算退款资金版本不守恒')
    parts = plan_release(allocations=[(pk, amount, released) for pk, _, amount, released in rows],
                         amount=projected.release_units)
    receipt_by_allocation = {pk: receipt for pk, receipt, _, _ in rows}
    releases = tuple((part.allocation_id, receipt_by_allocation[part.allocation_id], part.amount) for part in parts)
    return FundingRevision(0, projected.provider_income_units, net, required, projected.release_units,
                           projected.remaining_units, tuple(rows), releases)


def plan_funding_revision(*, allocations, original_reservation, previous_required,
                          paid_provider_units, projected):
    """Build a balanced revision without changing the original reservation."""
    if (type(previous_required) is not int or previous_required < 0
            or type(paid_provider_units) is not int or paid_provider_units < 0
            or not isinstance(allocations, (list, tuple))
            or not isinstance(original_reservation, (list, tuple))):
        raise PointsFundingAllocationError('补资版本依据无效')
    ordered = []
    for row in allocations:
        if not isinstance(row, (list, tuple)) or len(row) != 4:
            raise PointsFundingAllocationError('补资版本分配记录无效')
        allocation_id, receipt_id, amount, released = row
        if (type(allocation_id) is not int or allocation_id <= 0
                or type(receipt_id) is not int or receipt_id <= 0
                or type(amount) is not int or amount <= 0
                or type(released) is not int or not 0 <= released <= amount):
            raise PointsFundingAllocationError('补资版本分配金额无效')
        ordered.append((allocation_id, receipt_id, amount, released))
    ordered.sort()
    if len({row[0] for row in ordered}) != len(ordered):
        raise PointsFundingAllocationError('补资版本分配记录重复')
    net = sum(amount - released for _, _, amount, released in ordered)
    reservation = [(pk, amount - released) for pk, _, amount, released in ordered if amount > released]
    if list(original_reservation) != reservation or net != previous_required:
        raise PointsFundingAllocationError('原整单补资预留与已核验分配不一致')
    values = (projected.net_coin_units, projected.net_principal_units, projected.net_gift_units,
              projected.net_compensation_units, projected.provider_income_units,
              projected.planned_provider_external_units, projected.inviter_income_units,
              projected.store_income_units, projected.required_units, projected.allocated_units,
              projected.release_units, projected.remaining_units)
    if any(type(value) is not int or value < 0 for value in values):
        raise PointsFundingAllocationError('退款后资金版本金额无效')
    expected_required = max(projected.provider_income_units + projected.inviter_income_units
                            - projected.net_principal_units - projected.planned_provider_external_units, 0)
    expected_store = max(projected.net_principal_units + projected.planned_provider_external_units
                         - projected.provider_income_units - projected.inviter_income_units, 0)
    if (projected.net_coin_units != projected.net_principal_units + projected.net_gift_units + projected.net_compensation_units
            or projected.allocated_units != net or projected.required_units != expected_required
            or projected.store_income_units != expected_store or paid_provider_units > projected.provider_income_units
            or projected.release_units != max(net - expected_required, 0)
            or projected.remaining_units != max(expected_required - net, 0)):
        raise PointsFundingAllocationError('退款后资金版本不守恒')
    if (projected.net_principal_units + projected.planned_provider_external_units + net + projected.remaining_units
            != projected.provider_income_units + projected.inviter_income_units + projected.store_income_units
               + projected.release_units):
        raise PointsFundingAllocationError('退款后资金版本来源和去向不一致')
    parts = plan_release(allocations=[(pk, amount, released) for pk, _, amount, released in ordered],
                         amount=projected.release_units)
    receipt_by_allocation = {pk: receipt for pk, receipt, _, _ in ordered}
    releases = tuple((part.allocation_id, receipt_by_allocation[part.allocation_id], part.amount) for part in parts)
    return FundingRevision(paid_provider_units, projected.provider_income_units - paid_provider_units,
                           net, expected_required, projected.release_units, projected.remaining_units,
                           tuple(ordered), releases)
