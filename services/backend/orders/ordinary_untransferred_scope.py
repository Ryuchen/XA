"""Original-seat facts for ordinary orders with no handoff or transfer."""
from .models import (OrderOriginalShareTransfer, OrderPrestartHandoff, OrderProvider,
                     OrderSegmentFinancialSnapshot, OrderStatusLog)
from .settlement import compute_provider_income, split_settlement_bases


def supports(order, *, boundary=None):
    if not order or order.required_provider_count not in (1, 2):
        return False
    if (OrderPrestartHandoff.objects.filter(order=order).exists()
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
            or order.status_logs.filter(action=OrderStatusLog.Action.TRANSFER).exists()):
        return False
    assignments = list(OrderProvider.objects.filter(order=order).order_by('pk'))
    snapshots = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order)
                     .select_related('segment', 'segment__attribution').order_by('segment__slot'))
    count = order.required_provider_count
    if (len(assignments) != count or len(snapshots) != count
            or any((row.released_at and (boundary is None or row.released_at <= boundary))
                   or (boundary is not None and row.created_at >= boundary)
                   or not row.provider_account_id or not row.provider_id for row in assignments)
            or len({row.provider_account_id for row in assignments}) != count
            or {row.segment.slot for row in snapshots} != set(range(1, count + 1))):
        return False
    by_account = {row.provider_account_id: row for row in assignments}
    if {row.segment.provider_account_id for row in snapshots} != set(by_account):
        return False
    from .delta_personal_commission import verify_segment_evidence
    bases = split_settlement_bases(order.amount, count)
    for snapshot in snapshots:
        segment, assignment = snapshot.segment, by_account[snapshot.segment.provider_account_id]
        basis = bases[segment.slot - 1]
        if ((segment.released_at and (boundary is None or segment.released_at <= boundary))
                or (boundary is not None and segment.accepted_at >= boundary)
                or segment.released_at != assignment.released_at
                or snapshot.retained_basis is not None or snapshot.incoming_basis != basis
                or assignment.settlement_base != basis or snapshot.commission_type != assignment.commission_type
                or snapshot.commission_rate != assignment.commission_rate
                or snapshot.commission_fixed != assignment.commission_fixed
                or snapshot.game_commission_snapshot != assignment.game_commission_snapshot
                or snapshot.income_at_entry != assignment.provider_income
                or assignment.provider_income != compute_provider_income(basis, assignment.commission_type,
                    assignment.commission_rate, assignment.commission_fixed)
                or not verify_segment_evidence(segment, snapshot)):
            return False
        if boundary is not None and segment.released_at:
            # A later negotiated termination may close the original segment;
            # prove that closure rather than treating today's active set as
            # the set that existed at the earlier add-on event.
            from .models import NegotiationExecutionShare
            from console.deposit_clearance import _verified_refund_execution
            from .negotiation_termination import verify_termination_evidence
            from rest_framework.exceptions import ValidationError
            matches = list(NegotiationExecutionShare.objects.filter(
                assignment=assignment, account_id=assignment.provider_account_id,
                execution__accepted_at=segment.released_at,
                execution__termination_processed_at__isnull=False).select_related('execution__refund'))
            if len(matches) != 1:
                return False
            try:
                execution = matches[0].execution
                if execution.refund_id:
                    execution = _verified_refund_execution(execution.refund)
                    if not execution or execution.pk != matches[0].execution_id:
                        return False
                    verify_termination_evidence(execution)
                elif execution.zero_order_id == order.pk:
                    from datetime import timedelta
                    from django.utils import timezone
                    from .ordinary_zero_followup_evidence import verified_first
                    shares = verified_first(execution, boundary=timezone.now() + timedelta(microseconds=1))
                    if len(shares) != 1 or shares[0].pk != matches[0].pk:
                        return False
                else:
                    return False
            except ValidationError:
                return False
    return sum(row.provider_income for row in assignments) == order.provider_income
