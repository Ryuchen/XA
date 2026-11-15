"""Staff full refund for an unstarted points-paid reservation in test DBs.

Store-funding allocations require separately verified release evidence.
"""

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError as DjangoValidationError
from django.db import DatabaseError
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import (Order, OrderOriginalAttribution, OrderOriginalShareTransfer, OrderParticipationSegment,
                     OrderPointsFundingAllocation, OrderPointsRedemption, OrderPointsSettlement, OrderRefund, Reservation)
from .points_checkout_test import assignment_enabled, require_isolated_checkout
from .refunds import RefundError, authorized_refund, request_fingerprint


def _verify_locked(order, *, operator_account, preview=False):
    from .points_business_access import permits
    formal = permits(order, phase='assignment', kind='service')
    if not formal:
        try:
            require_isolated_checkout()
        except ValidationError as exc:
            raise RefundError('预约积分订单取消退款尚未开放') from exc
    if ((not formal and not getattr(settings, 'SERVICE_POINTS_RESERVATION_CANCEL_TESTING', False))
            or not assignment_enabled() or not transaction.get_connection().in_atomic_block
            or not authorized_refund(operator_account)):
        raise RefundError('预约积分订单取消退款尚未开放')
    persisted = Order.objects.select_for_update().get(pk=order.pk)
    booking = Reservation.objects.select_for_update().filter(order=persisted, status=Reservation.Status.CONVERTED).first()
    if (not booking or persisted.status != Order.Status.GRABBED or persisted.payment_status != Order.PaymentStatus.PAID
            or booking.required_provider_count != persisted.required_provider_count
            or persisted.in_service_at or persisted.completed_at or persisted.settled_at
            or persisted.consumption_confirmed_at or persisted.refund_records.exists() or persisted.aftersales.exists()
            or persisted.addon_intents.exists()):
        raise RefundError('预约订单状态或售后记录需要核对')
    from engagement.models import ConsumptionConfirmation, PointLedger
    from wallet.models import ProviderReport, Transaction
    from .direct_payment_models import DirectPaymentIntent
    from .grab_pool_models import OrderGrabPoolSnapshot
    attribution = OrderOriginalAttribution.objects.select_for_update().filter(order=persisted, status='ACTIVE').first()
    if (not attribution or OrderParticipationSegment.objects.filter(attribution=attribution, started_at__isnull=False).exists()
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution=attribution).exists()
            or ProviderReport.objects.filter(order=persisted).exists()
            or ConsumptionConfirmation.objects.filter(order=persisted).exists()
            or PointLedger.objects.filter(order=persisted, event_key=f'order:{persisted.pk}:points').exists()
            or DirectPaymentIntent.objects.filter(order=persisted).exists()
            or OrderGrabPoolSnapshot.objects.filter(order=persisted).exists()
            or OrderPointsSettlement.objects.filter(order=persisted).exists()
            or Transaction.objects.filter(order=persisted, status='SUCCESS',
                tx_type__in=['INCOME', 'GIFT', 'SHOP_INCOME'], amount__gt=0).exists()):
        raise RefundError('预约履约、收益或实销记录需要核对')
    has_funding = OrderPointsFundingAllocation.objects.filter(Q(redemption__order=persisted) | Q(service_order=persisted)).exists()
    from .financial_lock import lock_order_money
    from .points_funding_plan import PointsFundingPlanError, plan_verified_order
    if not preview or (formal and has_funding):
        lock_order_money()
    try:
        plan_verified_order(persisted.pk)
    except PointsFundingPlanError as exc:
        raise RefundError('预约付款或陪玩收益快照需要核对') from exc
    receipt = OrderPointsRedemption.objects.select_for_update().filter(order=persisted).first()
    if not receipt:
        raise RefundError('预约积分付款凭据缺失')
    funding_facts = None
    if has_funding:
        if formal:
            from .points_prestart_cancel_funding import capture_locked
        else:
            from .reservation_cancel_funding_test import capture_locked
        try:
            funding_facts = capture_locked(persisted) if formal else capture_locked(persisted, booking)
        except (ValidationError, DjangoValidationError, DatabaseError) as exc:
            raise RefundError('预约补资分配或释放凭证待核对') from exc
    from .points_service_refund import _plan
    try:
        _, payment, sources = _plan(persisted, None, receipt.deal_units)
    except (ValidationError, ObjectDoesNotExist) as exc:
        raise RefundError('预约积分或店币退款凭据需要核对') from exc
    return persisted, receipt, payment, sources, funding_facts


def preview(order_id, *, operator_account):
    from contextlib import ExitStack
    with transaction.atomic(), ExitStack() as scopes:
        order = Order.objects.select_for_update().get(pk=order_id)
        if getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True:
            from .points_business_access import service_order
            scopes.enter_context(service_order(order.pk, phase='assignment'))
        _, receipt, payment, sources, funding_facts = _verify_locked(order, operator_account=operator_account, preview=True)
        return {'refund_equivalent_units': receipt.deal_units, 'refund_coin_units': payment.coin_units,
                'refund_points_hundredths': payment.point_hundredths, 'prepaid_units': sources.prepaid_units,
                'gift_units': sources.gift_units, 'compensation_units': sources.compensation_units,
                'released_funding_units': sum(row[2] - row[3] if len(row) == 4 else row[2]
                                              for row in funding_facts['allocations']) if funding_facts else 0,
                'full_refund': True, 'test_only': not getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False)}


def refund_locked(order, *, reason, operator, operator_account, request_id):
    """Return full original payment sources; caller owns the order transaction."""
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 64:
        raise RefundError('退款操作凭证须为1至64字字符串', code='REFUND_INVALID_REQUEST')
    persisted, receipt, payment, _, funding_facts = _verify_locked(order, operator_account=operator_account)
    if not payment.is_final:
        raise RefundError('预约退款无法确认已全额返回')
    refund = OrderRefund.objects.create(order=persisted, amount=receipt.paid_units, reason=reason,
        operator=operator, operator_account=operator_account, client_request_id=request_id,
        request_fingerprint=request_fingerprint(receipt.paid_units, reason))
    from .points_service_refund import refund_assets_locked
    try:
        result = refund_assets_locked(order_id=persisted.pk, refund_id=refund.pk, equivalent_units=receipt.deal_units)
    except (ValidationError, ObjectDoesNotExist) as exc:
        raise RefundError('预约积分或店币退款凭据需要核对') from exc
    if not result.is_final or result.coin_units != persisted.amount or result.points_hundredths != receipt.points_hundredths:
        raise RefundError('预约积分退款构成不完整')
    from coupons.benefits import restore_order_coupons
    from coupons.models import UserCoupon
    coupons = list(UserCoupon.objects.select_for_update().filter(order=persisted, status=UserCoupon.Status.USED)
                   .select_related('coupon').order_by('pk'))
    restore_order_coupons(persisted)
    refund.benefit_effects = {'order_coupon_results': [
        {'name': row.coupon.name, 'outcome': 'RETURNED' if row.restore_on_refund else 'NOT_RETURNED'} for row in coupons],
        'reservation_points_prestart_cancel': True,
        'points_prestart_cancel_funding': bool(funding_facts and any(len(row) == 4 for row in funding_facts['allocations']))}
    refund.save(update_fields=['benefit_effects'])
    if funding_facts:
        if refund.benefit_effects['points_prestart_cancel_funding']:
            from .points_prestart_cancel_funding import record_locked
        else:
            from .reservation_cancel_funding_test import record_locked
        try:
            evidence = record_locked(result, funding_facts)
        except (ValidationError, DjangoValidationError, DatabaseError) as exc:
            raise RefundError('预约补资释放凭证写入失败') from exc
        if not refund.benefit_effects['points_prestart_cancel_funding']:
            order._reservation_cancel_funding_evidence_id = evidence.pk
    from .original_attribution import mark_review
    mark_review(persisted, '预约全额退款后的个人原价份额尚未核对')
    order.payment_status = Order.PaymentStatus.REFUNDED
    order.refunded_at = timezone.now()
    from wallet.notifier import notify_wallet_update
    if result.coin_transaction_id:
        transaction.on_commit(lambda: notify_wallet_update(result.coin_transaction.wallet))
    return refund
