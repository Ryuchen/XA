"""Resolve UNKNOWN gift VIP growth without replaying a payment."""
from django.db.models import Sum
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .models import ConsumptionConfirmation, LicenseGiftVipDecision, LicenseGiftVipReview


def resolve_locked(decision_id, *, request_id, actor_id, outcome, reason):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('礼物会员核对须在门店事务内执行')
    from orders.models import Order
    initial = LicenseGiftVipDecision.objects.filter(pk=decision_id).first()
    if not initial:
        raise ValidationError('待核对记录不存在')
    order = Order.objects.select_for_update().get(pk=initial.order_id)
    decision = LicenseGiftVipDecision.objects.select_for_update().get(pk=decision_id)
    existing = LicenseGiftVipReview.objects.select_for_update().filter(decision=decision).first()
    if existing:
        if existing.request_id != request_id or existing.outcome != outcome or existing.reason != reason:
            raise ValidationError('该记录已核对，请查看原处理结果')
        if existing.vip_recalculation_pending:
            if existing.outcome != 'ALLOW' or not existing.vip_applied:
                raise ValidationError('礼物会员审核与重算状态不一致')
            from .license_points_review import _verify_award_sources
            _verify_award_sources(order)
            refunds = order.refund_records.aggregate(total=Sum('amount'))['total'] or 0
            confirmation = ConsumptionConfirmation.objects.select_for_update().filter(order=order).first()
            if (not confirmation or confirmation.account_id != decision.account_id
                    or confirmation.business_cycle != decision.business_cycle
                    or decision.account_id != order.customer_account_id
                    or decision.business_cycle != order.business_cycle
                    or confirmation.amount != decision.paid_units or decision.paid_units != order.amount
                    or confirmation.confirmed_at != decision.decided_at
                    or confirmation.refunded_amount != refunds
                    or bool(confirmation.reversed_at) != (refunds == decision.paid_units)):
                raise ValidationError('礼物会员实销或当前退款记录待核对')
            from wallet.services import get_wallet
            if not get_wallet(account_id=decision.account_id, for_update=True, create=False):
                raise ValidationError('原老板钱包不存在，业务周期待核对')
            from club_accounts.models import ClubAccount
            account = ClubAccount.objects.select_for_update().get(pk=decision.account_id)
            if account.account_type != 'BOSS' or account.business_cycle != decision.business_cycle:
                raise ValidationError('原老板业务周期已变化，请先核对')
            from .license_vip_pending import is_only_pending
            if not is_only_pending(account.pk, account.business_cycle, kind='gift', review_id=existing.pk):
                raise ValidationError('同周期存在多笔待核对会员成长，不能单笔重算')
            from .services import update_vip
            update_vip(account)
            existing.refresh_from_db(fields=['vip_recalculation_pending'])
        return existing
    if LicenseGiftVipReview.objects.filter(request_id=request_id).exists():
        raise ValidationError('请求编号已用于其他核对')
    if (not order.is_gift_snapshot or not order.completed_at or not order.settled_at
            or order.payment_status not in ('PAID', 'PARTIALLY_REFUNDED', 'REFUNDED')
            or order.gift_vip_enabled_snapshot is not True
            or not isinstance(order.gift_points_snapshot, dict)
            or order.gift_points_snapshot.get('vip_enabled') is not True
            or decision.account_id != order.customer_account_id or decision.business_cycle != order.business_cycle
            or decision.paid_units != order.amount or decision.paid_units <= 0
            or not isinstance(decision.license_evidence, dict)
            or decision.license_evidence.get('status') not in ('CONTROL_UNAVAILABLE', 'LICENSE_CONFIGURATION_INVALID', 'STORE_CONTEXT_UNAVAILABLE')):
        raise ValidationError('原礼物会员授权或付款快照不完整')
    # Use the same proof as reviewed consumption points, including every
    # actual buyer debit/refund and each original principal allocation.
    from .license_points_review import _verify_award_sources
    _verify_award_sources(order)
    refunds = order.refund_records.aggregate(total=Sum('amount'))['total'] or 0
    if ConsumptionConfirmation.objects.filter(order=order).exists():
        raise ValidationError('礼物已存在会员成长记录，请先核对')
    from wallet.services import get_wallet
    get_wallet(account_id=decision.account_id, for_update=True)
    from club_accounts.models import ClubAccount
    account = ClubAccount.objects.select_for_update().get(pk=decision.account_id)
    applied, pending, skip = False, False, ''
    if outcome == 'ALLOW':
        if account.business_cycle != decision.business_cycle or not account.license_membership_active:
            skip = 'CYCLE_CHANGED'
        elif refunds == decision.paid_units:
            skip = 'FULLY_REFUNDED'
        else:
            ConsumptionConfirmation.objects.create(order=order, account=account, business_cycle=decision.business_cycle,
                amount=decision.paid_units, refunded_amount=refunds, confirmed_at=decision.decided_at)
            applied, pending = True, True
    review = LicenseGiftVipReview.objects.create(decision=decision, request_id=request_id,
        platform_actor_id=actor_id, outcome=outcome, reason=reason, vip_applied=applied,
        vip_recalculation_pending=pending, refunded_units=refunds, skip_reason=skip)
    if pending:
        from .license_vip_pending import is_only_pending
        if is_only_pending(account.pk, account.business_cycle, kind='gift', review_id=review.pk):
            from .services import update_vip
            update_vip(account)
        review.refresh_from_db(fields=['vip_recalculation_pending'])
    return review
