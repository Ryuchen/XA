"""Restricted ordinary team add-on handover before the first main refund."""
from django.core.exceptions import ValidationError


def verify(handover):
    if handover.previous_id:
        from .ordinary_team_later_handover import verify as verify_later
        return verify_later(handover)
    from console.models import AdminAuditLog
    from .addon_handover import _income
    from .addon_intents import _commission
    from .addon_offers import AddonSelectionError
    from .addon_payment import verify_receipt
    payment, order = handover.payment, handover.payment.intent.order
    verify_receipt(payment)
    if (handover.status != 'ACCEPTED' or not handover.accepted_at or handover.previous_id
            or not handover.created_at
            or payment.buyer_transaction.created_at >= handover.created_at
            or order.refund_records.filter(created_at__lte=handover.accepted_at).exists()
            or order.addon_refunds.filter(created_at__gte=handover.created_at,
                                           created_at__lte=handover.accepted_at).exists()
            or order.providers.filter(settled_at__lte=handover.accepted_at).exists()):
        raise ValidationError('主单退款前加购接替资金时序异常')
    if handover.version != 1:
        from .ordinary_pending_handover_refund import verify_retry
        if (payment.handovers.filter(version__lt=handover.version, status='ACCEPTED').exists()
                or not verify_retry(payment, before_version=handover.version, before=handover.created_at)):
            raise ValidationError('此前失效的加购接替凭证异常')
    prior_refunds = list(order.addon_refunds.filter(created_at__lt=handover.created_at).order_by('created_at', 'pk'))
    if prior_refunds:
        from .ordinary_multi_prior_addon_facts import verify as verify_prior
        verify_prior(order, handover.created_at)
    basis_before = payment.intent.amount - sum(row.amount for row in prior_refunds if row.payment_id == payment.pk)
    income_before = payment.provider_income - sum(payment.refund_earning_deductions.filter(
        refund__created_at__lt=handover.created_at).values_list('amount', flat=True))
    source, target = handover.source_segment, handover.target_segment
    if (source.pk != payment.intent.segment_id or source.attribution.order_id != order.pk
            or target.attribution.order_id != order.pk or source.provider_account_id == target.provider_account_id
            or not source.started_at or not target.started_at or target.started_at > handover.created_at
            or (source.released_at and source.released_at < handover.accepted_at)
            or (target.released_at and target.released_at < handover.accepted_at)
            or basis_before <= 0 or income_before < 0 or handover.source_basis_before != basis_before
            or handover.retained_basis + handover.transferred_basis != basis_before
            or not handover.transferred_basis or not handover.remaining_work
            or (handover.retained_basis and not handover.completed_work)):
        raise ValidationError('主单退款前加购接替履约份额异常')
    decisions = list(handover.decisions.values_list('account_id', 'role', 'accepted', 'decided_at'))
    expected = {(payment.intent.buyer_id, 'BOSS', True), (source.provider_account_id, 'PROVIDER', True),
                (target.provider_account_id, 'PROVIDER', True)}
    if (len(decisions) != 3 or {(a, role, answer) for a, role, answer, _ in decisions} != expected
            or any(not at or at < handover.created_at for _, _, _, at in decisions)
            or max(at for _, _, _, at in decisions) != handover.accepted_at):
        raise ValidationError('主单退款前加购接替三方确认异常')
    try:
        commission = _commission(target, allow_refund_review=True, lock=False)
    except AddonSelectionError as exc:
        raise ValidationError('主单退款前加购接替抽成快照异常') from exc
    expected_rows = []
    if handover.retained_basis:
        original_income = _income(basis_before, payment.intent.commission_snapshot)
        retained_income = (_income(handover.retained_basis, payment.intent.commission_snapshot)
            if income_before == original_income else income_before * handover.retained_basis // basis_before)
        expected_rows.append((source.pk, handover.retained_basis,
            retained_income, payment.intent.commission_snapshot, False))
    expected_rows.append((target.pk, handover.transferred_basis,
        _income(handover.transferred_basis, commission), commission, True))
    rows = list(handover.shares.order_by('position'))
    if ([(row.segment_id, row.basis, row.income, row.commission_snapshot, row.is_current) for row in rows] != expected_rows
            or [row.position for row in rows] != list(range(len(rows)))):
        raise ValidationError('主单退款前加购接替逐段收益异常')
    audit = list(AdminAuditLog.objects.filter(resource='addon-handover', object_id=str(handover.pk),
        path=f'/api/admin/orders/{order.pk}/addons/payments/{payment.pk}/handovers/', method='POST', status_code=200))
    body = {'order_id': order.pk, 'payment_id': payment.pk, 'source_provider': source.provider_account_id,
            'target_provider': target.provider_account_id, 'retained_basis': handover.retained_basis,
            'completed_work': handover.completed_work, 'remaining_work': handover.remaining_work}
    if len(audit) != 1 or audit[0].operator_account_id != handover.created_by_id or audit[0].request_body != body:
        raise ValidationError('主单退款前加购接替审计异常')
    return handover


def permits(order):
    from rest_framework.exceptions import ValidationError as RestValidationError
    from .points_checkout_test import ordinary_production_enabled
    from .points_service_payment import needs_integrated_processing
    from .models import (OrderOriginalShareTransfer, OrderAddonHandover, OrderPointsFundingAllocation,
                         OrderAddonFundingReleaseRun)
    from .ordinary_refunded_team_handover import _accepting
    if (not ordinary_production_enabled() or not order or order.refund_records.exists()
            or needs_integrated_processing(order) or order.required_provider_count != 2
            or order.is_gift_snapshot or order.source_order_id
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        return False
    handovers = OrderAddonHandover.objects.filter(payment__intent__order=order, status='ACCEPTED').select_related(
        'payment__intent__order', 'source_segment__attribution', 'target_segment__attribution')
    try:
        for handover in handovers:
            verify(handover)
            funded = OrderPointsFundingAllocation.objects.filter(service_order=order,
                created_at__lte=handover.accepted_at).exists()
            if funded and _accepting.get() != handover.pk:
                run = OrderAddonFundingReleaseRun.objects.filter(handover=handover).first()
                if not run or not run.ordinary_prestart_snapshot or not run.ordinary_prestart_hash:
                    return False
                from .addon_funding_release import verify_run
                verify_run(run)
    except (ValidationError, RestValidationError, ValueError):
        return False
    if OrderAddonHandover.objects.filter(payment__intent__order=order, status='PENDING').exists():
        return not order.providers.filter(settled_at__isnull=False).exists()
    return True
