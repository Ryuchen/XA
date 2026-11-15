"""Consent terms for early termination of a free safety-box service."""
from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import Order, OrderPlatformSubsidy


def applies(order):
    return OrderPlatformSubsidy.objects.filter(order=order).exists()


def enabled():
    return bool(getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False)
                and getattr(settings, 'VIP_SAFE_BOX_NEGOTIATION_ENABLED', False))


def verify_coupon(order):
    from coupons.models import UserCoupon
    subsidy = OrderPlatformSubsidy.objects.filter(order=order).first()
    coupon = (UserCoupon.objects.select_related('coupon').filter(pk=subsidy.coupon_id_snapshot).first()
              if subsidy else None)
    prefix = f'vip-safe:{order.customer_account_id}:{order.business_cycle}:'
    if (not coupon or coupon.order_id != order.pk or coupon.account_id != order.customer_account_id
            or coupon.business_cycle != order.business_cycle or coupon.status != UserCoupon.Status.USED
            or not coupon.used_at or coupon.coupon.scope != 'SAFE_BOX'
            or not order.in_service_at or coupon.used_at < order.created_at or coupon.used_at > order.in_service_at
            or coupon.coupon.discount_type != 'RATE' or coupon.coupon.discount_rate != 0
            or not (coupon.coupon.grant_key or '').startswith(prefix)):
        raise ValidationError('安全箱资格券及使用记录待核对')
    return coupon


def calculate(order, case, scope, refund_units, requested_terms):
    """Prove the original subsidy and bind each proposed retained amount."""
    if not enabled():
        raise ValidationError('安全箱补贴协商尚未开放')
    from .subsidy_execution import table_available
    if not table_available():
        raise ValidationError('安全箱协商记录尚未就绪')
    from .models import SubsidyAgreementExecution
    if SubsidyAgreementExecution.objects.filter(order=order).exists():
        raise ValidationError('安全箱追加协商尚未接通，请先人工核对')
    if (refund_units != 0 or order.amount != 0 or order.payment_status != Order.PaymentStatus.PAID
            or order.status not in (Order.Status.IN_SERVICE, Order.Status.COMPLETED)
            or not order.in_service_at or order.settled_at or order.refund_records.exists()
            or order.in_service_at < order.created_at or order.in_service_at > timezone.now()
            or not order.customer_account_id or order.business_cycle != order.customer_account.business_cycle
            or case.status != 'OPEN' or not case.created_at or not order.in_service_at <= case.created_at <= timezone.now()
            or case.order_id != order.pk or case.account_id != order.customer_account_id):
        raise ValidationError('安全箱订单或售后状态已变化，请重新核对')
    if (order.service_clock_version is None or order.service_aftersale_hours is None
            or order.service_freeze_hours is None or order.service_deposit_percent is None
            or not 0 <= order.service_deposit_percent <= 100):
        raise ValidationError('订单结算时限或押金比例快照待核对')
    from .models import OrderRoom, OrderRoomObjection
    room = OrderRoom.objects.filter(order=order).first()
    if (order.room_required_snapshot is True) != bool(room):
        raise ValidationError('房间及订单关联待核对')
    if (room and order.status == Order.Status.COMPLETED
            and OrderRoomObjection.objects.filter(room=room, status='OPEN').exists()):
        raise ValidationError('已完成订单仍有房间异议，请先核对')
    verify_coupon(order)
    from .direct_payment_models import DirectPaymentIntent
    from .segment_financial_capture import transfer_settlement_pending
    if DirectPaymentIntent.objects.filter(order=order).exists() or transfer_settlement_pending(order):
        raise ValidationError('该订单的支付或转单凭证尚需核对')
    from wallet.models import Transaction
    payments = list(Transaction.objects.filter(order=order, tx_type='PAY', status='SUCCESS').select_related('wallet'))
    if (len(payments) != 1 or payments[0].amount != 0 or payments[0].wallet.account_id != order.customer_account_id
            or payments[0].wallet.user_id != order.customer_id or payments[0].balance_after != payments[0].balance_before
            or payments[0].reversal_of_id or payments[0].reversals.exists()):
        raise ValidationError('免费安全箱付款凭证待核对')
    from .settlement import SettlementError
    from .subsidies import _locked_subsidy_shares
    try:
        rows = _locked_subsidy_shares(order)
    except SettlementError as error:
        raise ValidationError(str(error)) from error
    if any(row.paid_at for row in rows):
        raise ValidationError('已发生的安全箱补贴须先人工核对')
    if any(row.retained_amount > row.amount for row in rows):
        raise ValidationError('历史安全箱转单补贴份额待核对')
    by_account = {row.assignment.provider_account_id: row for row in rows}
    if len(by_account) != len(rows) or not set(scope).issubset(by_account):
        raise ValidationError('受影响陪玩或补贴份额已变化')
    requested = {item['account_id']: item['retained_units'] for item in requested_terms}
    if set(requested) != set(scope):
        raise ValidationError('请逐项核定受影响陪玩的报酬')
    result = []
    for account_id in scope:
        original = by_account[account_id].amount
        retained = requested[account_id]
        if retained > original:
            raise ValidationError('核定补贴不能超过陪玩原本预计应得的补贴')
        result.append({'account_id':account_id, 'original_income_units':original,
                       'retained_units':retained, 'burden_units':original-retained,
                       'additional_units':0})
    return {'refund_units':0, 'provider_terms':result, 'rounding_to_store_units':0}
