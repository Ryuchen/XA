"""Reconcile transferred service and paid add-ons under the order money lock.

This calculation grants no permission to refund or pay. The caller must
separately verify the agreement, funding release and participant settlement.
"""
from collections import defaultdict
from dataclasses import dataclass, replace

from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import NegotiationExecution, Order, OrderAddonPayment, OrderTransferNegotiationEvidence
from .points_funding_plan import PointsFundingPlanError


@dataclass(frozen=True)
class TransferAddonFunding:
    main: object
    combined: object
    addon_history: tuple
    addon_parts: tuple
    main_deductions: tuple


def plan_locked(order_id, *, allow_finalized=False, allocated_override=None, boundary=None,
                allow_unconfirmed_addons=False):
    from .ordinary_transfer_refund_funding import plan_locked as main_plan_locked
    main = main_plan_locked(order_id, allow_finalized=allow_finalized,
                            _allocated_override=allocated_override, _with_addons=True)
    order = Order.objects.select_for_update().get(pk=order_id)
    execution = (NegotiationExecution.objects.select_for_update().select_related('refund')
                 .get(refund__order=order, termination_processed_at__isnull=False))
    evidence = OrderTransferNegotiationEvidence.objects.select_for_update().get(execution=execution, order=order)
    lines = evidence.snapshot['lines']
    retained = defaultdict(int)
    for line in lines:
        retained[line['account_id']] += line['income']
    for share in execution.shares.all():
        retained[share.account_id] -= share.pending_deduction
    if any(value < 0 for value in retained.values()):
        raise PointsFundingPlanError('转单后陪玩保留收益待核对')
    payments_query = OrderAddonPayment.objects.select_for_update().filter(intent__order=order)
    if boundary is not None:
        payments_query = payments_query.filter(buyer_transaction__created_at__lt=boundary)
    payments = list(payments_query.select_related('intent').order_by('pk'))
    if not payments:
        raise PointsFundingPlanError('已付款加购凭证缺失')
    from .addon_handover import assert_no_pending_handover, current_responsibilities
    from .addon_completion import verify_accepted
    affected_segments = set(execution.proposal.affected_segment_ids or [])
    addon_parts = defaultdict(int)
    valid_segments = {(line['segment_id'], line['account_id']) for line in lines}
    try:
        for payment in payments:
            if not (allow_unconfirmed_addons and boundary is not None):
                assert_no_pending_handover(payment)
            # A funding release is an immutable historical calculation.
            # Subsequent handovers must not replace the responsibility that
            # was current at its stored boundary.
            if allow_unconfirmed_addons and boundary is not None:
                continue
            _, responsibilities = current_responsibilities(payment, lock=True)
            for row in responsibilities:
                if (row['segment_id'], row['provider_id']) not in valid_segments:
                    raise ValidationError('加购履约段不属于主订单')
                addon_parts[(payment.pk, row['segment_id'], row['provider_id'])] += row['income']
                if (not allow_unconfirmed_addons and row['is_current']
                        and row['segment_id'] in affected_segments):
                    verify_accepted(order, payment, execution, row)
        from .addon_funding_history import combined_at
        shares = [(line['segment_id'], line['account_id'], line['basis'], line['fee'], line['income'])
                  for line in lines]
        history = combined_at(order, main, boundary or timezone.now(), segment_shares=shares)
        from .refunded_addon_funding import combine_refunded_history
        combined = combine_refunded_history(main, history, main_retained_by_account=retained)
    except ValidationError as exc:
        raise PointsFundingPlanError('转单加购的完成或交接凭证待核对') from exc
    if not (allow_unconfirmed_addons and boundary is not None) and sum(addon_parts.values()) != sum(
            row['provider_units'] for row in history['addon_history']):
        raise PointsFundingPlanError('加购逐段收益与历史付款凭证不一致')
    # The store must receive the recorded rounding surplus independently of
    # the net margin of gift/compensation funding and paid add-ons.
    addon_refunds = order.addon_refunds.all()
    if boundary is not None:
        addon_refunds = addon_refunds.filter(created_at__lt=boundary)
    rounding = execution.rounding_to_store_units + sum(addon_refunds.values_list('rounding_surplus', flat=True))
    extra = max(rounding - combined.store_income_units, 0)
    if extra:
        required = combined.required_units + extra
        combined = replace(combined, store_income_units=combined.store_income_units + extra,
                           required_units=required, release_units=max(combined.allocated_units - required, 0),
                           remaining_units=max(required - combined.allocated_units, 0))
    parts = tuple({'payment_id': payment, 'segment_id': segment, 'provider_account_id': provider,
                   'provider_income_units': income}
                  for (payment, segment, provider), income in sorted(addon_parts.items()))
    return TransferAddonFunding(main=main, combined=combined, addon_history=tuple(history['addon_history']),
                                addon_parts=parts, main_deductions=tuple(history['main_deductions']))
