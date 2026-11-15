"""Verify funding needs after a closed second transferred-points refund."""
from django.conf import settings
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import Order, OrderPointsTransferFollowupFundingRevision
from .points_checkout_test import require_isolated_checkout
from .points_funding_plan import PointsFundingPlanError


def plan_locked(order_id, *, allow_recorded=False, allow_finalized=False, allow_in_service=False):
    from .points_transfer_refund_access import permits
    formal = permits(phase='refund') or permits(phase='settlement')
    try:
        if not formal:
            require_isolated_checkout()
    except ValidationError as exc:
        raise PointsFundingPlanError('转单追加退款后的补资尚未开放') from exc
    if (not transaction.get_connection().in_atomic_block or type(order_id) is not int or order_id <= 0
            or (not formal and not getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_FUNDING_TESTING', False))
            or (not formal and allow_recorded and not getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_TESTING', False))
            or (allow_finalized and not allow_recorded)):
        raise PointsFundingPlanError('转单追加退款后的补资尚未开放')
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if order and permits(order, phase='settlement'):
        from .points_transfer_refund_access import verification_scope
        with verification_scope(order):
            return plan_locked(order_id, allow_recorded=allow_recorded, allow_finalized=allow_finalized,
                               allow_in_service=allow_in_service)
    if formal and (not order or not any(permits(order, phase=phase) for phase in ('refund', 'settlement'))):
        raise PointsFundingPlanError('转单追加退款资金范围尚未开放')
    if (not order or (order.status != Order.Status.COMPLETED and not (
            allow_in_service and formal and order.status == Order.Status.IN_SERVICE))
            or (order.settled_at and not allow_finalized)
            or order.source_order_id or Order.objects.filter(source_order_id=order.pk).exists()
            or order.refund_records.count() != 2):
        raise PointsFundingPlanError('转单追加退款后的订单状态待核对')
    rows = list(OrderPointsTransferFollowupFundingRevision.objects.select_for_update().filter(
        posting__points_refund__redemption__order=order).select_related('posting').order_by('pk')[:2])
    if len(rows) != 1:
        raise PointsFundingPlanError('转单追加退款资金版本缺失或重复')
    from .points_transfer_refund_access import has_paid_addon_before
    if formal and has_paid_addon_before(order, rows[0].posting.created_at):
        from .points_transfer_followup_scope import addon_snapshot, addon_chain_before
        history = addon_snapshot(order, rows[0].first_posting)
        if history is None:
            history = addon_chain_before(order, rows[0].first_posting, rows[0].posting.created_at,
                                         pending_snapshot=rows[0].pending_handover_snapshot)
        if not history:
            raise PointsFundingPlanError('转单追加退款加购资金范围尚未开放')
    from .points_transfer_followup_termination import verify_locked as verify_termination
    from .points_transfer_followup_revision import verify_locked as verify_revision, _verify_followup_revision_facts_locked
    try:
        if allow_recorded:
            actual, _ = _verify_followup_revision_facts_locked(rows[0])
        else:
            verify_termination(rows[0].posting)
            actual, _ = verify_revision(rows[0])
    except ValidationError as exc:
        raise PointsFundingPlanError('转单追加退款结案或资金凭证待核对') from exc
    from .points_transfer_followup_addon_version import latest_run_locked
    try:
        latest = latest_run_locked(order, rows[0])
        if latest:
            from .points_negotiation_preview import RefundedFunding
            actual = RefundedFunding(**latest.points_followup_snapshot['funding'])
    except (ValidationError, KeyError, TypeError) as exc:
        raise PointsFundingPlanError('转单后续加购资金版本待核对') from exc
    from .models import PointsFollowupTransferRevision
    post_transfer = PointsFollowupTransferRevision.objects.filter(order=order).first()
    if post_transfer and latest and latest.created_at < post_transfer.created_at:
        latest = None
    if post_transfer:
        from dataclasses import replace
        from .points_followup_transfer_revision import available_after_transfer_locked
        from .points_negotiation_preview import RefundedFunding
        try:
            if latest:
                from .points_followup_transfer_revision import verify_locked
                verify_locked(post_transfer)
                if (latest.points_followup_snapshot.get('post_followup_transfer_id') != post_transfer.pk
                        or latest.points_followup_snapshot.get('post_followup_transfer_hash') != post_transfer.evidence_hash):
                    raise ValidationError('后续加购没有绑定真实转单版本')
            else:
                available = available_after_transfer_locked(post_transfer)
                frozen = RefundedFunding(**post_transfer.snapshot['funding'])
                if available < 0:
                    raise ValidationError('转单释放超过原到账分配')
                actual = replace(frozen, allocated_units=available, release_units=0,
                                 remaining_units=max(frozen.required_units - available, 0))
        except (ValidationError, KeyError, TypeError) as exc:
            raise PointsFundingPlanError('二次退款后转单资金版本待核对') from exc
    return actual
