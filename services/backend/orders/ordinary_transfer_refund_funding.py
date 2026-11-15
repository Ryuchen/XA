"""Reconcile the first ordinary refund against real transferred service facts.

This is a locked funding calculation only. It neither grants access nor posts
a refund, allocates receipts, releases funds, or settles a participant.
"""
from dataclasses import replace

from rest_framework.exceptions import ValidationError
from stores.context import current_store
from stores.db import transaction

from .models import NegotiationExecution, Order, OrderTransferNegotiationEvidence
from .points_funding_plan import PointsFundingPlanError


def plan_locked(order_id, *, allow_finalized=False, _allocated_override=None, _with_addons=False):
    if not transaction.get_connection().in_atomic_block or current_store() is None:
        raise PointsFundingPlanError('转单退款补资须在本店订单事务内核对')
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if not order:
        raise PointsFundingPlanError('订单不存在')
    from .financial_lock import lock_order_money
    lock_order_money()
    from .points_service_payment import needs_integrated_processing
    from .models import OrderAddonPayment
    if (needs_integrated_processing(order) or order.is_gift_snapshot or order.source_order_id
            or (OrderAddonPayment.objects.filter(intent__order=order).exists() and not _with_addons)
            or (order.refund_records.count() != 1 and not (allow_finalized and _allocated_override is not None))
            or order.funding_terms is None or (order.settled_at and not allow_finalized)):
        raise PointsFundingPlanError('该转单退款资金组合尚待核对')
    execution = (NegotiationExecution.objects.select_for_update().select_related('refund', 'proposal__case')
                 .filter(refund__order=order, termination_processed_at__isnull=False).first())
    if not execution or not OrderTransferNegotiationEvidence.objects.filter(execution=execution, order=order).exists():
        raise PointsFundingPlanError('转单协商与终止凭证缺失')
    try:
        from .negotiation_execution import verify_execution
        from .negotiation_termination import verify_termination_evidence
        from .negotiation_transfer_evidence import verify_locked
        shares = verify_execution(execution)
        verify_termination_evidence(execution)
        evidence = verify_locked(execution)
        if not evidence:
            raise ValidationError('转单协商凭证缺失')
        snapshot = evidence.snapshot
        lines = snapshot.get('lines')
        if (not isinstance(lines, list) or not lines
                or any(not isinstance(line, dict) or type(line.get('income')) is not int or line['income'] < 0
                       for line in lines)):
            raise ValidationError('逐段收益凭证无效')
        allocations = list(order.funding_allocations.select_for_update().select_related('lot').order_by('pk'))
        from engagement.funding_wallet import validate_order_funding_sources
        validate_order_funding_sources(order, allocations)
        if sum(row.amount for row in allocations) != order.amount:
            raise ValidationError('原付款来源不完整')
        sources = {kind: sum(row.amount for row in allocations if row.kind == kind)
                   for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
        returned = {kind: sum(row.refunded_amount for row in allocations if row.kind == kind)
                    for kind in sources}
        if allow_finalized and _allocated_override is not None:
            historical = snapshot.get('return_sources')
            if (not isinstance(historical, dict) or set(historical) != set(returned)
                    or any(type(historical[kind]) is not int or historical[kind] < 0
                           or historical[kind] > returned[kind] for kind in returned)):
                raise ValidationError('原协商退款历史来源待核对')
            returned = historical
        if (sum(sources.values()) != order.amount or sum(returned.values()) != execution.refund.amount
                or snapshot.get('return_sources') != returned):
            raise ValidationError('原付款退款来源不一致')
        if _allocated_override is None:
            from .points_funding_evidence import _verified_allocations_for_locked_order
            from .addon_funding_release import addon_release_totals
            funding = _verified_allocations_for_locked_order(order)
            released = addon_release_totals(funding)
            allocated = sum(row.amount - released[row.pk] for row in funding)
        elif type(_allocated_override) is int and _allocated_override >= 0:
            allocated = _allocated_override
        else:
            raise ValidationError('补资历史分配无效')
        from .points_negotiation_preview import funding_after_refund
        result = funding_after_refund(principal_units=sources['PREPAID'], gift_units=sources['GIFT'],
            compensation_units=sources['COMPENSATION'], returned_prepaid_units=returned['PREPAID'],
            returned_gift_units=returned['GIFT'], returned_compensation_units=returned['COMPENSATION'],
            provider_income_units=sum(line['income'] for line in lines),
            pending_deduction_units=sum(row.pending_deduction for row in shares),
            provider_external_units=sum(row.wallet_deduction + row.deposit_deduction for row in shares),
            inviter_rate=order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0,
            allocated_units=allocated)
        # The agreement affects selected segments only; other segments of the
        # same person retain their original income. Add-on deductions are
        # incorporated separately by the combined add-on funding plan.
        if result.provider_income_units != (sum(line['income'] for line in lines)
                                            - sum(row.pending_deduction for row in shares)):
            raise ValidationError('退款后逐人保留收益不一致')
        extra = max(execution.rounding_to_store_units - result.store_income_units, 0)
        if extra:
            required = result.required_units + extra
            result = replace(result, store_income_units=result.store_income_units + extra,
                             required_units=required, release_units=max(allocated - required, 0),
                             remaining_units=max(required - allocated, 0))
        return result
    except (ValidationError, ValueError) as exc:
        raise PointsFundingPlanError('转单退款后的逐段或资金凭证待核对') from exc
