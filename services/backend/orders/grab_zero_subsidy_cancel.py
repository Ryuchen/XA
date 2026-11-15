"""Prove and record the return of a zero-paid safety-box grab order."""
from stores.db import transaction
from .models import OrderGrabPoolOpeningIntent, OrderPlatformSubsidyShare


def before_cancel(order, state):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('安全箱抢单取消须在订单事务内执行')
    if order.amount != 0 or not order.commission_allocation_pending:
        return None
    # The normal refund obtains this mutex before locking coupon sources.
    from .financial_lock import lock_order_money
    lock_order_money()
    intent = (OrderGrabPoolOpeningIntent.objects.select_for_update().select_related('payment__wallet')
              .filter(order=order, state='OBSERVED').first())
    if (not intent or intent.paid_amount != 0 or state.round.pool.order_id != order.pk
            or intent.payment.order_id != order.pk or intent.payment.wallet.account_id != order.customer_account_id
            or intent.payment.tx_type != 'PAY' or intent.payment.status != 'SUCCESS'
            or order.provider_id or order.provider_account_id or order.providers.exists()
            or order.in_service_at or OrderPlatformSubsidyShare.objects.filter(subsidy__order=order).exists()):
        raise ValueError('零实付安全箱开池或取消凭据待核对')
    from .grab_opening_intent import _verify_zero_subsidy
    _verify_zero_subsidy(order, intent.payment, require_opening=False)
    subsidy = order.platform_subsidy
    return {'opening_intent_id': intent.pk, 'payment_id': intent.payment_id,
            'subsidy_id': subsidy.pk, 'coupon_id': subsidy.coupon_id_snapshot}


def after_cancel(order, refund, facts):
    if facts is None:
        return
    from coupons.models import UserCoupon
    returned = UserCoupon.objects.select_for_update().filter(pk=facts['coupon_id'],
        account_id=order.customer_account_id, business_cycle=order.business_cycle,
        status=UserCoupon.Status.UNUSED, order__isnull=True, used_at__isnull=True).exists()
    if (not refund or refund.order_id != order.pk or refund.amount != 0 or not returned
            or OrderPlatformSubsidyShare.objects.filter(subsidy_id=facts['subsidy_id']).exists()
            or not isinstance(refund.benefit_effects, dict)):
        raise ValueError('零实付安全箱退券结果待核对')
    refund.benefit_effects = {**refund.benefit_effects,
        'zero_subsidy_cancel': {**facts, 'coupon_returned': True}}
    refund.save(update_fields=['benefit_effects'])
