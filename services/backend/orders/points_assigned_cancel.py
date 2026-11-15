"""Full return of a verified, assigned points order before service starts.

Called by the existing staff refund transaction; this module does not enable
points checkout, settle a provider, or authorize an aftersale agreement.
"""
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import (Order, OrderOriginalAttribution, OrderOriginalShareTransfer,
                     OrderParticipationSegment, OrderPointsFundingAllocation,
                     OrderPointsRedemption, OrderPointsSettlement, OrderPrestartHandoff, OrderRefund, Reservation)
from .refunds import RefundError, authorized_refund, request_fingerprint


def _verify_locked(order, *, operator_account):
    from .points_business_access import permits
    formal = permits(order, phase='assignment', kind='service')
    if ((not formal and not getattr(settings, 'SERVICE_POINTS_ASSIGNED_CANCEL_ENABLED', False))
            or not transaction.get_connection().in_atomic_block or not authorized_refund(operator_account)):
        raise RefundError('已指派积分订单退款尚未开放')
    from .financial_lock import lock_order_money
    lock_order_money()
    persisted = Order.objects.select_for_update().get(pk=order.pk)
    from engagement.models import ConsumptionConfirmation, PointLedger
    from wallet.models import ProviderReport, Transaction
    from .direct_payment_models import DirectPaymentIntent
    attribution = OrderOriginalAttribution.objects.select_for_update().filter(order=persisted, status='ACTIVE').first()
    if (persisted.status != Order.Status.GRABBED or persisted.payment_status != Order.PaymentStatus.PAID
            or persisted.is_gift_snapshot or persisted.source_order_id or not persisted.customer_account_id
            or persisted.in_service_at or persisted.completed_at or persisted.settled_at
            or persisted.consumption_confirmed_at or persisted.refund_records.exists() or persisted.aftersales.exists()
            or persisted.addon_intents.exists() or Reservation.objects.filter(order=persisted).exists()
            or not attribution or OrderParticipationSegment.objects.filter(attribution=attribution, started_at__isnull=False).exists()
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution=attribution).exists()
            or ProviderReport.objects.filter(order=persisted).exists()
            or ConsumptionConfirmation.objects.filter(order=persisted).exists()
            or PointLedger.objects.filter(order=persisted, event_key=f'order:{persisted.pk}:points').exists()
            or DirectPaymentIntent.objects.filter(order=persisted).exists()
            or OrderPointsSettlement.objects.filter(order=persisted).exists()
            or Transaction.objects.filter(order=persisted, status='SUCCESS',
                tx_type__in=['INCOME', 'GIFT', 'SHOP_INCOME'], amount__gt=0).exists()):
        raise RefundError('订单履约、售后或补资记录需要核对')
    from .points_funding_plan import PointsFundingPlanError, plan_verified_order
    try:
        plan_verified_order(persisted.pk)
    except PointsFundingPlanError as exc:
        raise RefundError('订单付款或陪玩收益快照需要核对') from exc
    receipt = OrderPointsRedemption.objects.select_for_update().filter(order=persisted).first()
    if not receipt:
        raise RefundError('订单积分付款凭据缺失')
    from .points_prestart_cancel_funding import capture_locked, record_locked
    from django.core.exceptions import ValidationError as DjangoValidationError
    try:
        funding_facts = capture_locked(persisted)
    except (ValidationError, DjangoValidationError, PointsFundingPlanError) as exc:
        raise RefundError('订单补资分配或释放凭证待核对') from exc
    return persisted, receipt, funding_facts


def preview(order_id, *, operator_account):
    """Quote a full pre-service cancellation without posting a refund."""
    from contextlib import ExitStack
    with transaction.atomic(), ExitStack() as scopes:
        order = Order.objects.select_for_update().get(pk=order_id)
        if getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True:
            from .points_business_access import service_order
            scopes.enter_context(service_order(order.pk, phase='assignment'))
        persisted, receipt, funding_facts = _verify_locked(order, operator_account=operator_account)
        from club_accounts.models import ClubAccount, LegacyAccountMap
        from engagement.models import MemberProfile
        from wallet.models import Wallet
        account = ClubAccount.objects.select_for_update().filter(pk=persisted.customer_account_id).first()
        wallet = Wallet.objects.select_for_update().filter(account_id=persisted.customer_account_id).first()
        member = MemberProfile.objects.select_for_update().filter(account_id=persisted.customer_account_id).first()
        from .points_service_payment import MARKER
        if (not account or account.business_cycle != persisted.business_cycle or not wallet or not member
                or wallet.pk != receipt.payment.wallet_id or wallet.user_id != persisted.customer_id
                or (persisted.customer_id is not None and not LegacyAccountMap.objects.filter(
                    account_id=account.pk, legacy_user_id=persisted.customer_id).exists())
                or not isinstance(persisted.funding_terms, dict)
                or persisted.funding_terms.get(MARKER) != {'receipt_id': receipt.pk}
                or wallet.balance + receipt.paid_units > 2147483647):
            raise RefundError('退款账号、钱包或积分来源待核对')
        from .points_service_refund import _plan
        try:
            _, payment, sources = _plan(persisted, None, receipt.deal_units)
        except (ValidationError, ObjectDoesNotExist) as exc:
            raise RefundError('积分或店币退款凭据需要核对') from exc
        if (not payment.is_final or payment.coin_units != receipt.paid_units
                or payment.point_hundredths != receipt.points_hundredths):
            raise RefundError('积分退款构成不完整')
        return {'refund_equivalent_units': receipt.deal_units, 'refund_coin_units': payment.coin_units,
                'refund_points_hundredths': payment.point_hundredths, 'prepaid_units': sources.prepaid_units,
                'gift_units': sources.gift_units, 'compensation_units': sources.compensation_units,
                'released_funding_units': sum(row[2] - row[3] for row in funding_facts['allocations'])
                    if funding_facts else 0,
                'full_refund': True, 'test_only': not getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False)}


def refund_locked(order, *, reason, operator, operator_account, request_id):
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 64:
        raise RefundError('退款操作凭证须为1至64字字符串', code='REFUND_INVALID_REQUEST')
    persisted, receipt, funding_facts = _verify_locked(order, operator_account=operator_account)
    from .points_prestart_cancel_funding import record_locked
    from django.core.exceptions import ValidationError as DjangoValidationError
    refund = OrderRefund.objects.create(order=persisted, amount=receipt.paid_units, reason=reason,
        operator=operator, operator_account=operator_account, client_request_id=request_id,
        request_fingerprint=request_fingerprint(receipt.paid_units, reason))
    from .points_service_refund import refund_assets_locked
    try:
        result = refund_assets_locked(order_id=persisted.pk, refund_id=refund.pk, equivalent_units=receipt.deal_units)
    except (ValidationError, ObjectDoesNotExist) as exc:
        raise RefundError('积分或店币退款凭据需要核对') from exc
    if not result.is_final or result.coin_units != persisted.amount or result.points_hundredths != receipt.points_hundredths:
        raise RefundError('积分退款构成不完整')
    from coupons.benefits import restore_order_coupons
    from coupons.models import UserCoupon
    coupons = list(UserCoupon.objects.select_for_update().filter(order_id=persisted.pk, status=UserCoupon.Status.USED)
                   .select_related('coupon').order_by('pk'))
    restore_order_coupons(persisted)
    refund.benefit_effects = {'order_coupon_results': [
        {'name': row.coupon.name, 'outcome': 'RETURNED' if row.restore_on_refund else 'NOT_RETURNED'} for row in coupons],
        'points_assigned_prestart_cancel': True, 'points_prestart_cancel_funding': bool(funding_facts)}
    refund.save(update_fields=['benefit_effects'])
    if funding_facts:
        try:
            record_locked(result, funding_facts)
        except (ValidationError, DjangoValidationError) as exc:
            raise RefundError('订单补资释放凭证待核对') from exc
    from .original_attribution import mark_review
    mark_review(persisted, '取消退款后的个人原价份额尚未核对')
    order.payment_status = Order.PaymentStatus.REFUNDED
    order.refunded_at = timezone.now()
    from wallet.notifier import notify_wallet_update
    if result.coin_transaction_id:
        transaction.on_commit(lambda: notify_wallet_update(result.coin_transaction.wallet))
    return refund
