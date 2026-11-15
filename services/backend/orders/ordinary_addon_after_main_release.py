"""Release surplus funding after an add-on refund following a main refund."""
from dataclasses import asdict
import hashlib
import json

from django.conf import settings
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import OrderAddonFundingRelease, OrderAddonFundingReleaseRun
from .points_checkout_test import require_isolated_checkout
from .points_funding_allocation import PointsFundingAllocationError, plan_release
from .points_funding_plan import PointsFundingPlanError


def _facts(run, allocated):
    refund = run.refund
    if not refund or refund.created_at >= run.created_at:
        raise ValidationError('加购退款和补资释放时序待核对')
    from .ordinary_refunded_addon_funding import plan_locked
    plan = plan_locked(refund.order_id, _allocated_at_boundary=allocated, _boundary=run.created_at,
                       _allow_settled=True, _allow_finalized=True, _allow_unconfirmed_addons=True)
    if not any(row['payment_id'] == refund.payment_id and refund.pk in row['refund_ids']
               for row in plan.addon_history):
        raise ValidationError('加购退款不在整单资金依据中')
    facts = {'kind': 'ordinary_addon_after_main_refund_v1', 'refund_id': refund.pk,
             'combined': asdict(plan.combined), 'addon_history': list(plan.addon_history)}
    evidence = getattr(refund, 'zero_wallet_evidence', None)
    if evidence:
        facts['zero_wallet'] = [evidence.pk, evidence.wallet_id, evidence.account_id, evidence.business_cycle,
                                evidence.balance, evidence.frozen_amount, evidence.is_active,
                                evidence.created_at.isoformat()]
    participant_evidence = list(refund.participant_zero_wallet_evidence.order_by('participant_id'))
    if participant_evidence:
        facts['participant_zero_wallets'] = [[row.pk, row.participant_id, row.wallet_id, row.account_id,
            row.business_cycle, row.balance, row.frozen_amount, row.is_active, row.created_at.isoformat()]
            for row in participant_evidence]
    return facts


def _digest(run, facts):
    payload = [run.refund_id, run.created_at.isoformat(), run.allocation_snapshot,
               run.required_amount, run.allocated_before, run.released_amount, facts]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True).encode()).hexdigest()


def verify_run(run):
    from .ordinary_addon_production import eligible
    from .ordinary_followup_capability import permits_funding
    from .negotiation_zero import ordinary_funding_enabled
    if not (run.refund_id and (eligible(run.refund.order, allow_open=True)
                               or permits_funding(run.refund.order) or ordinary_funding_enabled(run.refund.order))):
        require_isolated_checkout()
    if not transaction.get_connection().in_atomic_block or not run.refund_id:
        raise ValidationError('加购退款补资核验须在门店事务内执行')
    try:
        facts = _facts(run, run.allocated_before)
    except PointsFundingPlanError as exc:
        raise ValidationError('加购退款后的资金凭证待核对') from exc
    if (run.ordinary_refund_snapshot != facts or run.ordinary_refund_hash != _digest(run, facts)
            or facts['combined']['required_units'] != run.required_amount):
        raise ValidationError('加购退款补资快照异常')
    return run


def record_locked(order, refund):
    from .ordinary_addon_production import eligible
    from .negotiation_zero import ordinary_funding_enabled
    zero = ordinary_funding_enabled(order)
    production = eligible(order, allow_open=True) or zero
    if not production:
        require_isolated_checkout()
    if (not transaction.get_connection().in_atomic_block or refund.order_id != order.pk
            or not (production or getattr(settings, 'ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING', False))):
        raise ValidationError('加购退款后的补资调整尚未开放')
    if zero:
        from .ordinary_zero_post_addon import continuing_account_locked
        from .addon_handover import current_responsibilities
        account_id = continuing_account_locked(order, allow_open_case=True)
        _, responsibilities = current_responsibilities(refund.payment, lock=True, before=refund.created_at,
                                                        exclude_refund_id=refund.pk)
        if (not any(row['provider_id'] == account_id and row['basis'] > 0 for row in responsibilities)
                or set(refund.proposal.participants.values_list('account_id', flat=True)) != {account_id}):
            raise ValidationError('零退款后加购退款责任范围待核对')
    from .points_funding_evidence import verified_order_allocations_locked
    from .addon_funding_release import addon_release_totals, verify_run as verify_release
    from .ordinary_refunded_addon_funding import plan_locked
    existing = OrderAddonFundingReleaseRun.objects.filter(refund=refund).first()
    if existing:
        # The event is immutable. A repeated execution must reconcile the
        # original release rather than create another version or credit.
        return verify_release(existing)
    if production:
        from .points_funding_evidence import _verified_allocations_for_locked_order
        allocations = _verified_allocations_for_locked_order(order)
    else:
        allocations = verified_order_allocations_locked(order)
    previous = addon_release_totals(allocations)
    snapshot = [[row.pk, row.amount, previous[row.pk], []] for row in allocations]
    normalized = [[row[0], row[1], row[2]] for row in snapshot]
    before = sum(row[1] - row[2] for row in normalized)
    try:
        from .ordinary_refunded_addon_paid_revision import posting_refund_active
        plan = plan_locked(order.pk, _allow_unconfirmed_addons=True,
                           _allow_settled=posting_refund_active(refund))
        if plan.allocated != before:
            raise ValidationError('加购退款补资分配已变化')
        parts = plan_release(allocations=normalized, amount=plan.releasable)
    except (PointsFundingPlanError, PointsFundingAllocationError) as exc:
        raise ValidationError('加购退款后的补资计算待核对') from exc
    # Preserve the actual funding version even when this event releases no
    # money.  A later refund must be able to distinguish it from no event.
    run = OrderAddonFundingReleaseRun.objects.create(refund=refund, required_amount=plan.required,
        allocated_before=before, released_amount=plan.releasable, allocation_snapshot=snapshot)
    try:
        facts = _facts(run, before)
    except PointsFundingPlanError as exc:
        raise ValidationError('加购退款后的补资依据待核对') from exc
    if facts['combined']['required_units'] != plan.required:
        raise ValidationError('加购退款后的补资计算已变化')
    run.ordinary_refund_snapshot = facts
    run.ordinary_refund_hash = _digest(run, facts)
    run.save(update_fields=['ordinary_refund_snapshot', 'ordinary_refund_hash'])
    OrderAddonFundingRelease.objects.bulk_create([
        OrderAddonFundingRelease(run=run, allocation_id=part.allocation_id, amount=part.amount) for part in parts])
    verified = verify_release(run)
    if zero and not allocations:
        from .ordinary_after_main_addon_receipt import verified_release as verify_empty
        verify_empty(run, None)
    if production and getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is True:
        from users.receipt_usage_sources import record_first_addon_release_locked, verify_allocations_locked
        from .ordinary_after_main_addon_receipt import verified_release as verify_local
        for release in run.releases.select_related('allocation', 'run__refund').order_by('allocation__receipt_id', 'pk'):
            record_first_addon_release_locked(release)
        for receipt_id in sorted({row.receipt_id for row in allocations}):
            verify_local(run, receipt_id)
            verify_allocations_locked(receipt_id)
    return verified
