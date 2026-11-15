"""Prove and terminate unpaid safety-box shares before any service starts."""
from stores.db import transaction
from .models import Order, OrderPlatformSubsidy, OrderPlatformSubsidyShare
from .refunds import RefundError


def capture_locked(order):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('安全箱服务前取消须在订单事务内执行')
    from .financial_lock import lock_order_money
    lock_order_money()
    from coupons.models import Coupon, UserCoupon
    from wallet.models import Transaction
    from .models import OrderOriginalAttribution, OrderSegmentFinancialCoverage
    subsidy = OrderPlatformSubsidy.objects.select_for_update().filter(order=order).first()
    if (order.status != Order.Status.GRABBED or order.payment_status != Order.PaymentStatus.PAID
            or order.amount != 0 or order.in_service_at or order.settled_at or not subsidy
            or not order.customer_account_id or not order.provider_account_id
            or order.refund_records.exists() or order.aftersales.filter(status='OPEN').exists()):
        raise RefundError('安全箱取消的订单或售后状态待核对')
    coupon_id = UserCoupon.objects.filter(pk=subsidy.coupon_id_snapshot).values_list('coupon_id', flat=True).first()
    if coupon_id:
        Coupon.objects.select_for_update().filter(pk=coupon_id).first()
    owned = UserCoupon.objects.select_for_update().select_related('coupon').filter(
        pk=subsidy.coupon_id_snapshot, account_id=order.customer_account_id, business_cycle=order.business_cycle,
        order=order, status=UserCoupon.Status.USED, restore_on_refund=True).first()
    payments = list(Transaction.objects.select_for_update().select_related('wallet').filter(order=order,
        tx_type='PAY', status='SUCCESS').order_by('pk')[:2])
    attribution = OrderOriginalAttribution.objects.select_for_update().filter(order=order).first()
    coverage = OrderSegmentFinancialCoverage.objects.select_for_update().filter(attribution=attribution).first() if attribution else None
    shares = list(OrderPlatformSubsidyShare.objects.select_for_update().filter(subsidy=subsidy)
                  .select_related('assignment').order_by('pk'))
    assignments = list(order.providers.select_for_update().order_by('pk'))
    if (not owned or owned.coupon.scope != 'SAFE_BOX' or owned.coupon.discount_type != 'RATE'
            or not owned.used_at or owned.coupon.discount_rate != 0 or not (owned.coupon.grant_key or '').startswith(
                f'vip-safe:{order.customer_account_id}:{order.business_cycle}:')
            or subsidy.basis != order.original_amount or subsidy.basis <= 0
            or len(payments) != 1 or payments[0].wallet.account_id != order.customer_account_id
            or payments[0].amount != 0 or payments[0].balance_before != payments[0].balance_after
            or payments[0].reversal_of_id or payments[0].reversals.exists()
            or not attribution or not coverage or coverage.source != 'PLATFORM_SUBSIDY'
            or {s.assignment_id for s in shares} != {a.pk for a in assignments}
            or any(s.paid_at or s.income_transaction_id or s.expense_transaction_id or s.retained_amount
                   or s.assignment.settled_at for s in shares)
            or attribution.segments.filter(started_at__isnull=False).exists()):
        raise RefundError('安全箱原付款、券或未履约补贴凭证待核对')
    from .subsidy_segment_capture import verify_entry_capture
    from .settlement import SettlementError
    try:
        verify_entry_capture(order, shares)
    except SettlementError as exc:
        raise RefundError('安全箱本人抽成或补贴分段凭证待核对') from exc
    if not shares or sum(s.assignment.released_at is None for s in shares) != 1:
        raise RefundError('安全箱陪玩份额待核对')
    active = next(s for s in shares if s.assignment.released_at is None)
    if (active.assignment.provider_account_id != order.provider_account_id
            or active.assignment.provider_id != order.provider_id):
        raise RefundError('安全箱当前陪玩身份待核对')
    return {'subsidy_id': subsidy.pk, 'coupon_id': owned.pk, 'payment_id': payments[0].pk,
            'provider_cycle': active.assignment.provider_business_cycle,
            'shares': [{'id': s.pk, 'amount': s.amount} for s in shares]}


def finish_locked(order, refund, facts):
    from coupons.models import UserCoupon
    if (not refund or refund.order_id != order.pk or refund.amount != 0
            or not UserCoupon.objects.filter(pk=facts['coupon_id'], account_id=order.customer_account_id,
                business_cycle=order.business_cycle, status=UserCoupon.Status.UNUSED,
                order__isnull=True, used_at__isnull=True).exists()):
        raise RefundError('安全箱零额取消或原券返还结果待核对')
    shares = list(OrderPlatformSubsidyShare.objects.select_for_update().filter(subsidy_id=facts['subsidy_id']).order_by('pk'))
    if ([{'id': s.pk, 'amount': s.amount} for s in shares] != facts['shares']
            or any(s.paid_at or s.income_transaction_id or s.expense_transaction_id or s.retained_amount for s in shares)):
        raise RefundError('安全箱未支付补贴份额已变化')
    for share in shares:
        share.amount = 0
        share.save(update_fields=['amount'])
    refund.benefit_effects = {**refund.benefit_effects,
        'safety_box_prestart_cancel': {**facts, 'coupon_returned': True}}
    refund.save(update_fields=['benefit_effects'])
