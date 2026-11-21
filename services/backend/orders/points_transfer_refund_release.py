"""Isolated first-refund releases after previously funded service transfers.

Callers must hold the order and all relevant receipt locks. This does not
close the aftersale or release provider earnings.
"""
from dataclasses import asdict

from django.conf import settings
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import OrderPointsFundingAllocation, OrderPointsFundingRelease, OrderPointsFundingReleaseEvidence
from .points_checkout_test import require_isolated_checkout
from .points_funding_allocation import PointsFundingAllocationError, plan_release
from .points_funding_release import _digest
from .points_negotiation_preview import funding_after_refund
from .points_transfer_refund_evidence import reconstruct_funding_locked, verify_transfer_revisions_after_refund_locked


def _guard():
    from .points_transfer_refund_access import permits, require_refund_or_isolated
    require_refund_or_isolated()
    if (not transaction.get_connection().in_atomic_block
            or (not permits(phase='refund') and (not getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_RELEASE_TESTING', False)
                or not getattr(settings, 'SERVICE_POINTS_FUNDING_RELEASE_TESTING', False)))):
        raise ValidationError('转单退款补资释放尚未开放')


def _compute(posting, items):
    order, proof = posting.proposal.case.order, posting.points_refund
    evidence = posting.participants
    facts = evidence['facts']
    latest = facts['transfers'][-1]['id']
    original = reconstruct_funding_locked(order.pk, evidence, refund_id=proof.refund_id,
                                           through_transfer_id=latest).funding
    return _compute_from_original(posting, items, original)


def _compute_from_original(posting, items, original):
    """Calculate from previously verified transfer funding facts."""
    order, proof = posting.proposal.case.order, posting.points_refund
    shares = list(posting.shares.order_by('account_id'))
    rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
    return original, funding_after_refund(principal_units=original.principal_units, gift_units=original.gift_units,
        compensation_units=original.compensation_units, returned_prepaid_units=proof.prepaid_units,
        returned_gift_units=proof.gift_units, returned_compensation_units=proof.compensation_units,
        provider_income_units=original.provider_income_units,
        pending_deduction_units=sum(item.pending_deduction for item in shares),
        provider_external_units=sum(item.wallet_deduction + item.deposit_deduction for item in shares),
        inviter_rate=rate, allocated_units=sum(item[1] - item[3] for item in items),
        rounding_reserved_units=posting.rounding_to_store_units)


def _add_addon_history(posting, main):
    """Rebuild paid add-ons at the confirmed refund boundary."""
    from collections import defaultdict
    from .models import OrderAddonPayment
    order = posting.proposal.case.order
    if OrderAddonPayment.objects.filter(intent__order=order,
            buyer_transaction__created_at=posting.created_at).exists():
        raise ValidationError('加购付款与主单退款时序无法区分')
    if not OrderAddonPayment.objects.filter(intent__order=order,
            buyer_transaction__created_at__lt=posting.created_at).exists():
        return main, None
    if OrderAddonPayment.objects.filter(intent__order=order,
            buyer_transaction__created_at__lt=posting.created_at).count() > 1:
        # Verify every earlier refund even when no receipt was allocated (or
        # the net release is zero), before using its earnings and cash facts.
        from .models import OrderAddonRefund, OrderPointsFundingAllocation
        from .points_addon_receipt_usage import facts as addon_refund_facts
        witness = OrderPointsFundingAllocation.objects.filter(redemption__order=order).order_by('pk')\
            .values_list('receipt_id', flat=True).first()
        refunds = list(OrderAddonRefund.objects.filter(order=order, created_at__lt=posting.created_at)
                       .select_related('payment', 'proposal', 'order').order_by('created_at', 'pk'))
        if len({item.created_at for item in refunds}) != len(refunds):
            raise ValidationError('此前加购退款事件顺序无法区分')
        try:
            for item in refunds:
                addon_refund_facts(item, read_receipt_id=witness, _event_history=False)
        except Exception as exc:
            from django.core.exceptions import ValidationError as DjangoValidationError
            if isinstance(exc, (DjangoValidationError, ValidationError)):
                raise ValidationError('此前加购退款资金凭证待核对') from exc
            raise
    from .addon_funding_history import combined_at
    from .refunded_addon_funding import combine_refunded_history
    from .points_funding_plan import PointsFundingPlanError
    facts = posting.participants['facts']
    history = reconstruct_funding_locked(order.pk, posting.participants,
        refund_id=posting.points_refund.refund_id, through_transfer_id=facts['transfers'][-1]['id'])
    retained = defaultdict(int)
    segment_income = {line[0]: line[4] for line in history.segment_shares}
    segments = {item['id']: item for item in facts['segments']}
    selected = set(posting.proposal.affected_segment_ids or [])
    for line in history.segment_shares:
        retained[line[1]] += line[4]
    for share in posting.shares.all():
        retained[share.account_id] -= share.pending_deduction
        chosen = [line[0] for line in history.segment_shares if line[1] == share.account_id
                  and segments[line[0]]['started_at'] and (not selected or line[0] in selected)]
        if share.pending_deduction and len(chosen) != 1:
            raise ValidationError('主单退款扣减须明确唯一履约段')
        if share.pending_deduction:
            segment_income[chosen[0]] -= share.pending_deduction
    if any(amount < 0 for amount in retained.values()):
        raise ValidationError('退款后的逐人收益异常')
    try:
        snapshot = combined_at(order, main, posting.created_at, segment_shares=[
            (line[0], line[1], line[2], line[3], segment_income[line[0]]) for line in history.segment_shares])
        return combine_refunded_history(main, snapshot, main_retained_by_account=retained), snapshot['addon_history']
    except PointsFundingPlanError as exc:
        raise ValidationError('转单退款加购历史资金异常') from exc


def _usage_scope_locked(evidence, *, verify_ledger):
    from .points_transfer_refund_access import permits
    if not (permits(evidence.posting.proposal.case.order, phase='refund')
            or getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_RECEIPT_USAGE_TESTING', False)):
        return
    from types import SimpleNamespace
    from users.models import PassRefundRepaymentReceipt
    from users.receipt_usage_sources import _require_current_unlocked_reads
    from django.core.exceptions import ValidationError as DjangoValidationError
    items = evidence.allocations
    if not isinstance(items, list) or any(not isinstance(i, list) or len(i) != 4 for i in items):
        raise ValidationError('转单退款到账快照异常')
    ids, receipt_ids = {i[0] for i in items}, {i[2] for i in items}
    receipts = list(PassRefundRepaymentReceipt.objects.select_for_update().filter(pk__in=receipt_ids).order_by('pk'))
    if len(receipts) != len(receipt_ids):
        raise ValidationError('转单退款到账凭证缺失')
    ledger_receipts = [receipt for receipt in receipts if receipt.usage_version is not None]
    if not ledger_receipts:
        return
    allocation = OrderPointsFundingAllocation.objects.filter(pk__in=ids, receipt=ledger_receipts[0]).first()
    if not allocation:
        raise ValidationError('转单退款原分配不存在')
    try:
        _require_current_unlocked_reads()
        from .points_transfer_refund_receipt_usage import facts
        facts(SimpleNamespace(posting=evidence.posting, allocation=allocation, pk=0, amount=0))
        if verify_ledger:
            from users.receipt_usage_guard import isolated_allocation_state_locked
            for receipt in ledger_receipts:
                isolated_allocation_state_locked(receipt)
    except DjangoValidationError as exc:
        raise ValidationError('转单退款到账用途凭证异常') from exc


def verify_release_locked(evidence, *, _skip_usage=False):
    """Verify the actual refund, historic transfer releases and release rows."""
    _guard()
    from .points_transfer_refund_access import permits
    if permits(phase='refund') or getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_RECEIPT_USAGE_TESTING', False):
        from .models import Order
        from .financial_lock import lock_order_money
        from .points_receipt_usage import prelock_transfer_refund_receipts
        order_id = OrderPointsFundingReleaseEvidence.objects.filter(pk=evidence.pk).values_list(
            'posting__proposal__case__order_id', flat=True).first()
        order = Order.objects.select_for_update().get(pk=order_id)
        from .points_transfer_refund_access import permits
        if permits(phase='refund') and not permits(order, phase='refund'):
            raise ValidationError('转单释放上下文不属于当前订单')
        lock_order_money()
        prelock_transfer_refund_receipts(order)
    from .points_negotiation_execution import verify_posting_locked
    evidence = OrderPointsFundingReleaseEvidence.objects.select_for_update().select_related(
        'posting__proposal__case__order', 'posting__points_refund__redemption').get(pk=evidence.pk)
    posting = verify_posting_locked(evidence.posting)
    order, refund = posting.proposal.case.order, posting.points_refund.refund
    prior = verify_transfer_revisions_after_refund_locked(order.pk, posting.participants, refund_id=refund.pk)
    actual = _verify_release_from_verified_facts(evidence, posting, prior, None)
    if not _skip_usage:
        _usage_scope_locked(evidence, verify_ledger=True)
    return actual


def _verify_release_from_verified_facts(evidence, posting, prior, original):
    """Validate release rows after the caller verifies posting and history."""
    order, refund = posting.proposal.case.order, posting.points_refund.refund
    terms = getattr(posting.proposal, 'points_terms', None)
    if (not isinstance(posting.participants, dict) or not terms or terms.prior_refund_id
            or evidence.original_settlement_id is not None
            or evidence.created_at < refund.created_at):
        raise ValidationError('转单退款补资释放凭证类型异常')
    items = evidence.allocations
    if (not isinstance(items, list) or any(not isinstance(item, list) or len(item) != 4
            or any(type(value) is not int for value in item)
            or item[0] <= 0 or item[1] <= 0 or item[2] <= 0 or not 0 <= item[3] <= item[1] for item in items)
            or len({item[0] for item in items}) != len(items) or items != sorted(items, key=lambda item: item[0])
            or evidence.evidence_hash != _digest(posting, items, evidence.original_provider_income,
                None, evidence.expected_release, evidence.addon_funding_snapshot,
                evidence.prior_addon_release_snapshot)):
        raise ValidationError('转单退款补资释放快照异常')
    owner = Q(redemption__order=order) | Q(service_order=order)
    allocations = list(OrderPointsFundingAllocation.objects.select_for_update().filter(
        owner, created_at__lte=refund.created_at).order_by('pk'))
    from .points_funding_release import _prior_addon_releases
    prior_addon = _prior_addon_releases(allocations, posting.created_at)
    if prior_addon != evidence.prior_addon_release_snapshot:
        raise ValidationError('转单退款前加购释放凭证异常')
    prior = dict(prior)
    for _, allocation_id, _, amount in prior_addon:
        prior[allocation_id] = prior.get(allocation_id, 0) + amount
    if [[row.pk, row.amount, row.receipt_id, prior.get(row.pk, 0)] for row in allocations] != items:
        raise ValidationError('转单退款补资原分配或历史释放异常')
    if original is None:
        original, _ = _compute(posting, items)
    _, actual = _compute_from_original(posting, items, original)
    actual, addon_snapshot = _add_addon_history(posting, actual)
    if addon_snapshot != evidence.addon_funding_snapshot:
        raise ValidationError('转单退款加购资金快照异常')
    try:
        expected = plan_release(allocations=[(item[0], item[1], item[3]) for item in items], amount=actual.release_units)
    except PointsFundingAllocationError as exc:
        raise ValidationError('转单退款补资释放金额异常') from exc
    rows = list(posting.funding_releases.order_by('-allocation_id'))
    if (original.provider_income_units != evidence.original_provider_income
            or actual.release_units != evidence.expected_release
            or [(row.allocation_id, row.amount) for row in rows] != [(part.allocation_id, part.amount) for part in expected]
            or any(row.created_at < evidence.created_at for row in rows)):
        raise ValidationError('转单退款补资释放与实际承担不一致')
    return actual


def _verify_release_facts_locked(evidence):
    """Check the first release without authorizing a later settlement state."""
    _guard()
    from .points_transfer_refund_access import permits
    if not (permits(phase='refund') or permits(phase='settlement')
            or getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_RELEASE_FACTS_TESTING', False)):
        raise ValidationError('转单追加退款的历史补资核对尚未开放')
    from .points_negotiation_execution import _verify_transfer_posting_facts_locked
    from .points_transfer_refund_evidence import _verify_revisions_from_verified_facts
    evidence = OrderPointsFundingReleaseEvidence.objects.select_for_update().select_related(
        'posting__proposal__case__order', 'posting__points_refund__redemption').get(pk=evidence.pk)
    posting, facts, history = _verify_transfer_posting_facts_locked(evidence.posting)
    prior = _verify_revisions_from_verified_facts(posting.proposal.case.order_id, facts,
                                                   refund_id=posting.points_refund.refund_id)
    return _verify_release_from_verified_facts(evidence, posting, prior, history.funding)


def record_release_locked(posting, calculation):
    """Persist first-refund releases after actual wallet/deposit deductions."""
    _guard()
    from .points_negotiation_execution import verify_posting_locked
    posting = verify_posting_locked(posting)
    existing = OrderPointsFundingReleaseEvidence.objects.filter(posting=posting).first()
    if existing:
        return verify_release_locked(existing)
    order = posting.proposal.case.order
    owner = Q(redemption__order=order) | Q(service_order=order)
    allocations = list(OrderPointsFundingAllocation.objects.select_for_update().filter(owner).order_by('pk'))
    if OrderPointsFundingRelease.objects.filter(allocation__in=allocations).exists():
        raise ValidationError('订单已有退款补资释放记录，须先核对')
    prior = verify_transfer_revisions_after_refund_locked(order.pk, posting.participants,
                                                          refund_id=posting.points_refund.refund_id)
    from .points_funding_release import _prior_addon_releases
    prior_addon = _prior_addon_releases(allocations, posting.created_at)
    prior = dict(prior)
    for _, allocation_id, _, amount in prior_addon:
        prior[allocation_id] = prior.get(allocation_id, 0) + amount
    items = [[row.pk, row.amount, row.receipt_id, prior.get(row.pk, 0)] for row in allocations]
    original, actual = _compute(posting, items)
    actual, addon_snapshot = _add_addon_history(posting, actual)
    if asdict(actual) != calculation['conditional_funding_if_recovered']:
        raise ValidationError('实际退款承担与补资测算不一致')
    try:
        parts = plan_release(allocations=[(row[0], row[1], row[3]) for row in items], amount=actual.release_units)
    except PointsFundingAllocationError as exc:
        raise ValidationError('转单退款补资释放金额异常') from exc
    evidence = OrderPointsFundingReleaseEvidence.objects.create(posting=posting, allocations=items,
        original_provider_income=original.provider_income_units, original_settlement_id=None,
        expected_release=actual.release_units, addon_funding_snapshot=addon_snapshot,
        prior_addon_release_snapshot=prior_addon,
        evidence_hash=_digest(posting, items, original.provider_income_units, None, actual.release_units,
                              addon_snapshot, prior_addon))
    OrderPointsFundingRelease.objects.bulk_create([OrderPointsFundingRelease(posting=posting,
        allocation_id=part.allocation_id, amount=part.amount) for part in parts])
    # Checking the original allocations reads the whole receipt ledger, which
    # must already include events for the release rows written in this transaction.
    from .points_transfer_refund_access import permits
    if permits(order, phase='refund') or getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_RECEIPT_USAGE_TESTING', False):
        from users.receipt_usage_sources import record_transfer_refund_release_locked as record_usage, verify_allocations_locked
        touched = set()
        rows = posting.funding_releases.select_related('allocation__receipt').order_by('allocation__receipt_id', 'pk')
        for row in rows:
            if row.allocation.receipt.usage_version is not None:
                record_usage(row)
                touched.add(row.allocation.receipt_id)
        for receipt_id in sorted(touched):
            verify_allocations_locked(receipt_id)
    verified = verify_release_locked(evidence, _skip_usage=True)
    _usage_scope_locked(evidence, verify_ledger=False)
    return verified
