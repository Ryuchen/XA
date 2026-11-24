"""Isolated reconciliation of a transferred points order after one refund."""
from django.conf import settings
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import Order
from .points_funding_plan import PointsFundingPlanError


def plan_refunded_transfer(order_id, *, allow_finalized=False, include_basis=False):
    """Return verified funding and optionally its posting, transfer version and later events."""
    from .points_transfer_refund_access import permits, verification_scope
    order = Order.objects.filter(pk=order_id).first()
    if order and any(permits(order, phase=phase) for phase in ('report', 'settlement')):
        with verification_scope(order):
            return _plan_refunded_transfer(order_id, allow_finalized=allow_finalized, include_basis=include_basis)
    return _plan_refunded_transfer(order_id, allow_finalized=allow_finalized, include_basis=include_basis)


def _plan_refunded_transfer(order_id, *, allow_finalized=False, include_basis=False):
    from .points_transfer_refund_access import permits, require_refund_or_isolated
    try:
        require_refund_or_isolated()
    except ValidationError as exc:
        raise PointsFundingPlanError('转单退款后补资尚未开放') from exc
    if (not transaction.get_connection().in_atomic_block or type(order_id) is not int or order_id <= 0
            or (not permits(phase='refund') and not getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_FUNDING_TESTING', False))):
        raise PointsFundingPlanError('转单退款后补资尚未开放')
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if permits(phase='refund') and not permits(order, phase='refund'):
        raise PointsFundingPlanError('积分转单资金上下文与订单不一致')
    if (not order or order.status not in (Order.Status.IN_SERVICE, Order.Status.COMPLETED)
            or order.source_order_id or Order.objects.filter(source_order_id=order.pk).exists()
            or order.refund_records.count() != 1):
        raise PointsFundingPlanError('转单退款后的订单或加购记录待核对')
    if order.settled_at:
        from .models import OrderSegmentSettlementManifest
        if not allow_finalized or not OrderSegmentSettlementManifest.objects.filter(
                order=order, finalized_at=order.settled_at).exists():
            raise PointsFundingPlanError('转单退款后的结算凭证待核对')
    from .points_transfer_refund_continuation import verify_locked
    from .points_transfer_addon_events import later_runs
    from .points_negotiation_preview import RefundedFunding
    try:
        from .models import PointsPostRefundTransferRevision
        versions = list(PointsPostRefundTransferRevision.objects.filter(order=order).order_by('pk'))
        latest = versions[-1] if versions else None
        if latest:
            posting = verify_locked(order) if include_basis else None
            from .points_post_refund_transfer import verify_locked as verify_version, unresolved_pending_locked
            for version in versions:
                verify_version(version)
                pending = unresolved_pending_locked(version)
                from .points_transfer_addon_liability import first_refund_pending_allowed
                if pending and not all(first_refund_pending_allowed(order, item[0]) for item in pending):
                    raise ValidationError('转单后加购尚待三方确认接替')
            if not posting:
                from .models import PointsNegotiationPosting
                posting = PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order).first()
                if not posting:
                    raise ValidationError('转单退款凭证缺失')
            later = later_runs(order, posting)
            for event in later:
                when = (event.payment.buyer_transaction.created_at if event.payment_id else
                        event.refund.created_at if event.refund_id else event.handover.accepted_at)
                preceding = [version for version in versions if version.created_at < when]
                snapshot = event.refunded_main_snapshot
                if not isinstance(snapshot, dict):
                    raise ValidationError('加购资金事件版本缺失')
                if preceding:
                    if snapshot.get('transfer_revision_id') != preceding[-1].pk:
                        raise ValidationError('加购资金事件与转单版本不一致')
                elif (snapshot.get('transfer_origin_posting_id') != posting.pk
                      and not (isinstance(posting.participants, list)
                               and event.created_at < versions[0].transfer.created_at
                               and snapshot.get('posting_id') == posting.pk
                               and not snapshot.get('transfer_revision_id'))):
                    raise ValidationError('加购资金事件的主单来源不一致')
            before = [row for row in later if row.created_at < latest.created_at]
            after = [row for row in later if row.created_at > latest.created_at]
            if len(before) + len(after) != len(later):
                raise ValidationError('转单与加购资金事件时序无法区分')
            if before and any(not isinstance(row.refunded_main_snapshot, dict)
                    or (not row.refunded_main_snapshot.get('transfer_revision_id')
                        and row.refunded_main_snapshot.get('transfer_origin_posting_id') != posting.pk
                        and not (isinstance(posting.participants, list)
                            and row.created_at < versions[0].transfer.created_at
                            and not row.refunded_main_snapshot.get('transfer_origin_posting_id')
                            and row.refunded_main_snapshot.get('posting_id') == posting.pk))
                    for row in before):
                raise ValidationError('转单前加购资金版本待核对')
            if any(not isinstance(row.refunded_main_snapshot, dict)
                   or row.refunded_main_snapshot.get('transfer_revision_id') != latest.pk for row in after):
                raise ValidationError('转单后的加购资金版本待核对')
            snapshot = after[-1].refunded_main_snapshot if after else latest.snapshot
            funding = RefundedFunding(**snapshot['funding'])
            later = after
        else:
            posting, _, initial = verify_locked(order, include_release=True)
            later = later_runs(order, posting)
            if any(not isinstance(row.refunded_main_snapshot, dict)
                   or row.refunded_main_snapshot.get('transfer_origin_posting_id') != posting.pk
                   or row.refunded_main_snapshot.get('transfer_revision_id')
                   or row.created_at <= posting.created_at for row in later):
                raise ValidationError('主单退款后的加购资金来源待核对')
            funding = RefundedFunding(**later[-1].refunded_main_snapshot['funding']) if later else initial
        return (funding, posting, latest, later) if include_basis else funding
    except ValidationError as exc:
        raise PointsFundingPlanError('转单退款后的协商或补资凭证待核对') from exc
