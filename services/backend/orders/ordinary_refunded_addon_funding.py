"""Verify the whole-order funding position after an ordinary negotiated refund."""
from dataclasses import dataclass

from django.utils import timezone
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import (NegotiationExecution, Order, OrderAddonHandover, OrderAddonPayment,
                     OrderOriginalShareTransfer, OrderPlatformSubsidy, OrderPrestartHandoff)
from .points_funding_plan import PointsFundingPlanError
from .settlement import SettlementError


@dataclass(frozen=True)
class OrdinaryRefundedAddonFunding:
    required: int
    allocated: int
    remaining: int
    releasable: int
    main: object
    combined: object
    addon_history: tuple


def plan_locked(order_id, *, _allocated_at_boundary=None, _boundary=None, _allow_settled=False,
                _allow_finalized=False, _allow_unconfirmed_addons=False):
    """Recompute verified sources and liabilities; never move or reserve funds.

    The caller holds a tenant transaction. A separate posting operation must
    recheck this plan under its own order and funding locks before paying.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('退款后整单资金核对必须在门店事务内执行')
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if not order:
        raise PointsFundingPlanError('订单不存在')
    from .financial_lock import lock_order_money
    lock_order_money()
    from .negotiation_execution import _lock_reverse_addon_people
    _lock_reverse_addon_people(order)
    from .points_service_payment import needs_integrated_processing
    addons = list(OrderAddonPayment.objects.select_for_update().filter(intent__order=order)
                  .select_related('intent').order_by('pk'))
    refund_query = order.refund_records.select_for_update()
    if _boundary is not None:
        refund_query = refund_query.filter(created_at__lt=_boundary)
    refunds = list(refund_query.order_by('pk'))
    handovers = OrderAddonHandover.objects.filter(payment__in=addons, status__in=('PENDING', 'ACCEPTED'))
    if _boundary is not None:
        handovers = handovers.filter(created_at__lt=_boundary)
    from django.conf import settings
    from .negotiation_zero import marked_execution_id, ordinary_funding_enabled
    try:
        zero_id = (marked_execution_id(order) if not refunds and ordinary_funding_enabled(
            order, has_addons=bool(addons)) else None)
    except ValidationError as exc:
        raise PointsFundingPlanError('零退款协商标记待核对') from exc
    is_zero = zero_id is not None
    if ((is_zero and getattr(settings, 'ORDINARY_ZERO_ADDON_HANDOVER_FUNDING_INTERNAL_TESTING', False))
            or (not is_zero and len(refunds) == 1 and getattr(
                settings, 'ORDINARY_REFUNDED_ADDON_HANDOVER_FUNDING_INTERNAL_TESTING', False))):
        handovers = handovers.filter(status='PENDING')
    if not is_zero and len(refunds) == 1:
        from .ordinary_refunded_team_handover import permits as team_handover_permits
        if team_handover_permits(order):
            handovers = handovers.filter(status='PENDING')
    from .points_checkout_test import ordinary_refund_production_order
    from .ordinary_addon_production import eligible as ordinary_addon_eligible
    from .ordinary_funding_continuation import refunded_continuation_active
    scoped_continuation = refunded_continuation_active(order)
    production_refund = (scoped_continuation or ordinary_refund_production_order(order) or ordinary_addon_eligible(
        order, allow_open=_allow_unconfirmed_addons))
    from .ordinary_zero_followup_capability import eligible as zero_followup_eligible
    if is_zero and zero_followup_eligible(order):
        production_refund = True
        if handovers.exists():
            from .ordinary_team_pre_main_handover import verify as verify_pre_zero
            from .ordinary_refunded_team_handover import verify as verify_post_zero
            execution_for_handover = NegotiationExecution.objects.filter(pk=zero_id, zero_order=order).first()
            if not execution_for_handover:
                raise PointsFundingPlanError('首次零退款协商凭证缺失')
            for handover in handovers.filter(status='ACCEPTED').select_related('payment__intent__order',
                    'source_segment__attribution', 'target_segment__attribution'):
                if handover.accepted_at < execution_for_handover.accepted_at:
                    verify_pre_zero(handover)
                else:
                    verify_post_zero(handover)
            handovers = handovers.filter(status='PENDING')
    if (needs_integrated_processing(order) or order.is_gift_snapshot
            or (not addons and not (production_refund or getattr(settings, 'ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING', False)))
            or (len(refunds) != 1 and not is_zero)
            or (order.settled_at and not _allow_finalized and (_boundary is None or order.settled_at <= _boundary))
            or order.funding_terms is None
            or OrderPlatformSubsidy.objects.filter(order=order).exists()
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
            or OrderPrestartHandoff.objects.filter(order=order).exists()
            or (not _allow_unconfirmed_addons and handovers.exists())):
        raise PointsFundingPlanError('当前主单退款与加购组合暂不能核对补资')
    executions = NegotiationExecution.objects.select_for_update().select_related(
        'refund', 'zero_order', 'proposal__case__order', 'customer_transaction__wallet')
    execution = (executions.filter(pk=zero_id, zero_order=order).first() if is_zero
                 else executions.filter(refund=refunds[0]).first())
    if not execution or not execution.termination_processed_at:
        raise PointsFundingPlanError('主单退款及终止凭证缺失')
    if is_zero and _boundary is not None and execution.termination_processed_at >= _boundary:
        raise PointsFundingPlanError('零退款协商与补资记录时序待核对')
    refund_amount = 0 if is_zero else refunds[0].amount
    expected_status = (Order.PaymentStatus.PAID if is_zero else Order.PaymentStatus.REFUNDED
                       if refund_amount == order.amount else Order.PaymentStatus.PARTIALLY_REFUNDED)
    if ((_boundary is None and order.payment_status != expected_status)
            or (not is_zero and not order.refunded_at) or (is_zero and order.refunded_at)):
        raise PointsFundingPlanError('主单退款状态待核对')
    try:
        from .addon_payment import verify_order_debit_links
        verify_order_debit_links(order, table_exists=True, lock=True)
        if is_zero:
            from .negotiation_zero import verify_zero_execution, verify_zero_termination
            from .negotiation_settlement import execution_for_order
            if execution_for_order(order, lock=True).pk != execution.pk:
                raise PointsFundingPlanError('零退款协商凭证待核对')
            shares = verify_zero_execution(execution)
            verify_zero_termination(execution)
            if production_refund and any(payment.refund_records.filter(created_at__lt=execution.created_at).exists()
                                         for payment in addons):
                from .ordinary_addon_prior_refund import verify_locked
                verify_locked(order, boundary=execution.created_at)
        else:
            from .negotiation_execution import verify_execution
            from .negotiation_termination import verify_termination_evidence
            shares = verify_execution(execution)
            verify_termination_evidence(execution)
            if production_refund and any(payment.refund_records.filter(created_at__lt=execution.created_at).exists() for payment in addons):
                from .ordinary_addon_prior_refund import verify_locked
                verify_locked(order, boundary=execution.created_at)
        if (addons and _boundary is None and not _allow_unconfirmed_addons and (is_zero or production_refund or getattr(
                settings, 'ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING', False))):
            from .addon_handover import current_responsibilities
            affected = {row.account_id for row in shares}
            for payment in addons:
                _, responsibilities = current_responsibilities(payment, lock=True)
                for row in responsibilities:
                    if row['is_current'] and row['provider_id'] in affected:
                        from .addon_completion import verify_accepted
                        try:
                            verify_accepted(order, payment, execution, row)
                        except ValidationError as exc:
                            raise PointsFundingPlanError('终止陪玩仍负责加购，请先核对完成情况及交接') from exc
        payments = list(order.transactions.select_for_update().filter(tx_type='PAY', status='SUCCESS', amount__lt=0)
                        .select_related('wallet'))
        if (len(payments) != 1 or payments[0].amount != -order.amount
                or payments[0].wallet.account_id != order.customer_account_id
                or payments[0].wallet.user_id != order.customer_id
                or payments[0].balance_after != payments[0].balance_before + payments[0].amount
                or payments[0].reversal_of_id or payments[0].reversals.exists()):
            raise PointsFundingPlanError('主单付款流水待核对')
        assignments = list(order.providers.select_for_update().order_by('pk'))
        from .settlement import compute_provider_income
        if (not assignments or any((row.settled_at and not _allow_settled
                                     and (_boundary is None or row.settled_at <= _boundary))
                                    or not row.provider_account_id for row in assignments)
                or len({row.provider_account_id for row in assignments}) != len(assignments)
                or sum(row.settlement_base for row in assignments) != order.amount
                or sum(row.provider_income for row in assignments) != order.provider_income
                or any(row.commission_type not in ('PERCENT', 'FIXED')
                       or row.provider_income != compute_provider_income(row.settlement_base, row.commission_type,
                       row.commission_rate, row.commission_fixed) for row in assignments)):
            raise PointsFundingPlanError('主单陪玩收益快照待核对')
        pending = {row.account_id: row.pending_deduction for row in shares}
        pre_addon_income = {row.provider_account_id: row.provider_income - pending.get(row.provider_account_id, 0)
                            for row in assignments}
        if min(pre_addon_income.values()) < 0:
            raise PointsFundingPlanError('主单退款承担超过可用收益')
        allocations = list(order.funding_allocations.select_for_update().order_by('pk'))
        sources = {kind: sum(row.amount for row in allocations if row.kind == kind)
                   for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
        returned = {kind: sum(row.refunded_amount for row in allocations if row.kind == kind)
                    for kind in sources}
        if _boundary is not None and not is_zero:
            from .refund_math import wallet_source_refund
            actual = wallet_source_refund(coin_units=refunds[0].amount, paid_prepaid_units=sources['PREPAID'],
                paid_gift_units=sources['GIFT'], paid_compensation_units=sources['COMPENSATION'])
            returned = {'PREPAID': actual.prepaid_units, 'GIFT': actual.gift_units,
                        'COMPENSATION': actual.compensation_units}
        if sum(sources.values()) != order.amount or sum(returned.values()) != refund_amount:
            raise PointsFundingPlanError('主单付款或退款来源不一致')
        if _allocated_at_boundary is None:
            from .points_funding_evidence import verified_order_allocations_locked
            from .addon_funding_release import addon_release_totals
            if production_refund and _allow_unconfirmed_addons:
                from .points_funding_evidence import _verified_allocations_for_locked_order
                funding_allocations = _verified_allocations_for_locked_order(order)
            else:
                funding_allocations = verified_order_allocations_locked(order)
            released = addon_release_totals(funding_allocations)
            allocated = sum(row.amount - released[row.pk] for row in funding_allocations)
        elif type(_allocated_at_boundary) is int and _allocated_at_boundary >= 0 and _boundary is not None:
            allocated = _allocated_at_boundary
        else:
            raise PointsFundingPlanError('退款后的补资历史金额无效')
        if is_zero and not _allow_unconfirmed_addons:
            # Reconcile actual later add-on refunds even if no RELEASE was
            # necessary.  Existing allocations are locked above; an empty
            # history is verified under the order/financial locks instead.
            from .ordinary_zero_post_addon import verify_addon_refunds_before
            witness = funding_allocations[0].receipt_id if _allocated_at_boundary is None and funding_allocations else None
            verify_addon_refunds_before(order, execution, _boundary or timezone.now(), witness_receipt_id=witness)
        from .points_negotiation_preview import funding_after_refund
        rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
        if order.inviter_commission != order.amount * rate // 100:
            raise PointsFundingPlanError('主单推荐佣金快照待核对')
        main = funding_after_refund(principal_units=sources['PREPAID'], gift_units=sources['GIFT'],
            compensation_units=sources['COMPENSATION'], returned_prepaid_units=returned['PREPAID'],
            returned_gift_units=returned['GIFT'], returned_compensation_units=returned['COMPENSATION'],
            provider_income_units=order.provider_income, pending_deduction_units=sum(pending.values()),
            provider_external_units=0 if is_zero else refunds[0].recovered_amount,
            inviter_rate=rate, allocated_units=allocated)
        from .addon_funding_history import combined_at
        segment_shares = [(row.pk, row.provider_account_id, 0, 0, pre_addon_income[row.provider_account_id])
                          for row in assignments]
        historical_addons = [payment for payment in addons if _boundary is None
                             or payment.buyer_transaction.created_at <= _boundary]
        if historical_addons:
            history = combined_at(order, main, _boundary or timezone.now(), segment_shares=segment_shares)
            from .refunded_addon_funding import combine_refunded_history
            combined = combine_refunded_history(main, history, main_retained_by_account=pre_addon_income)
        else:
            history, combined = {'addon_history': []}, main
        # The rounding surplus belongs to the store even if gift or
        # compensation funding makes its ordinary net margin negative. Fund
        # that designated destination before releasing any participant.
        from dataclasses import replace
        addon_refunds = order.addon_refunds.all()
        if _boundary is not None:
            addon_refunds = addon_refunds.filter(created_at__lt=_boundary)
        rounding = execution.rounding_to_store_units + sum(addon_refunds.values_list('rounding_surplus', flat=True))
        if type(rounding) is not int or rounding < 0:
            raise PointsFundingPlanError('退款取整差额待核对')
        additional = max(rounding - combined.store_income_units, 0)
        if additional:
            required = combined.required_units + additional
            combined = replace(combined, store_income_units=combined.store_income_units + additional,
                required_units=required, release_units=max(allocated - required, 0),
                remaining_units=max(required - allocated, 0))
    except (ValidationError, SettlementError) as exc:
        raise PointsFundingPlanError('退款后整单资金凭证待核对') from exc
    return OrdinaryRefundedAddonFunding(required=combined.required_units, allocated=allocated,
        remaining=combined.remaining_units, releasable=combined.release_units, main=main,
        combined=combined, addon_history=tuple(history['addon_history']))
