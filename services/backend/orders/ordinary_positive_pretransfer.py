"""Original positive agreement, verified from facts preceding a later transfer."""
from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as RestValidationError


def verify(order, *, before):
    from console.deposit_clearance import _verified_refund_execution
    from .models import NegotiationExecution, OrderOriginalShareTransfer, OrderSegmentFinancialSnapshot
    from .negotiation_termination import verify_termination_evidence
    from .settlement import compute_provider_income
    refunds = list(order.refund_records.filter(created_at__lt=before).order_by('created_at', 'pk')[:2])
    if len(refunds) != 1:
        raise ValidationError('首次转单前须有且仅有一笔真实协商退款')
    execution = _verified_refund_execution(refunds[0])
    if (not execution or not execution.termination_processed_at or execution.termination_processed_at >= before
            or execution.proposal.case.order_id != order.pk or execution.proposal.refund_units <= 0
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
                created_at__lte=execution.termination_processed_at).exists()):
        raise ValidationError('转单前正退款协商来源或时序异常')
    try:
        verify_termination_evidence(execution)
    except RestValidationError as exc:
        raise ValidationError('转单前退款结案凭证异常') from exc
    from wallet.models import Transaction
    payments = list(Transaction.objects.filter(order=order, tx_type='PAY', status='SUCCESS',
        reversal_of__isnull=True).select_related('wallet'))
    if (len(payments) != 1 or payments[0].amount != -order.amount
            or payments[0].wallet.account_id != order.customer_account_id
            or payments[0].wallet.user_id != order.customer_id
            or payments[0].balance_after != payments[0].balance_before + payments[0].amount
            or payments[0].reversals.exists()):
        raise ValidationError('转单前退款的原付款流水异常')
    snapshots = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order,
        segment__accepted_at__lt=execution.accepted_at).select_related('segment'))
    if (len(snapshots) != order.required_provider_count
            or len({row.segment.provider_account_id for row in snapshots}) != len(snapshots)
            or sum(row.incoming_basis for row in snapshots) != order.amount):
        raise ValidationError('转单前原履约段快照异常')
    from .addon_main_deduction import deductions_at
    prior = deductions_at(order, before=execution.created_at)
    by_account = {item.segment.provider_account_id: item for item in snapshots}
    allocations = execution.refund.negotiated_allocation
    if (not isinstance(allocations, list) or len(allocations) != len(snapshots)
            or {item.get('account_id') for item in allocations if isinstance(item, dict)} != set(by_account)):
        raise ValidationError('转单前退款原陪玩分配异常')
    terms = {item['account_id']: item for item in execution.proposal.provider_terms}
    shares = list(execution.shares.select_related('assignment', 'wallet_transaction__wallet',
        'deposit_deficit__profile', 'deposit_deficit__source_store_transaction__wallet__user'))
    if set(terms) != {item.account_id for item in shares} or not shares:
        raise ValidationError('转单前退款受影响陪玩异常')
    for item in allocations:
        snap = by_account[item['account_id']]
        income = snap.income_at_entry - prior.get(item['account_id'], 0)
        term = terms.get(item['account_id'])
        if (item.get('role') != 'PROVIDER' or type(item.get('settled')) is not bool
                or item.get('key') != f"provider:{item.get('share_id')}"
                or not order.providers.filter(pk=item.get('share_id'), provider_account_id=item.get('account_id'),
                                              provider_id=item.get('user_id')).exists()
                or snap.income_at_entry != compute_provider_income(snap.incoming_basis, snap.commission_type,
                    snap.commission_rate, snap.commission_fixed)
                or item.get('entitlement') != income or item.get('amount') != (term['burden_units'] if term else 0)
                or item.get('retained') != (term['retained_units'] if term else income)):
            raise ValidationError('转单前退款收益分配异常')
    from .ordinary_refund_receipt_evidence import _verify_direct_deposit
    for share in shares:
        term, tx = terms[share.account_id], share.wallet_transaction
        snap = by_account.get(share.account_id)
        if (not snap or snap.segment.released_at != execution.accepted_at
                or share.assignment.order_id != order.pk or share.assignment.provider_account_id != share.account_id
                or share.original_income != term['original_income_units'] or share.retained_income != term['retained_units']
                or share.burden != term['burden_units']
                or share.burden != share.pending_deduction + share.wallet_deduction + share.deposit_deduction
                or share.original_income != share.pending_deduction + share.retained_income
                or bool(tx) != bool(share.wallet_deduction) or bool(share.deposit_deficit_id) != bool(share.deposit_deduction)):
            raise ValidationError('转单前退款本人承担或原段终止异常')
        if tx and (tx.order_id != order.pk or tx.wallet.account_id != share.account_id
                or tx.wallet.user_id != share.assignment.provider_id or tx.tx_type != 'REFUND' or tx.status != 'SUCCESS'
                or tx.amount != -share.wallet_deduction or tx.balance_after != tx.balance_before + tx.amount
                or tx.operator_account_id != execution.operator_id or tx.operator_id != execution.refund.operator_id
                or tx.reversal_of_id or tx.reversals.exists()):
            raise ValidationError('转单前退款本人实际流水异常')
        if share.deposit_deduction:
            _verify_direct_deposit(share.deposit_deficit, refund=execution.refund, account_id=share.account_id,
                user_id=share.assignment.provider_id, amount=share.deposit_deduction, addon=False)
    if (sum(item.burden for item in shares) != execution.refund.amount + execution.rounding_to_store_units
            or execution.refund.recovered_amount != sum(item.wallet_deduction + item.deposit_deduction for item in shares)):
        raise ValidationError('转单前退款承担合计异常')
    return execution
