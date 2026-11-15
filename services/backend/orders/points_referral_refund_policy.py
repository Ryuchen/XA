"""Verified paid referral liability for a first refund of settled points service."""
from django.core.exceptions import ValidationError


def required_locked(order, refund_amount):
    """Use the actual paid-coin base and posted commission, never point value."""
    from .models import OrderOriginalShareTransfer, OrderPointsSettlement
    from .points_service_payment import needs_integrated_processing
    from .referral_evidence import paid_referral_credits, ReferralEvidenceError
    from .refunds import proportional_targets
    if (not needs_integrated_processing(order) or order.addon_intents.exists()
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
            or order.refund_records.exists() or not order.settled_at):
        raise ValidationError('该积分订单的已发佣金退款范围尚待核对')
    settlement = OrderPointsSettlement.objects.select_related('redemption__payment', 'inviter_transaction__wallet').filter(
        order=order, finalized_at=order.settled_at).first()
    if not settlement or not settlement.redemption_id or settlement.redemption.order_id != order.pk:
        raise ValidationError('积分订单实际结算凭证缺失')
    redemption, payment = settlement.redemption, settlement.redemption.payment
    if (settlement.paid_units != redemption.paid_units or redemption.paid_units != order.amount
            or payment.order_id != order.pk or payment.tx_type != 'PAY' or payment.status != 'SUCCESS'
            or payment.amount != -redemption.paid_units or payment.reversal_of_id or payment.reversals.exists()
            or payment.balance_after != payment.balance_before + payment.amount
            or type(refund_amount) is not int or not 0 < refund_amount <= settlement.paid_units):
        raise ValidationError('积分订单实付或退款金额待核对')
    tx = settlement.inviter_transaction
    credits = list(order.transactions.filter(status='SUCCESS', tx_type__in=['INCOME', 'GIFT'],
        remark__startswith='推荐分佣：').order_by('pk'))
    try:
        referral = paid_referral_credits(order, credits, expected_amount=settlement.inviter_units)
    except ReferralEvidenceError as exc:
        raise ValidationError(str(exc)) from exc
    if ((len(referral) != 1 or not tx or referral[0].pk != tx.pk) if settlement.inviter_units else bool(referral or tx)):
        raise ValidationError('积分订单已发推荐佣金凭证异常')
    if not tx:
        return 0
    if (tx.reversals.exists() or tx.balance_after != tx.balance_before + tx.amount
            or tx.amount > settlement.paid_units):
        raise ValidationError('积分订单推荐佣金流水异常')
    # Preserve the project's existing proportional allocation and rounding;
    # the complementary weight is paid coins, not provider income funded by the store.
    targets = proportional_targets({'referral': tx.amount, 'other': settlement.paid_units - tx.amount}, refund_amount)
    return targets['referral']
