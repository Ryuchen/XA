"""Test-only WeChat refund domain; no public endpoint or network calls.

This staged slice only reserves a full refund for an unassigned order or a
late payment already held for review. Settled/partial refunds, entitlement
reversal, delivery and reconciliation must be completed before enabling direct
payments. A network timeout must retain the same merchant refund number.
"""
import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone

from .direct_payment import _require_test_domain
from .direct_payment_models import DirectPaymentIntent
from .direct_refund_models import DirectPaymentRefund
from .models import Order, OrderStatusLog
from .state_machine import transition


class DirectRefundError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedRefund:
    """Construct only from an authenticated provider response or notification."""
    mchid: str
    merchant_order_no: str
    transaction_id: str
    merchant_refund_no: str
    channel_refund_id: str
    amount_fen: int
    total_fen: int
    currency: str
    payer_total_fen: int
    payer_refund_fen: int
    settlement_total_fen: int
    settlement_refund_fen: int
    discount_refund_fen: int
    status: str
    success_at: datetime | None = None


def reserve_full_refund(order_id, *, request_id, reason):
    """Atomically stop a pending order and reserve one stable refund number.

    No provider call is performed inside the transaction. UNKNOWN, PROCESSING
    and ABNORMAL remain reserved; only a verified CLOSED response permits a
    new refund number. This function is not an authorization boundary.
    """
    _require_test_domain()
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}', request_id):
        raise DirectRefundError('退款请求编号无效')
    if not isinstance(reason, str) or not reason.strip() or len(reason.encode('utf-8')) > 80:
        raise DirectRefundError('退款原因须为不超过 80 字节的非空文字')
    reason = reason.strip()
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        intent = DirectPaymentIntent.objects.select_for_update().get(order=order)
        from .direct_payment_quote_models import DirectPaymentQuote
        snapshot = DirectPaymentQuote.objects.filter(intent=intent).first()
        if snapshot and snapshot.fee_fen:
            raise DirectRefundError('手续费退款规则尚未确定，请人工核对')
        from .direct_payment_quote import quote_matches_intent
        existing = DirectPaymentRefund.objects.filter(intent=intent, request_id=request_id).first()
        if existing:
            if existing.reason != reason or existing.amount_fen != intent.amount_fen:
                raise DirectRefundError('同一退款请求编号不能用于不同内容')
            return existing
        if (intent.account_id != order.customer_account_id or not quote_matches_intent(intent, order)
                or intent.status not in (DirectPaymentIntent.Status.PAID, DirectPaymentIntent.Status.REVIEW)
                or not intent.transaction_id or not intent.paid_at):
            raise DirectRefundError('未确认原微信支付，不能申请退款')
        if intent.channel_refunds.exclude(status=DirectPaymentRefund.Status.CLOSED).exists():
            raise DirectRefundError('已有退款待处理或已成功，请先核对原退款')
        if intent.status == DirectPaymentIntent.Status.PAID:
            if (order.payment_status != Order.PaymentStatus.PAID
                    or order.provider_id or order.provider_account_id or order.providers.exists()):
                raise DirectRefundError('此阶段仅支持未指派的已支付订单退款')
            if order.status == Order.Status.PENDING:
                def cancel(current):
                    current.cancel_reason = reason
                    current.auto_cancel_at = None
                order = transition(order.pk, Order.Status.CANCELLED, action=OrderStatusLog.Action.REFUND,
                                   reason=reason, side_effect=cancel,
                                   update_fields=['cancel_reason', 'auto_cancel_at'])
            elif order.status != Order.Status.CANCELLED or not intent.channel_refunds.filter(status=DirectPaymentRefund.Status.CLOSED).exists():
                raise DirectRefundError('订单状态与已关闭退款不一致，请人工核对')
        elif order.status != Order.Status.CANCELLED or order.payment_status != Order.PaymentStatus.UNPAID:
            raise DirectRefundError('迟到付款的订单状态不一致，请人工核对')
        return DirectPaymentRefund.objects.create(
            intent=intent, request_id=request_id, merchant_refund_no='XR' + uuid.uuid4().hex,
            amount_fen=intent.amount_fen, total_fen=intent.amount_fen, reason=reason,
        )


def mark_refund_unknown(order_id, refund_id):
    """Record an unknown transport outcome without consuming a new number."""
    _require_test_domain()
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        intent = DirectPaymentIntent.objects.select_for_update().get(order=order)
        refund = DirectPaymentRefund.objects.select_for_update().get(pk=refund_id, intent=intent)
        if refund.status == DirectPaymentRefund.Status.RESERVED:
            refund.status = DirectPaymentRefund.Status.UNKNOWN
            refund.save(update_fields=['status', 'updated_at'])
        return refund


def _record_terminal_conflict(intent, refund, evidence):
    """Keep contradictory authenticated evidence for reconciliation."""
    refund.conflict_previous_status = refund.status
    refund.conflict_status = evidence.status
    refund.conflict_channel_refund_id = evidence.channel_refund_id
    refund.conflict_success_at = evidence.success_at
    refund.conflict_payer_total_fen = evidence.payer_total_fen
    refund.conflict_payer_refund_fen = evidence.payer_refund_fen
    refund.conflict_settlement_total_fen = evidence.settlement_total_fen
    refund.conflict_settlement_refund_fen = evidence.settlement_refund_fen
    refund.conflict_discount_refund_fen = evidence.discount_refund_fen
    refund.conflict_observed_at = timezone.now()
    refund.status = DirectPaymentRefund.Status.REVIEW
    refund.save(update_fields=['conflict_previous_status', 'conflict_status', 'conflict_channel_refund_id', 'conflict_success_at',
                               'conflict_payer_total_fen', 'conflict_payer_refund_fen',
                               'conflict_settlement_total_fen', 'conflict_settlement_refund_fen',
                               'conflict_discount_refund_fen',
                               'conflict_observed_at', 'status', 'updated_at'])
    intent.status = DirectPaymentIntent.Status.REVIEW
    intent.review_reason = '渠道退款终态冲突，停止自动退款并人工核对'
    intent.save(update_fields=['status', 'review_reason', 'updated_at'])
    return refund


def apply_verified_refund(order_id, refund_id, evidence):
    """Persist authenticated channel outcome without touching wallet balance."""
    _require_test_domain()
    if (not isinstance(evidence, VerifiedRefund) or type(evidence.amount_fen) is not int
            or type(evidence.total_fen) is not int or not isinstance(evidence.channel_refund_id, str)
            or type(evidence.payer_total_fen) is not int or type(evidence.payer_refund_fen) is not int
            or not 0 <= evidence.payer_refund_fen <= evidence.payer_total_fen <= evidence.total_fen
            or evidence.payer_refund_fen > evidence.amount_fen
            or type(evidence.settlement_total_fen) is not int or type(evidence.settlement_refund_fen) is not int
            or type(evidence.discount_refund_fen) is not int
            or not 0 <= evidence.settlement_total_fen <= evidence.total_fen
            or not 0 <= evidence.settlement_refund_fen <= evidence.amount_fen
            or not 0 <= evidence.discount_refund_fen <= evidence.amount_fen
            or not evidence.channel_refund_id.strip() or evidence.channel_refund_id != evidence.channel_refund_id.strip()
            or len(evidence.channel_refund_id) > 64
            or evidence.status not in ('PROCESSING', 'SUCCESS', 'CLOSED', 'ABNORMAL')
            or (evidence.status == 'SUCCESS' and (not isinstance(evidence.success_at, datetime)
                or timezone.is_naive(evidence.success_at)))
            or (evidence.status != 'SUCCESS' and evidence.success_at is not None)):
        raise DirectRefundError('渠道退款凭证无效')
    try:
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order_id)
            intent = DirectPaymentIntent.objects.select_for_update().get(order=order)
            refund = DirectPaymentRefund.objects.select_for_update().get(pk=refund_id, intent=intent)
            from .direct_payment_quote import quote_matches_intent
            if (intent.account_id != order.customer_account_id or not quote_matches_intent(intent, order)
                    or evidence.mchid != intent.mchid or evidence.merchant_order_no != intent.merchant_order_no
                    or evidence.transaction_id != intent.transaction_id
                    or evidence.merchant_refund_no != refund.merchant_refund_no
                    or evidence.amount_fen != refund.amount_fen or evidence.total_fen != refund.total_fen
                    or refund.total_fen != intent.amount_fen
                    or (intent.payer_total_fen is not None and evidence.payer_total_fen != intent.payer_total_fen)
                    or (intent.payer_currency is not None and intent.payer_currency != 'CNY')
                    or evidence.currency != refund.currency or evidence.currency != intent.currency):
                raise DirectRefundError('渠道退款凭证与原交易不一致')
            if refund.status == DirectPaymentRefund.Status.REVIEW:
                return refund  # Keep the original conflict until manual reconciliation.
            if refund.channel_refund_id and refund.channel_refund_id != evidence.channel_refund_id:
                return _record_terminal_conflict(intent, refund, evidence)
            if refund.status == DirectPaymentRefund.Status.SUCCESS:
                if evidence.status == 'PROCESSING':
                    return refund  # Delayed non-terminal notice must not roll back success.
                if (evidence.status != 'SUCCESS' or evidence.success_at != refund.success_at
                        or evidence.payer_total_fen != refund.payer_total_fen
                        or evidence.payer_refund_fen != refund.payer_refund_fen
                        or evidence.settlement_total_fen != refund.settlement_total_fen
                        or evidence.settlement_refund_fen != refund.settlement_refund_fen
                        or evidence.discount_refund_fen != refund.discount_refund_fen):
                    return _record_terminal_conflict(intent, refund, evidence)
                return refund
            if refund.status in (DirectPaymentRefund.Status.CLOSED, DirectPaymentRefund.Status.ABNORMAL) and evidence.status == 'PROCESSING':
                return refund
            if refund.status == DirectPaymentRefund.Status.CLOSED and evidence.status != 'CLOSED':
                return _record_terminal_conflict(intent, refund, evidence)
            if refund.status == DirectPaymentRefund.Status.ABNORMAL and evidence.status not in ('ABNORMAL', 'SUCCESS'):
                return _record_terminal_conflict(intent, refund, evidence)
            refund.channel_refund_id = evidence.channel_refund_id
            refund.payer_total_fen = evidence.payer_total_fen
            refund.payer_refund_fen = evidence.payer_refund_fen
            refund.settlement_total_fen = evidence.settlement_total_fen
            refund.settlement_refund_fen = evidence.settlement_refund_fen
            refund.discount_refund_fen = evidence.discount_refund_fen
            refund.status = evidence.status
            refund.success_at = evidence.success_at
            refund.save(update_fields=['channel_refund_id', 'payer_total_fen', 'payer_refund_fen',
                                       'settlement_total_fen', 'settlement_refund_fen', 'discount_refund_fen',
                                       'status', 'success_at', 'updated_at'])
            if evidence.status == 'SUCCESS':
                if order.status != Order.Status.CANCELLED or order.payment_status not in (Order.PaymentStatus.PAID, Order.PaymentStatus.UNPAID):
                    raise DirectRefundError('订单状态与退款结果不一致，请人工核对')
                order.payment_status = Order.PaymentStatus.REFUNDED
                order.refunded_at = evidence.success_at
                order.save(update_fields=['payment_status', 'refunded_at', 'updated_at'])
            return refund
    except IntegrityError as exc:
        # Only convert a verified cross-refund collision. Preserve unrelated
        # database failures for operators rather than misreporting the cause.
        if DirectPaymentRefund.objects.filter(channel_refund_id=evidence.channel_refund_id).exclude(pk=refund_id).exists():
            raise DirectRefundError('渠道退款编号已关联其他请求') from exc
        raise
