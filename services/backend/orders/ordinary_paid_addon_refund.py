"""Bind an actual add-on refund to earlier, unaffected paid segments."""
import hashlib
import json

from rest_framework.exceptions import ValidationError
from stores.db import transaction


def _digest(refund_id, manifest_id, snapshot):
    data = [refund_id, manifest_id, snapshot]
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def capture_locked(order, case, accounts):
    from .models import OrderSegmentSettlementManifest, OrderSegmentPersonSettlement
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
    if not manifest or not OrderSegmentPersonSettlement.objects.filter(manifest=manifest).exists():
        return None
    from engagement.confirmation import aftersale_deadline
    deadline = aftersale_deadline(order)
    people = list(OrderSegmentPersonSettlement.objects.filter(manifest=manifest).order_by('pk'))
    if (order.status != order.Status.COMPLETED or not order.completed_at or not deadline
            or not case.created_at or not order.completed_at <= case.created_at < deadline
            or any(case.created_at > row.settled_at for row in people)
            or not set(accounts).issubset(set(case.affected_provider_account_ids or []))):
        raise ValidationError('已付人员结算前的有效加购售后事实待核对')
    revised = {pk for revision in manifest.addon_revisions.all() for pk in revision.refund_ids}
    if any(pk not in revised for pk in manifest.ordinary_paid_addon_refunds.values_list('refund_id', flat=True)):
        raise ValidationError('前一笔加购退款的已付清单尚未更新')
    from .segment_settlement import _ordinary_transfer_terms, _terms
    from .ordinary_first_paid_settlement import capture_locked as capture_paid
    from .settlement import SettlementError
    try:
        plan, funding = _ordinary_transfer_terms(order)
        if funding is None or manifest.plan != _terms(order, plan, points_funding=funding):
            raise ValidationError('原整单补资或已付清单待核对')
        return capture_paid(order, plan, accounts)
    except SettlementError as exc:
        raise ValidationError('退款前已结算流水待核对') from exc


def record_locked(refund, snapshot):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('已付加购退款凭证须与退款同事务')
    from .models import OrdinaryPaidAddonRefundEvidence, OrderAddonFundingReleaseRun
    run = OrderAddonFundingReleaseRun.objects.filter(refund=refund).first()
    if not run:
        raise ValidationError('加购退款真实补资版本缺失')
    from .addon_funding_release import verify_run
    verify_run(run)
    data = {'paid': snapshot, 'funding_run_id': run.pk}
    return OrdinaryPaidAddonRefundEvidence.objects.create(refund=refund, manifest_id=snapshot['manifest_id'],
        snapshot=data, snapshot_hash=_digest(refund.pk, snapshot['manifest_id'], data))


def advance_locked(order, evidence):
    """Update the unpaid plan now if the actual whole-order funding permits it."""
    from .segment_settlement import _ordinary_transfer_terms, _terms
    from .segment_addon_settlement_revision import transition_locked
    from .models import OrderSegmentSettlementManifest
    from .settlement import SettlementError
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().get(pk=evidence.manifest_id, order=order)
    try:
        plan, funding = _ordinary_transfer_terms(order)
        if funding is None:
            return False
        terms = _terms(order, plan, points_funding=funding)
        transition_locked(order, manifest, plan, terms)
    except SettlementError as exc:
        raise ValidationError('加购退款后的已付清单修订待核对') from exc
    return True


def verify_locked(evidence):
    snapshot = evidence.snapshot
    if (not isinstance(snapshot, dict) or evidence.snapshot_hash != _digest(
            evidence.refund_id, evidence.manifest_id, snapshot)
            or not isinstance(snapshot.get('paid'), dict)
            or snapshot['paid'].get('manifest_id') != evidence.manifest_id):
        raise ValidationError('已付加购退款凭证异常')
    from .models import OrderAddonFundingReleaseRun
    run = OrderAddonFundingReleaseRun.objects.filter(pk=snapshot.get('funding_run_id'), refund=evidence.refund).first()
    if not run:
        raise ValidationError('已付加购退款补资版本缺失')
    from .addon_funding_release import verify_run
    verify_run(run)
    from .ordinary_first_paid_settlement import verify_saved_snapshot
    accounts = list(evidence.refund.proposal.participants.values_list('account_id', flat=True))
    verify_saved_snapshot(evidence.refund.order, snapshot['paid'], accounts)
    return snapshot['paid']
