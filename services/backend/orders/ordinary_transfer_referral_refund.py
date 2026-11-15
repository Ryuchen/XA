"""Quote store liability for a previously paid service referral commission.

This function only checks and calculates; it never moves funds. A posting
caller must hold the order and money locks and recalculate before binding a
verified platform receipt allocation to the refund.
"""
from django.core.exceptions import ValidationError

from .models import (OrderSegmentSettlementManifest, ReferralRefundFunding,
                     ReferralRefundFundingAction)


def required_for_refund(order, refund_amount):
    from .models import OrderOriginalShareTransfer
    if (not OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
            or type(refund_amount) is not int or refund_amount <= 0 or order.is_gift_snapshot):
        raise ValidationError('该订单的已发推荐佣金资金来源尚待核对')
    manifest = (OrderSegmentSettlementManifest.objects.select_related('inviter_transaction__wallet')
                .filter(order=order).first())
    from .segment_settlement import _hash
    if (not manifest or not manifest.finalized_at or order.settled_at != manifest.finalized_at
            or not isinstance(manifest.plan, dict) or manifest.plan_hash != _hash(manifest.plan)
            or manifest.plan.get('inviter_rate') != order.inviter_commission_rate_snapshot
            or manifest.plan.get('inviter_account_id') != order.inviter_account_id
            or manifest.plan.get('inviter_id') != order.inviter_id):
        raise ValidationError('逐段订单最终结算凭证待核对')
    from wallet.models import Transaction
    payments = list(order.transactions.filter(tx_type=Transaction.TxType.PAY, status=Transaction.Status.SUCCESS,
                    reversal_of__isnull=True).select_related('wallet'))
    if (len(payments) != 1 or payments[0].amount != -order.amount or payments[0].wallet.account_id != order.customer_account_id
            or payments[0].balance_after != payments[0].balance_before + payments[0].amount or payments[0].reversals.exists()):
        raise ValidationError('主单付款流水待核对')
    refunds = list(order.refund_records.order_by('created_at', 'pk'))
    if any(item.created_at == manifest.finalized_at for item in refunds):
        raise ValidationError('退款与佣金发放时序待核对')
    before_settlement = sum(item.amount for item in refunds if item.created_at < manifest.finalized_at)
    net = order.amount - before_settlement
    rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
    if not 0 <= rate <= 100 or net < 0:
        raise ValidationError('推荐佣金比例或实付净额异常')
    expected_paid = net * rate // 100
    tx = manifest.inviter_transaction
    credits = list(order.transactions.filter(status=Transaction.Status.SUCCESS, reversal_of__isnull=True,
                   tx_type=Transaction.TxType.INCOME, amount__gt=0).select_related('wallet'))
    from .referral_evidence import paid_referral_credits, ReferralEvidenceError
    try:
        paid = paid_referral_credits(order, credits, expected_amount=expected_paid)
    except ReferralEvidenceError as exc:
        raise ValidationError('推荐佣金收款流水待核对') from exc
    if (manifest.plan.get('inviter_income') != expected_paid or order.inviter_commission != expected_paid
            or (expected_paid and (len(paid) != 1 or not tx or paid[0].pk != tx.pk
                or tx.balance_after != tx.balance_before + tx.amount or tx.reversals.exists()))
            or (not expected_paid and (paid or tx))):
        raise ValidationError('已发推荐佣金与逐段清单不一致')
    from .referral_refund_funding import _verified_platform_completion
    used = set()
    for refund in refunds:
        allocation = ReferralRefundFunding.objects.filter(order=order, refund=refund).first()
        if refund.created_at < manifest.finalized_at:
            if allocation:
                raise ValidationError('佣金发放前退款存在异常补资')
            continue
        if refund.amount > net:
            raise ValidationError('历史退款超过主单实付净额')
        required = net * rate // 100 - (net - refund.amount) * rate // 100
        net -= refund.amount
        if not required:
            if allocation:
                raise ValidationError('零佣金承担存在异常补资')
            continue
        if (not allocation or allocation.amount != required or allocation.target_refund_amount != refund.amount
                or hasattr(allocation, 'release')):
            raise ValidationError('历史佣金退款门店承担凭证缺失')
        action = ReferralRefundFundingAction.objects.filter(allocation=allocation).first()
        if (not action or action.actor != allocation.allocated_by or action.request_key != allocation.request_key
                or action.result != {'receipt_id': allocation.receipt_id, 'allocation_id': allocation.pk,
                                     'order_id': order.pk, 'amount': allocation.amount,
                                     'refund_amount': refund.amount}):
            raise ValidationError('历史佣金补资操作异常')
        _verified_platform_completion(allocation, action)
        used.add(allocation.pk)
    if ReferralRefundFunding.objects.filter(order=order, refund__isnull=False).exclude(pk__in=used).exists():
        raise ValidationError('存在无法归属的已发佣金补资')
    if refund_amount > net:
        raise ValidationError('退款超过主单实付净额')
    return net * rate // 100 - (net - refund_amount) * rate // 100
