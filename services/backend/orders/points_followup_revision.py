"""Durable, isolated evidence for the second refund's funding revision.

This records actual refund/debit facts and a reservation plan. It does not
release a receipt, close an aftersale, or authorize the remaining payout.
"""
import hashlib
import json

from django.conf import settings
from rest_framework.exceptions import ValidationError

from engagement.models import OrderFundingAllocation
from stores.db import transaction
from .models import (OrderPointsFollowupFundingRevision, OrderPointsFundingAllocation, OrderPointsFundingRelease,
                     OrderPointsRefundSettlement, OrderPointsRefundSettlementPerson,
                     OrderProvider, OrderSegmentFinancialSnapshot, PointsNegotiationPosting)
from .points_checkout_test import require_isolated_checkout
from .points_funding_allocation import PointsFundingAllocationError
from .points_funding_evidence import verified_order_allocations_locked
from .points_funding_plan import PointsFundingPlanError
from .points_funding_revision import plan_funding_revision, plan_unpaid_funding_revision
from .points_negotiation_preview import funding_after_refund


FIELDS = ('net_paid_units', 'principal_units', 'gift_units', 'compensation_units', 'recovered_units',
          'provider_units', 'paid_provider_units', 'remaining_provider_units', 'inviter_units', 'store_units',
          'prior_required_units', 'allocated_units', 'required_units', 'release_units', 'shortfall_units')


def _digest(posting, prior_settlement_id, paid_person_id, allocations, releases, values, addon_snapshot=None,
            *, sort_keys=True):
    payload = [posting.pk, posting.terms_hash, posting.points_refund_id, prior_settlement_id,
               paid_person_id, allocations, releases, values]
    if addon_snapshot is not None:
        payload.append(addon_snapshot)
    return hashlib.sha256(json.dumps(payload, separators=(',', ':'), ensure_ascii=True,
                                     sort_keys=sort_keys).encode()).hexdigest()


def revision_hash_matches(row, posting, values, rebuilt_addons):
    """Check the persisted snapshot against the rebuilt facts, including older hashes."""
    facts = (posting, row.prior_settlement_id, row.paid_person_id, row.allocations, row.releases, values)
    rebuilt_hash = _digest(*facts, rebuilt_addons)
    if row.addon_snapshot is not None or rebuilt_addons is not None:
        if _digest(*facts, row.addon_snapshot) != rebuilt_hash:
            return False
    return (row.evidence_hash == rebuilt_hash
            or (rebuilt_addons is not None and row.evidence_hash in (
                _digest(*facts, row.addon_snapshot, sort_keys=False),
                _digest(*facts, rebuilt_addons, sort_keys=False))))


def _facts(projected, revision, prior_required):
    return (projected.net_coin_units, projected.net_principal_units, projected.net_gift_units,
            projected.net_compensation_units, projected.planned_provider_external_units,
            projected.provider_income_units, revision.paid_provider_units, revision.remaining_provider_units,
            projected.inviter_income_units, projected.store_income_units, prior_required,
            revision.allocated_units, revision.required_units, revision.release_units, revision.shortfall_units)


def _with_addons(order, main, snapshots, shares, boundary):
    """Rebuild add-on facts at the second refund's persisted boundary."""
    from .addon_funding_history import combined_at
    from .refunded_addon_funding import combine_refunded_history
    pending = {share.account_id: share.pending_deduction for share in shares}
    retained = {row.segment.provider_account_id: row.income_at_entry - pending.get(row.segment.provider_account_id, 0)
                for row in snapshots}
    entries = [(row.segment_id, row.segment.provider_account_id, row.incoming_basis, 0,
                retained[row.segment.provider_account_id]) for row in snapshots]
    try:
        history = combined_at(order, main, boundary, segment_shares=entries)
        combined = combine_refunded_history(main, history, main_retained_by_account=retained)
    except PointsFundingPlanError as exc:
        raise ValidationError('后续退款的加购资金凭证待核对') from exc
    return combined, {'addon_history': history['addon_history'], 'main_deductions': history['main_deductions']}


def verify_followup_revision_locked(row):
    from .points_business_access import permits
    formal = any(permits(phase=phase, kind=kind) for kind in ('service', 'addon')
                 for phase in ('funding', 'refund', 'report', 'settlement'))
    if not formal:
        require_isolated_checkout()
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('后续退款资金版本须在门店事务内核对')
    if formal or getattr(settings, 'SERVICE_POINTS_FOLLOWUP_RECEIPT_USAGE_TESTING', False):
        from .models import Order
        from .financial_lock import lock_order_money
        from .points_receipt_usage import prelock_followup_receipts
        order_id = OrderPointsFollowupFundingRevision.objects.filter(pk=row.pk).values_list(
            'posting__proposal__case__order_id', flat=True).first()
        order = Order.objects.select_for_update().get(pk=order_id)
        if formal and not any(permits(order, phase=phase, kind=kind) for kind in ('service', 'addon')
                              for phase in ('funding', 'refund', 'report', 'settlement')):
            raise ValidationError('后续退款资金版本不属于当前订单')
        lock_order_money()
        prelock_followup_receipts(order)
    row = OrderPointsFollowupFundingRevision.objects.select_for_update().select_related(
        'posting__proposal__case__order', 'posting__points_refund__redemption', 'prior_settlement__posting',
        'paid_person__segment', 'paid_person__report', 'paid_person__income_transaction__wallet').get(pk=row.pk)
    from .points_followup_context import checking
    with checking(row.posting_id):
        return _verify_followup_revision_locked(row)


def _verify_followup_revision_locked(row):
    from .points_negotiation_execution import verify_posting_locked
    from .points_negotiation_termination import verify_termination_locked
    second = verify_posting_locked(row.posting)
    terms = second.proposal.points_terms
    first = PointsNegotiationPosting.objects.select_for_update().filter(points_refund_id=terms.prior_refund_id).first()
    if not first:
        raise ValidationError('后续退款的原结案凭证缺失')
    first = verify_termination_locked(first, allow_verified_later_final=True)
    order, proof = second.proposal.case.order, second.points_refund
    prior_proof = first.points_refund
    if (terms.prior_refund_id != prior_proof.pk or proof.redemption_id != prior_proof.redemption_id
            or proof.refund_id <= prior_proof.refund_id
            or (row.prior_settlement_id and (row.prior_settlement.posting_id != first.pk
                                             or row.prior_settlement.finalized_at))
            or proof.redemption.refunds.count() != 2):
        raise ValidationError('后续退款与原结算凭证不一致')
    from .points_refunded_settlement import _check_person
    from .points_service_participation import service_snapshots
    snapshots = service_snapshots(order)
    assignments = {item.provider_account_id: item for item in OrderProvider.objects.select_for_update().filter(
        order=order, provider_account_id__in=[snap.segment.provider_account_id for snap in snapshots])}
    if len(snapshots) != 2 or len(assignments) != 2:
        raise ValidationError('后续退款双陪履约凭证不完整')
    first_shares = list(first.shares.all())
    second_shares = list(second.shares.all())
    if (len(first_shares) != 1 or len(second_shares) != 1
            or second_shares[0].account_id == first_shares[0].account_id
            or second_shares[0].account_id not in assignments):
        raise ValidationError('后续退款受影响陪玩结算状态异常')
    if row.paid_person_id:
        if (not row.prior_settlement_id or row.paid_person.settlement_id != row.prior_settlement_id
                or row.prior_settlement.persons.count() != 1):
            raise ValidationError('后续退款原陪玩结算凭证不一致')
        paid_snapshot = next((item for item in snapshots if item.segment_id == row.paid_person.segment_id), None)
        if (not paid_snapshot or paid_snapshot.segment.provider_account_id != first_shares[0].account_id
                or paid_snapshot.segment.provider_account_id not in assignments):
            raise ValidationError('原陪玩收益归属待核对')
        _check_person(order, first, row.paid_person, paid_snapshot,
                      assignments[paid_snapshot.segment.provider_account_id])
        if assignments[second_shares[0].account_id].settled_at:
            from .points_followup_settlement import verify_followup_person_locked
            verify_followup_person_locked(order, row, second_shares[0], assignments[second_shares[0].account_id])
    else:
        existing_settlement = OrderPointsRefundSettlement.objects.select_for_update().filter(posting=first).first()
        if ((existing_settlement.pk if existing_settlement else None) != row.prior_settlement_id
                or (row.prior_settlement_id and row.prior_settlement.persons.exists())):
            raise ValidationError('原退款的未结算凭证异常')
        from .models import OrderAddonFundingReleaseRun
        later_version = OrderAddonFundingReleaseRun.objects.filter(
            points_followup_snapshot__followup_revision_id=row.pk).order_by('-created_at', '-pk').first()
        if later_version:
            if (OrderPointsRefundSettlementPerson.objects.filter(settlement__posting=second,
                    settled_at__lte=later_version.created_at).exists()
                    or any(item.settled_at and item.settled_at <= later_version.created_at
                           for item in assignments.values())):
                raise ValidationError('后续加购前已有陪玩结算，须核对已付收益')
        else:
            from .points_unpaid_followup_settlement import verify_progress_locked
            verify_progress_locked(order, row, first, second, snapshots, assignments)
    if (not isinstance(row.allocations, list) or any(not isinstance(item, list) or len(item) != 4
            or any(type(value) is not int for value in item) for item in row.allocations)):
        raise ValidationError('后续退款原补资快照无效')
    original_ids = [item[0] for item in row.allocations]
    if len(set(original_ids)) != len(original_ids):
        raise ValidationError('后续退款原补资快照重复')
    from django.db.models import Q
    from .models import OrderAddonFundingReleaseRun
    team = OrderAddonFundingReleaseRun.objects.filter(handover__payment__intent__order=order,
        points_handover_snapshot__kind='SAME_TEAM_V1').exists()
    owner = (Q(redemption__order=order) | Q(service_order=order)) if team else Q(redemption__order=order)
    all_allocations = list(OrderPointsFundingAllocation.objects.select_for_update().filter(owner).order_by('pk'))
    extra = [allocation for allocation in all_allocations if allocation.pk not in original_ids]
    if (any(allocation.created_at < row.created_at or (original_ids and allocation.pk <= max(original_ids))
            for allocation in extra) or OrderPointsFundingRelease.objects.filter(allocation__in=extra).exists()):
        raise ValidationError('后续补资的时间或释放记录异常')
    try:
        from .points_receipt_usage import followup_eligible
        allocations = verified_order_allocations_locked(order, pending_followup_posting_id=second.pk,
                    allocation_ids=original_ids, _main_refund_usage=followup_eligible(order))
    except PointsFundingPlanError as exc:
        raise ValidationError('后续退款补资到账凭证待核对') from exc
    from .points_funding_release import _prior_addon_releases
    prior_addon = {allocation.pk: 0 for allocation in allocations}
    for _, allocation_id, _, amount in _prior_addon_releases(allocations, second.created_at):
        prior_addon[allocation_id] += amount
    allocation_rows = []
    for allocation in allocations:
        releases = list(OrderPointsFundingRelease.objects.filter(allocation=allocation))
        if any(item.posting_id not in (first.pk, second.pk)
               and item.posting.created_at <= second.created_at for item in releases):
            raise ValidationError('后续退款存在未核对的补资释放')
        previous = sum(item.amount for item in releases if item.posting_id == first.pk) + prior_addon[allocation.pk]
        allocation_rows.append((allocation.pk, allocation.receipt_id, allocation.amount, previous))
    if row.allocations != [list(item) for item in allocation_rows]:
        raise ValidationError('后续退款原补资分配快照已变化')
    reservation = (list(row.prior_settlement.funding_uses.order_by('allocation_id').values_list('allocation_id', 'amount'))
                   if row.prior_settlement_id else None)
    paid = {kind: sum(item.amount for item in OrderFundingAllocation.objects.filter(order=order, kind=kind))
            for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
    if sum(paid.values()) != proof.redemption.paid_units:
        raise ValidationError('后续退款原付款来源不一致')
    rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
    common = dict(principal_units=paid['PREPAID'], gift_units=paid['GIFT'], compensation_units=paid['COMPENSATION'],
                  provider_income_units=sum(item.income_at_entry for item in snapshots), inviter_rate=rate,
                  allocated_units=sum(amount - released for _, _, amount, released in allocation_rows))
    first_plan = funding_after_refund(**common, returned_prepaid_units=prior_proof.prepaid_units,
        returned_gift_units=prior_proof.gift_units, returned_compensation_units=prior_proof.compensation_units,
        pending_deduction_units=sum(item.pending_deduction for item in first_shares),
        provider_external_units=sum(item.wallet_deduction + item.deposit_deduction for item in first_shares),
        rounding_reserved_units=first.rounding_to_store_units)
    if row.addon_snapshot is not None:
        first_plan, first_addons = _with_addons(order, first_plan, snapshots, first_shares, second.created_at)
        if first_addons != row.addon_snapshot:
            raise ValidationError('后续退款的加购版本不一致')
        from .points_unpaid_followup_settlement import _addon_data
        from .points_refunded_settlement import _facts as settlement_facts
        addon_totals, addon_parts = _addon_data(order, row, first)
        first_facts = settlement_facts(first_plan, addon_totals)
    else:
        from .models import OrderAddonPayment
        if OrderAddonPayment.objects.filter(intent__order=order,
                buyer_transaction__created_at__lt=second.created_at).exists():
            raise ValidationError('后续退款的加购版本缺失')
        addon_totals, addon_parts = (0, 0, 0, 0, 0, 0), ()
        first_facts = (first_plan.net_coin_units, first_plan.net_principal_units, first_plan.net_gift_units,
                       first_plan.net_compensation_units, first_plan.planned_provider_external_units,
                       first_plan.required_units, first_plan.provider_income_units,
                       first_plan.inviter_income_units, first_plan.store_income_units, *addon_totals)
    settlement = row.prior_settlement
    if first_plan.required_units != row.prior_required_units or (settlement and first_facts != (
            settlement.paid_units, settlement.principal_units, settlement.gift_units,
            settlement.compensation_units, settlement.recovered_units, settlement.funding_units,
            settlement.provider_units, settlement.inviter_units, settlement.store_units,
            settlement.addon_paid_units, settlement.addon_principal_units, settlement.addon_gift_units,
            settlement.addon_compensation_units, settlement.addon_recovered_units, settlement.addon_provider_units)):
        raise ValidationError('原退款结算资金快照不一致')
    actual = funding_after_refund(**common,
        returned_prepaid_units=prior_proof.prepaid_units + proof.prepaid_units,
        returned_gift_units=prior_proof.gift_units + proof.gift_units,
        returned_compensation_units=prior_proof.compensation_units + proof.compensation_units,
        pending_deduction_units=sum(item.pending_deduction for item in first_shares + second_shares),
        provider_external_units=sum(item.wallet_deduction + item.deposit_deduction for item in first_shares + second_shares),
        rounding_reserved_units=first.rounding_to_store_units + second.rounding_to_store_units)
    actual_addons = None
    if row.addon_snapshot is not None:
        actual, actual_addons = _with_addons(order, actual, snapshots, first_shares + second_shares, second.created_at)
        if actual_addons != row.addon_snapshot:
            raise ValidationError('后续退款加购记录已变化')
    try:
        if row.paid_person_id:
            paid_addons = 0
            if addon_parts:
                from .addon_settlement import verify_addon_records
                verified = verify_addon_records(order, row.prior_settlement, addon_parts, [row.paid_person])
                expected = {(part.payment_id, part.segment_id) for part in addon_parts
                            if part.segment_id == row.paid_person.segment_id}
                if verified != expected:
                    raise ValidationError('原陪玩已付加购收益待核对')
                paid_addons = sum(part.provider_income_units for part in addon_parts
                                  if part.segment_id == row.paid_person.segment_id)
            revision = plan_funding_revision(allocations=allocation_rows, original_reservation=reservation,
                previous_required=row.prior_required_units, paid_provider_units=row.paid_person.amount + paid_addons, projected=actual)
        else:
            revision = plan_unpaid_funding_revision(allocations=allocation_rows, original_reservation=reservation,
                previous_required=row.prior_required_units, projected=actual)
    except PointsFundingAllocationError as exc:
        raise ValidationError('后续退款资金版本不守恒') from exc
    values = _facts(actual, revision, row.prior_required_units)
    actual_values = tuple(getattr(row, field) for field in FIELDS)
    release_rows = [list(item) for item in revision.releases]
    if (actual_values != values or row.releases != release_rows
            or not revision_hash_matches(row, second, list(values), actual_addons)):
        raise ValidationError('后续退款资金版本凭证不一致')
    return revision


def record_followup_revision_locked(posting, calculation):
    from .points_business_access import permits
    order = posting.proposal.case.order
    formal = any(permits(order, phase='refund', kind=kind) for kind in ('service', 'addon'))
    if not formal:
        require_isolated_checkout()
    if (not transaction.get_connection().in_atomic_block
            or (not formal and not getattr(settings, 'SERVICE_POINTS_FOLLOWUP_REVISION_TESTING', False))):
        raise ValidationError('后续退款资金版本尚未开放')
    existing = OrderPointsFollowupFundingRevision.objects.select_for_update().filter(posting=posting).first()
    if existing:
        verify_followup_revision_locked(existing)
        return existing
    prior_id = calculation['prior_refund_id']
    prior = PointsNegotiationPosting.objects.get(points_refund_id=prior_id)
    settlement = OrderPointsRefundSettlement.objects.select_for_update().filter(posting=prior).first()
    people = list(OrderPointsRefundSettlementPerson.objects.select_for_update().filter(settlement=settlement)) if settlement else []
    if len(people) > 1 or (not people and not formal and not getattr(settings, 'SERVICE_POINTS_UNPAID_FOLLOWUP_POSTING_TESTING', False)):
        raise ValidationError('后续退款原陪玩结算记录待核对')
    revision, projected = calculation['conditional_funding_revision'], calculation['conditional_funding']
    allocations = [list(item) for item in revision.allocations]
    releases = [list(item) for item in revision.releases]
    values = _facts(projected, revision, calculation['current_required_units'])
    addon_snapshot = calculation.get('addon_snapshot')
    row = OrderPointsFollowupFundingRevision.objects.create(posting=posting, prior_settlement=settlement,
        paid_person=people[0] if people else None, allocations=allocations, releases=releases,
        addon_snapshot=addon_snapshot, **dict(zip(FIELDS, values)),
        evidence_hash=_digest(posting, settlement.pk if settlement else None, people[0].pk if people else None,
                              allocations, releases, list(values), addon_snapshot))
    verify_followup_revision_locked(row)
    return row
