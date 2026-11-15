"""Receipt-local remaining add-on earnings across independently refunded payments."""
from django.core.exceptions import ValidationError


def remaining_for_payment(refund):
    """Use actual per-payment deductions, including other payments' refunds."""
    from .models import OrderAddonRefundEarningDeduction
    payment, order = refund.payment, refund.order
    rows = list(OrderAddonRefundEarningDeduction.objects.filter(payment=payment,
        refund__created_at__lte=refund.created_at).select_related(
            'refund__proposal', 'participant', 'segment').order_by('refund__created_at', 'refund_id', 'pk'))
    if any(row.refund_id != refund.pk and row.refund.created_at == refund.created_at for row in rows):
        raise ValidationError('加购收益扣减与退款事件时间无法区分')
    for row in rows:
        event, person = row.refund, row.participant
        if (event.order_id != order.pk or person.proposal_id != event.proposal_id
                or row.segment_id != payment.intent.segment_id
                or row.segment.provider_account_id != payment.intent.provider_id
                or person.account_id != payment.intent.provider_id or row.amount <= 0):
            raise ValidationError('逐付款收益扣减不属于实际履约人')
        all_rows = list(event.earning_deductions.all())
        if (sum(item.amount for item in all_rows) != event.addon_pending_deduction
                or sum(item.amount for item in all_rows if item.participant_id == person.pk)
                    != person.addon_pending_deduction):
            raise ValidationError('逐笔退款待结收益扣减合计不一致')
    remaining = payment.provider_income - sum(row.amount for row in rows)
    if remaining < 0:
        raise ValidationError('逐付款收益扣减超过实际收益')
    return remaining
