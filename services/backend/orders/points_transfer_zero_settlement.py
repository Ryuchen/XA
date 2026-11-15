"""Independent settlement history for a transferred zero-refund termination."""
from copy import deepcopy

from rest_framework.exceptions import ValidationError
from stores.db import transaction


def verify_revision(revision, *, before_hash=None):
    """Check the evidence without taking an order lock (receipt readers use it)."""
    from .points_zero_followup import digest
    from .segment_settlement import _hash
    termination, manifest = revision.termination, revision.manifest
    proposal, saved = termination.proposal, revision.paid_snapshot
    before, after = revision.before_plan, revision.after_plan
    if (not isinstance(before, dict) or not isinstance(after, dict) or not isinstance(saved, dict)
            or not isinstance(termination.funding_snapshot, dict)
            or termination.funding_snapshot.get('kind') != 'TRANSFER_ZERO_V1'
            or termination.funding_snapshot.get('paid_snapshot') != saved
            or saved.get('manifest_id') != manifest.pk or saved.get('plan') != before
            or saved.get('plan_hash') != revision.before_plan_hash
            or revision.before_plan_hash != _hash(before) or revision.after_plan_hash != _hash(after)
            or (before_hash is not None and before_hash != revision.before_plan_hash)
            or manifest.order_id != termination.order_id or proposal.case.order_id != termination.order_id
            or proposal.refund_units != 0 or proposal.status != 'ACCEPTED'
            or proposal.accepted_at != termination.accepted_at or not termination.terminated_at
            or termination.created_at > termination.terminated_at or revision.created_at < termination.terminated_at
            or termination.evidence_hash != digest(proposal, termination.prior_posting, termination.assignment,
                termination.segment, termination.operator, termination.request_id, termination.funding_snapshot)):
        raise ValidationError('零退款结算版本凭证不一致')
    expected = deepcopy(before)
    expected['completed_at'] = termination.accepted_at.isoformat()
    if before.get('completed_at') is not None or after != expected:
        raise ValidationError('零退款终止改变了原结算份额或资金')
    people = saved.get('people')
    if not isinstance(people, list) or not people or any(
            item.get('account_id') == termination.assignment.provider_account_id for item in people):
        raise ValidationError('零退款结算版本的已付人员范围异常')
    from .points_transfer_paid_addons import unchanged
    unchanged(saved, after.get('points_funding', {}).get('addons', []))
    return revision.after_plan_hash


def transition_locked(order, manifest, plan, terms):
    """Advance only the completion clock; never alter or pay a credited share."""
    from .models import PointsTransferZeroSettlementRevision, PointsZeroFollowupTermination
    from .points_transfer_zero_followup import verify_record_locked, _paid_facts_locked
    from .segment_settlement import _hash, _validate_records
    from .settlement import SettlementError
    if not transaction.get_connection().in_atomic_block or manifest.finalized_at or order.settled_at:
        raise SettlementError('零退款结算版本须在未完结的订单事务中处理')
    zero = PointsZeroFollowupTermination.objects.select_for_update().filter(order=order).first()
    if not zero:
        raise SettlementError('零退款终止凭证缺失')
    verify_record_locked(zero)
    saved = zero.funding_snapshot.get('paid_snapshot')
    if not isinstance(saved, dict) or manifest.plan != saved.get('plan') or manifest.plan_hash != saved.get('plan_hash'):
        raise SettlementError('零退款之前的已付结算计划已变化')
    current = _paid_facts_locked(order, plan, affected_account_id=zero.assignment.provider_account_id)
    if current != saved:
        raise SettlementError('零退款之前的已付流水已变化')
    expected = deepcopy(manifest.plan)
    expected['completed_at'] = zero.accepted_at.isoformat()
    if terms != expected:
        raise SettlementError('零退款终止后的收益或资金方案发生变化')
    with transaction.atomic():
        revision = PointsTransferZeroSettlementRevision.objects.create(termination=zero, manifest=manifest,
            before_plan=manifest.plan, before_plan_hash=manifest.plan_hash,
            after_plan=terms, after_plan_hash=_hash(terms), paid_snapshot=saved)
        verify_revision(revision, before_hash=manifest.plan_hash)
        manifest.plan, manifest.plan_hash = terms, revision.after_plan_hash
        manifest.save(update_fields=['plan', 'plan_hash'])
        return _validate_records(order, manifest, plan)
