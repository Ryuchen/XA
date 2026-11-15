"""Rebalance allocated store funding alongside an ordinary add-on follow-up."""
from django.conf import settings
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import NegotiationFollowupLink, OrderAddonFundingRelease, OrderAddonFundingReleaseRun
from .points_checkout_test import require_isolated_checkout
from .points_funding_allocation import PointsFundingAllocationError, plan_release


def facts_locked(run):
    from .ordinary_addon_followup_posting import verify_posting_locked
    link = NegotiationFollowupLink.objects.select_for_update().filter(execution_id=run.negotiation_id).first()
    if not link:
        raise ValidationError('追加协商补资缺少退款凭证')
    link = verify_posting_locked(link)
    estimate = link.projected_ledger['estimate']
    if run.allocated_before != estimate['allocated_funding']:
        raise ValidationError('追加协商退款前的补资分配已变化')
    return {'followup_link_id': link.pk, 'execution_id': link.execution_id,
            'refund_id': link.execution.refund_id, 'prior_facts_hash': link.projected_ledger['prior_facts_hash'],
            'required_units': estimate['required_funding'], 'allocated_before': estimate['allocated_funding'],
            'release_units': estimate['funding_to_release']}


def record_locked(link):
    """The caller posts the refund and releases excess funds in one transaction."""
    from .ordinary_followup_capability import require_access, stage_enabled
    require_access()
    if (not transaction.get_connection().in_atomic_block
            or not stage_enabled('ORDINARY_ADDON_FOLLOWUP_RELEASE_INTERNAL_TESTING')):
        raise ValidationError('带加购的追加退款补资调整尚未开放')
    from .ordinary_addon_followup_posting import verify_posting_locked
    link = verify_posting_locked(link)
    if not link.execution.refund_id:
        estimate = link.projected_ledger['estimate']
        if estimate['funding_to_release']:
            raise ValidationError('零退款协商的补资余额不一致')
        return None
    existing = OrderAddonFundingReleaseRun.objects.filter(negotiation_id=link.execution_id).first()
    if existing:
        from .addon_funding_release import verify_run
        verified = verify_run(existing)
        from users.receipt_usage_sources import verify_allocations_locked
        receipt_ids = set(existing.releases.filter(allocation__receipt__usage_version__isnull=False)
                          .values_list('allocation__receipt_id', flat=True))
        for receipt_id in sorted(receipt_ids):
            verify_allocations_locked(receipt_id)
        return verified
    order = link.order
    from .points_funding_evidence import verified_order_allocations_locked
    from .addon_funding_release import addon_release_totals, verify_run
    allocations = verified_order_allocations_locked(order)
    if not allocations:
        if link.projected_ledger['estimate']['allocated_funding']:
            raise ValidationError('追加退款补资分配缺失')
        return None
    prior = addon_release_totals(allocations)
    snapshot = [[item.pk, item.amount, prior[item.pk], []] for item in allocations]
    normalized = [[item[0], item[1], item[2]] for item in snapshot]
    before = sum(item[1] - item[2] for item in normalized)
    estimate = link.projected_ledger['estimate']
    if before != estimate['allocated_funding']:
        raise ValidationError('追加退款前的补资分配已变化')
    try:
        parts = plan_release(allocations=normalized, amount=estimate['funding_to_release'])
    except PointsFundingAllocationError as exc:
        raise ValidationError('追加退款补资释放计算异常') from exc
    run = OrderAddonFundingReleaseRun.objects.create(negotiation=link.execution,
        required_amount=estimate['required_funding'], allocated_before=before,
        released_amount=estimate['funding_to_release'], allocation_snapshot=snapshot)
    facts = facts_locked(run)
    from .ordinary_refunded_addon_release import _digest
    run.ordinary_refund_snapshot = facts
    run.ordinary_refund_hash = _digest(run, facts)
    run.save(update_fields=['ordinary_refund_snapshot', 'ordinary_refund_hash'])
    OrderAddonFundingRelease.objects.bulk_create([
        OrderAddonFundingRelease(run=run, allocation_id=part.allocation_id, amount=part.amount) for part in parts])
    verified = verify_run(run)
    from users.receipt_usage_sources import record_first_addon_release_locked, verify_allocations_locked
    touched = set()
    for release in run.releases.select_related('allocation__receipt', 'allocation__service_order',
                                               'run__negotiation__refund').order_by('allocation__receipt_id', 'pk'):
        if release.allocation.receipt.usage_version is not None:
            record_first_addon_release_locked(release)
            touched.add(release.allocation.receipt_id)
    for receipt_id in sorted(touched):
        verify_allocations_locked(receipt_id)
    return verified
