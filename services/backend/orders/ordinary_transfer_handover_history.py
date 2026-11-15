"""Verify discarded add-on handover versions without taking financial locks."""
from django.core.exceptions import ValidationError


def verify_discarded(handover, boundary):
    from console.models import AdminAuditLog
    from .models import PointsHandoverRefundInvalidation
    from .points_handover_refund_invalidation import digest
    payment = handover.payment
    if (handover.status not in ('REJECTED', 'SUPERSEDED') or handover.accepted_at
            or not handover.created_at or not boundary or handover.created_at >= boundary):
        raise ValidationError('未生效加购接替版本异常')
    decisions = list(handover.decisions.values_list('account_id', 'role', 'accepted', 'decided_at'))
    expected = {(payment.intent.buyer_id, 'BOSS'), (handover.source_segment.provider_account_id, 'PROVIDER'),
                (handover.target_segment.provider_account_id, 'PROVIDER')}
    if (len(decisions) != 3 or {(a, role) for a, role, _, _ in decisions} != expected
            or any((answer is None) != (at is None) or (at and (at < handover.created_at or at >= boundary))
                   for _, _, answer, at in decisions)
            or (handover.status == 'REJECTED' and sum(answer is False for _, _, answer, _ in decisions) != 1)
            or (handover.status == 'SUPERSEDED' and any(answer is False for _, _, answer, _ in decisions))
            or all(answer is True for _, _, answer, _ in decisions)):
        raise ValidationError('未生效加购接替决定异常')
    audits = list(AdminAuditLog.objects.filter(resource='addon-handover', object_id=str(handover.pk),
        path=f'/api/admin/orders/{payment.intent.order_id}/addons/payments/{payment.pk}/handovers/',
        method='POST', status_code=200))
    body = {'order_id': payment.intent.order_id, 'payment_id': payment.pk,
            'source_provider': handover.source_segment.provider_account_id,
            'target_provider': handover.target_segment.provider_account_id,
            'retained_basis': handover.retained_basis, 'completed_work': handover.completed_work,
            'remaining_work': handover.remaining_work}
    if len(audits) != 1 or audits[0].operator_account_id != handover.created_by_id or audits[0].request_body != body:
        raise ValidationError('未生效加购接替审计异常')
    record = PointsHandoverRefundInvalidation.objects.filter(handover=handover).select_related('refund').first()
    if record:
        from .ordinary_pending_handover_refund import capture
        if (handover.status != 'SUPERSEDED' or record.refund.created_at >= boundary
                or record.created_at < record.refund.created_at or record.created_at >= boundary
                or not isinstance(record.snapshot, dict) or record.evidence_hash != digest(record.snapshot)
                or capture(handover, record.refund) != record.snapshot):
            raise ValidationError('加购退款导致接替失效的事实异常')
    elif handover.status == 'SUPERSEDED':
        # A manually replaced pending proposal has no financial event; the
        # next genuine version is its proof of replacement.
        if not payment.handovers.filter(version=handover.version + 1, created_at=boundary).exists():
            raise ValidationError('被替换接替方案缺少后续版本')


def verify_history(payment, *, before_version, boundary):
    from .ordinary_transfer_pre_main_addon import verify_handover_at
    rows = list(payment.handovers.filter(version__lt=before_version).order_by('version'))
    if [row.version for row in rows] != list(range(1, before_version)):
        raise ValidationError('加购接替版本不连续')
    for index, row in enumerate(rows):
        end = rows[index + 1].created_at if index + 1 < len(rows) else boundary
        if row.status == 'ACCEPTED':
            verify_handover_at(payment, row, end)
        else:
            verify_discarded(row, end)
