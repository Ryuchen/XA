"""Order-scoped admission for the unfinished production points-refund slice.

This is not an endpoint and does not override the isolated checkout guard.
Callers must hold the same tenant transaction for the subsequent operation.
"""
from django.conf import settings
from rest_framework.exceptions import ValidationError

from stores.context import current_store
from stores.db import transaction
from .models import (Order, OrderOriginalShareTransfer, OrderPointsRedemption, OrderPointsRefund,
                     OrderPrestartHandoff, PointsNegotiationPosting, Reservation)


def enabled():
    """An explicit, default-closed capability; dependencies are independently required."""
    names = ('SERVICE_POINTS_NEGOTIATION_PRODUCTION_ENABLED', 'BOSS_FUNDING_SELECTION_ENABLED',
             'POINTS_USAGE_SWITCHES_ENABLED', 'SERVICE_ORDER_POINTS_RECEIPT_ENABLED',
             'ORIGINAL_ATTRIBUTION_ENABLED', 'SEGMENT_ECONOMICS_CAPTURE_ENABLED',
             'PLATFORM_IDENTITY_ENABLED', 'PLATFORM_PASS_REPAYMENT_ENABLED',
             'NEGOTIATION_PROPOSAL_ENABLED', 'NEGOTIATION_REFUND_EXECUTION_ENABLED')
    return all(getattr(settings, name, False) is True for name in names)


def require_order_locked(order_id, *, phase, refund_equivalent_units=None):
    """Lock and verify the persisted facts for a first, partial main-order refund.

    `before` is used before posting; `after` verifies exactly one posted,
    partial refund. This guard does not replace consent, financial calculation,
    settlement clocks or caller authorization.
    """
    store = current_store()
    connection = transaction.get_connection()
    from .points_business_access import permits
    formal_scope = permits(phase='refund', kind='service') or permits(phase='refund', kind='addon')
    if ((not enabled() and not formal_scope) or not connection.in_atomic_block or store is None
            or store.database_alias != connection.alias or type(order_id) is not int or order_id <= 0
            or phase not in ('before', 'posting', 'after')):
        raise ValidationError('积分协商退款正式路径未启用或门店上下文无效')
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if formal_scope and (not order or not (permits(order, phase='refund', kind='service')
            or permits(order, phase='refund', kind='addon'))):
        raise ValidationError('积分退款上下文与订单不一致')
    if (not order or not order.customer_account_id or order.is_gift_snapshot or order.source_order_id
            or Order.objects.filter(source_order_id=order_id).exists()
            or order.status not in (Order.Status.IN_SERVICE, Order.Status.COMPLETED)
            or not order.in_service_at or (order.addon_intents.filter(payment__isnull=False).exists()
                                           and not permits(order, phase='refund', kind='addon'))
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        raise ValidationError('订单不在已核实的积分协商范围')
    if OrderPrestartHandoff.objects.filter(order=order).exists():
        from .points_service_participation import service_snapshots
        snapshots = service_snapshots(order)
        accounts = [item.segment.provider_account_id for item in snapshots]
        assignments = order.providers.filter(provider_account_id__in=accounts)
        if phase == 'before':
            assignments = assignments.filter(released_at__isnull=True)
        actual = list(assignments.values_list('provider_account_id', flat=True))
        if (len(accounts) != order.required_provider_count or len(set(accounts)) != len(accounts)
                or len(actual) != len(accounts) or set(actual) != set(accounts)):
            raise ValidationError('改派后实际履约席位待核对')
    reservations = list(Reservation.objects.filter(order=order))
    if reservations and (len(reservations) != 1 or reservations[0].status != Reservation.Status.CONVERTED
            or reservations[0].customer_account_id != order.customer_account_id
            or reservations[0].service_id != order.service_id
            or reservations[0].required_provider_count != order.required_provider_count
            or (reservations[0].required_provider_count == 1
                and not order.providers.filter(provider_account_id=reservations[0].provider_account_id).exists())
            or reservations[0].required_provider_count not in (1, 2)):
        raise ValidationError('预约转单付款身份或履约凭证待核对')
    if reservations and reservations[0].required_provider_count == 2:
        from .points_reservation_refund_identity import verify_dual_reservation
        verify_dual_reservation(order)
    from club_accounts.models import ClubAccount
    account = ClubAccount.objects.select_for_update().filter(pk=order.customer_account_id,
        account_type=ClubAccount.AccountType.BOSS, business_cycle=order.business_cycle).first()
    if not account:
        raise ValidationError('订单老板账号或业务周期待核对')
    from .direct_payment_models import DirectPaymentIntent
    from .models import OrderPlatformSubsidy
    if (DirectPaymentIntent.objects.filter(order=order).exists()
            or OrderPlatformSubsidy.objects.filter(order=order).exists()):
        raise ValidationError('订单付款类型不在积分协商范围')
    receipt = OrderPointsRedemption.objects.select_for_update().filter(order=order).first()
    from .points_service_payment import MARKER
    if (not receipt or not isinstance(order.funding_terms, dict)
            or order.funding_terms.get(MARKER) != {'receipt_id': receipt.pk}
            or receipt.deal_units != receipt.paid_units + receipt.discount_units
            or receipt.paid_units != order.amount or receipt.discount_units <= 0):
        raise ValidationError('积分付款凭据待核对')
    refunds = list(order.refund_records.select_for_update().order_by('pk'))
    proofs = list(OrderPointsRefund.objects.select_for_update().filter(redemption=receipt).order_by('pk'))
    postings = list(PointsNegotiationPosting.objects.select_for_update().filter(
        points_refund__redemption=receipt).order_by('pk'))
    if phase == 'before':
        if (type(refund_equivalent_units) is not int or not 0 < refund_equivalent_units < receipt.deal_units
                or order.payment_status != Order.PaymentStatus.PAID or refunds or proofs or postings):
            raise ValidationError('订单已有退款或状态已变化')
    else:
        if (len(refunds) != 1 or len(proofs) != 1 or len(postings) != 1
                or proofs[0].refund_id != refunds[0].pk or postings[0].points_refund_id != proofs[0].pk
                or proofs[0].is_final or not 0 < proofs[0].equivalent_units < receipt.deal_units
                or refund_equivalent_units is not None):
            raise ValidationError('积分部分退款凭证或数量待核对')
        if phase == 'posting':
            if order.payment_status != Order.PaymentStatus.PAID or postings[0].termination_processed_at:
                raise ValidationError('积分退款记账状态待核对')
        elif order.payment_status != Order.PaymentStatus.PARTIALLY_REFUNDED or not postings[0].termination_processed_at:
            raise ValidationError('积分退款结案状态待核对')
    from .points_service_refund import _plan
    _plan(order, None, None, history_only=True)
    return order
