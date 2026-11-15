"""Prove a same-order add-on review batch before a single VIP recalculation."""
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def retry_same_order(account_id, order_id):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('加购批量会员重算须在门店事务内执行')
    from orders.models import Order
    from club_accounts.models import ClubAccount
    from .models import LicenseAddonBenefitsReview, LicenseGiftVipReview, LicenseRefundVipReview
    order = Order.objects.select_for_update().filter(pk=order_id, customer_account_id=account_id).first()
    if not order:
        raise ValidationError('原加购订单或老板身份待核对')
    refund_reviews = list(LicenseRefundVipReview.objects.filter(decision__account_id=account_id,
        decision__business_cycle=order.business_cycle, vip_recalculation_pending=True)
        .order_by('decision_id', 'pk'))
    if any(row.decision.order_id != order.pk for row in refund_reviews):
        raise ValidationError('批次包含其他订单的待核对成长')
    if refund_reviews:
        if order.is_gift_snapshot:
            raise ValidationError('礼物权益不属于服务订单加购批次')
        # The main-order verifier binds its own exact settlement capability
        # when needed. Serialize money before it and all add-on verifiers.
        from orders.financial_lock import lock_existing_order_money
        lock_existing_order_money()
    reviews = list(LicenseAddonBenefitsReview.objects.filter(consumption__account_id=account_id,
        consumption__business_cycle=order.business_cycle, vip_recalculation_pending=True)
        .order_by('consumption_id', 'pk'))
    if not reviews or any(row.consumption.payment.intent.order_id != order.pk for row in reviews):
        raise ValidationError('批次包含其他订单的待核对成长')
    from .license_refund_vip_review import resolve_locked as resolve_refund
    for review in refund_reviews:
        resolve_refund(review.decision_id, request_id=review.request_id, actor_id=review.platform_actor_id,
            outcome=review.outcome, reason=review.reason, _batch_verify=True)
    from .license_addon_review import resolve_locked
    for review in reviews:
        resolve_locked(review.consumption_id, request_id=review.request_id, actor_id=review.platform_actor_id,
            vip_outcome=review.vip_outcome, points_outcome=review.points_outcome, reason=review.reason,
            _batch_verify=True)
    # The individual verifier has locked the wallet and account. Recheck the
    # entire pending set under that lock before granting anything.
    account = ClubAccount.objects.select_for_update().get(pk=account_id)
    if account.account_type != 'BOSS' or account.business_cycle != order.business_cycle:
        raise ValidationError('原老板业务周期已变化，请先核对')
    current_ids = set(LicenseAddonBenefitsReview.objects.filter(consumption__account_id=account_id,
        consumption__business_cycle=account.business_cycle, vip_recalculation_pending=True)
        .values_list('pk', flat=True))
    current_refund_ids = set(LicenseRefundVipReview.objects.filter(decision__account_id=account_id,
        decision__business_cycle=account.business_cycle, vip_recalculation_pending=True)
        .values_list('pk', flat=True))
    if (current_ids != {review.pk for review in reviews}
            or current_refund_ids != {review.pk for review in refund_reviews}
            or LicenseGiftVipReview.objects.filter(decision__account_id=account_id,
                decision__business_cycle=account.business_cycle, vip_recalculation_pending=True).exists()):
        raise ValidationError('同周期待核对会员成长已变化，不能批量重算')
    from .services import update_vip
    update_vip(account)
