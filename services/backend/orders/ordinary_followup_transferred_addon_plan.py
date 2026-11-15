"""Read the real post-follow-up transfer version at an add-on event boundary."""
from collections import defaultdict
from dataclasses import asdict, replace

from django.core.exceptions import ValidationError


def plan_at(order, row, *, boundary, allocated):
    from django.db.models import Q
    from .models import NegotiationExecution, OrderOriginalShareTransfer, OrdinaryPostRefundTransferRevision
    from .ordinary_post_refund_transfer import verify_locked as verify_revision
    from .ordinary_transfer_event_scope import segment_lines_at
    from .points_negotiation_preview import funding_after_refund

    transfers = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
        created_at__lt=boundary).order_by('-created_at', '-pk')
    transfer = transfers.first()
    if not transfer or transfer.created_at <= row.execution.termination_processed_at:
        raise ValidationError('追加协商后没有可核验的真实转单')
    revision = OrdinaryPostRefundTransferRevision.objects.filter(order=order, transfer=transfer,
        created_at__lt=boundary).order_by('-created_at', '-pk').first()
    if not revision:
        raise ValidationError('追加协商后转单资金版本缺失')
    verify_revision(revision)
    snapshot = revision.snapshot
    lines = segment_lines_at(order, boundary)
    if [asdict(line) for line in lines] != snapshot.get('lines'):
        raise ValidationError('加购事件时的转单履约段不一致')
    receipts = list(NegotiationExecution.objects.filter(Q(refund__order=order) | Q(zero_order=order),
        termination_processed_at__isnull=False, termination_processed_at__lt=boundary).order_by('pk'))
    if ([item.pk for item in receipts] != snapshot.get('receipts')
            or not receipts or receipts[-1].pk != row.execution_id):
        raise ValidationError('转单后的协商轮次已变化')
    paid, returned = snapshot.get('sources'), snapshot.get('returned')
    if not isinstance(paid, dict) or not isinstance(returned, dict):
        raise ValidationError('转单原付款来源缺失')
    kinds = ('PREPAID', 'GIFT', 'COMPENSATION')
    if (any(type(paid.get(kind)) is not int or type(returned.get(kind)) is not int for kind in kinds)
            or sum(paid[kind] for kind in kinds) != order.amount
            or sum(returned[kind] for kind in kinds) != sum(item.refund.amount for item in receipts if item.refund_id)):
        raise ValidationError('转单原付款与退款来源异常')
    from engagement.funding_wallet import validate_order_funding_sources
    from engagement.funding_plan import RefundablePart, plan_refund
    sources = list(order.funding_allocations.select_related('lot').order_by('pk'))
    validate_order_funding_sources(order, sources)
    reconstructed = plan_refund([RefundablePart(item.pk, item.kind, item.amount) for item in sources],
        sum(item.refund.amount for item in receipts if item.refund_id))
    if (paid != {kind: sum(item.amount for item in sources if item.kind == kind) for kind in kinds}
            or returned != {kind: sum(reconstructed[item.pk] for item in sources if item.kind == kind)
                            for kind in kinds}):
        raise ValidationError('转单时原付款及退款构成不一致')
    amounts = defaultdict(int)
    for line in lines:
        amounts[line.account_id] += line.income
    actual_pending = defaultdict(int)
    for receipt in receipts:
        for share in receipt.shares.all():
            actual_pending[share.account_id] += share.pending_deduction
    if snapshot.get('pending') != [[account, amount] for account, amount in sorted(actual_pending.items())]:
        raise ValidationError('转单协商本人承担快照不一致')
    for account, value in snapshot.get('pending', []):
        amounts[account] -= value
    if any(value < 0 for value in amounts.values()):
        raise ValidationError('转单协商后本人收益异常')
    external = sum(share.wallet_deduction + share.deposit_deduction
                   for receipt in receipts for share in receipt.shares.all())
    main = funding_after_refund(principal_units=paid['PREPAID'], gift_units=paid['GIFT'],
        compensation_units=paid['COMPENSATION'], returned_prepaid_units=returned['PREPAID'],
        returned_gift_units=returned['GIFT'], returned_compensation_units=returned['COMPENSATION'],
        provider_income_units=sum(amounts.values()), pending_deduction_units=0, provider_external_units=external,
        inviter_rate=order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0,
        allocated_units=allocated)
    from .addon_funding_history import combined_at
    history = combined_at(order, main, boundary, segment_shares=[
        (line.segment_id, line.account_id, line.basis, line.fee, line.income) for line in lines])
    if history.get('addon_history'):
        from .refunded_addon_funding import combine_refunded_history
        result = combine_refunded_history(main, history, main_retained_by_account=amounts)
    else:
        result = main
    from .ordinary_transfer_addon_rounding import facts as rounding_facts
    rounding = sum(item.rounding_to_store_units for item in receipts) + sum(
        item[1] for item in rounding_facts(order, before=boundary))
    extra = max(rounding - result.store_income_units, 0)
    if extra:
        required = result.required_units + extra
        result = replace(result, store_income_units=result.store_income_units + extra, required_units=required,
            release_units=max(allocated - required, 0), remaining_units=max(required - allocated, 0))
    return result, history
