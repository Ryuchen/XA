"""Isolated read-only funding check after a second agreed points refund.

The first provider's payout and both refund histories must already exist.
This does not allocate additional money or release the second provider.
"""
from django.conf import settings
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .financial_lock import lock_order_money
from .models import Order, OrderPointsFundingRelease, PointsNegotiationPosting
from .points_checkout_test import require_isolated_checkout
from .points_funding_evidence import VerifiedOrderFunding, verified_order_allocations_locked
from .points_funding_plan import PointsFundingPlanError
from .settlement import SettlementError


@transaction.atomic
def verify_followup_funding(order_id):
    from .points_business_access import permits
    formal = any(permits(phase=phase, kind=kind) for kind in ('service', 'addon')
                 for phase in ('refund', 'report', 'settlement', 'funding'))
    if not formal:
        try:
            require_isolated_checkout()
        except ValidationError as exc:
            raise PointsFundingPlanError('后续退款补资核对尚未开放') from exc
    if ((not formal and not getattr(settings, 'SERVICE_POINTS_FOLLOWUP_FUNDING_TESTING', False))
            or type(order_id) is not int or order_id <= 0):
        raise PointsFundingPlanError('后续退款补资核对尚未开放')
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if not order:
        raise PointsFundingPlanError('订单不存在')
    if formal and not any(permits(order, phase=phase, kind=kind) for kind in ('service', 'addon')
                          for phase in ('refund', 'report', 'settlement', 'funding')):
        raise PointsFundingPlanError('后续退款补资不属于当前订单')
    lock_order_money()
    from .points_receipt_usage import prelock_followup_receipts, followup_eligible
    prelock_followup_receipts(order)
    postings = list(PointsNegotiationPosting.objects.select_for_update().filter(
        points_refund__redemption__order=order).select_related('proposal__points_terms').order_by('pk'))
    if len(postings) != 2 or order.refund_records.count() != 2:
        raise PointsFundingPlanError('后续退款记录不完整')
    followups = [posting for posting in postings if posting.proposal.points_terms.prior_refund_id]
    if len(followups) != 1:
        raise PointsFundingPlanError('后续退款顺序待核对')
    try:
        from .points_followup_termination import verify_followup_termination_locked
        from .points_followup_revision import verify_followup_revision_locked
        posting = verify_followup_termination_locked(followups[0])
        revision = posting.followup_funding_revision
        verified = verify_followup_revision_locked(revision)
        from .points_followup_addon_version import later_runs
        later = later_runs(order, revision)
        allocations = verified_order_allocations_locked(order, _main_refund_usage=followup_eligible(order))
        original_ids = {item[0] for item in revision.allocations}
        extra = sum(allocation.amount for allocation in allocations if allocation.pk not in original_ids)
        from .addon_funding_release import addon_release_totals
        addon_released = addon_release_totals(allocations)
        net = 0
        for allocation in allocations:
            released = sum(OrderPointsFundingRelease.objects.filter(allocation=allocation)
                           .values_list('amount', flat=True)) + addon_released[allocation.pk]
            if released > allocation.amount:
                raise ValidationError('后续退款补资释放超过原分配')
            net += allocation.amount - released
        if later:
            required = later[-1].points_followup_snapshot['funding']['required_units']
            if net > required:
                raise ValidationError('后续加购后的多余补资尚未释放')
        else:
            required = verified.required_units
            if net != verified.allocated_units - verified.release_units + extra or net > required:
                raise ValidationError('后续退款补资余额与实际流水不一致')
        remaining = required - net
        return VerifiedOrderFunding(required, net, remaining, remaining == 0)
    except (ValidationError, SettlementError, AttributeError) as exc:
        raise PointsFundingPlanError('后续退款补资凭证待核对') from exc
