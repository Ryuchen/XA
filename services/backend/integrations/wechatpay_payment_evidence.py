"""Convert authenticated JSAPI payment facts without changing business state.

Only pass notifications returned by WeChatPayV3.decrypt_notification. Query
responses are obtained from the gateway inside this module. These in-memory
objects are not a database record or a substitute for matching locked intent
snapshots and durably deduplicating notification IDs in an orchestrator.
"""
from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING

from .wechatpay_gateway import WeChatPayGateway, WeChatPayInputError, WeChatPayOutcomeUnknown
from .wechatpay_v3 import VerifiedNotification

if TYPE_CHECKING:
    from orders.direct_payment import VerifiedPayment


_TIME = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z')


@dataclass(frozen=True)
class PaymentEvidenceBundle:
    verified_payment: VerifiedPayment
    notification_id: str | None = None


def _field(payload, name, limit):
    value = payload.get(name)
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > limit or any(ord(c) < 32 for c in value)):
        raise WeChatPayOutcomeUnknown('Invalid authenticated payment identity')
    return value


def _payment(payload):
    if not isinstance(payload, dict) or payload.get('trade_type') != 'JSAPI' or payload.get('trade_state') != 'SUCCESS':
        raise WeChatPayOutcomeUnknown('Payment is not an authenticated JSAPI success')
    amount, payer = payload.get('amount'), payload.get('payer')
    if not isinstance(amount, dict) or not isinstance(payer, dict):
        raise WeChatPayOutcomeUnknown('Missing authenticated payment information')
    total, payer_total = amount.get('total'), amount.get('payer_total')
    if (type(total) is not int or not 0 < total <= 2**63 - 1
            or (payer_total is not None and (type(payer_total) is not int or not 0 <= payer_total <= total))
            or amount.get('currency') != 'CNY'):
        raise WeChatPayOutcomeUnknown('Invalid authenticated payment amounts or currency')
    if 'payer_total' in amount and payer_total is None:
        raise WeChatPayOutcomeUnknown('Invalid authenticated payer amount')
    payer_currency = amount.get('payer_currency')
    if 'payer_currency' in amount:
        payer_currency = _field(amount, 'payer_currency', 16)
    promotion_detail = payload.get('promotion_detail')
    if 'promotion_detail' in payload and (not isinstance(promotion_detail, list)
                                            or any(not isinstance(item, dict) for item in promotion_detail)):
        raise WeChatPayOutcomeUnknown('Invalid authenticated promotion detail')
    timestamp = payload.get('success_time')
    if not isinstance(timestamp, str) or len(timestamp) > 64 or not _TIME.fullmatch(timestamp):
        raise WeChatPayOutcomeUnknown('Missing or invalid payment success time')
    try:
        paid_at = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    except ValueError as exc:
        raise WeChatPayOutcomeUnknown('Missing or invalid payment success time') from exc
    if paid_at.tzinfo is None or paid_at.utcoffset() is None:
        raise WeChatPayOutcomeUnknown('Missing or invalid payment success time')
    from orders.direct_payment import VerifiedPayment
    return VerifiedPayment(
        merchant_order_no=_field(payload, 'out_trade_no', 32),
        transaction_id=_field(payload, 'transaction_id', 32),
        appid=_field(payload, 'appid', 32), mchid=_field(payload, 'mchid', 32),
        openid=_field(payer, 'openid', 128), amount_fen=total, payer_total_fen=payer_total,
        payer_currency=payer_currency, promotion_detail=deepcopy(promotion_detail),
        currency=amount['currency'], paid_at=paid_at,
    )


def parse_payment_notification(notice: VerifiedNotification) -> PaymentEvidenceBundle:
    """Convert a verified/decrypted payment notification, retaining its ID."""
    if (not isinstance(notice, VerifiedNotification) or notice.event_type != 'TRANSACTION.SUCCESS'
            or notice.resource_type != 'encrypt-resource' or notice.original_type != 'transaction'):
        raise WeChatPayOutcomeUnknown('Invalid payment notification type')
    notification_id = _field({'id': notice.notification_id}, 'id', 36)
    return PaymentEvidenceBundle(_payment(notice.payload), notification_id)


def query_payment_evidence(gateway: WeChatPayGateway, *, intent_mchid: str, intent_appid: str,
                           out_trade_no: str, notification: PaymentEvidenceBundle | None = None) -> PaymentEvidenceBundle:
    """Query once using the immutable merchant snapshot; never retry automatically."""
    if (not isinstance(gateway, WeChatPayGateway) or not isinstance(intent_mchid, str)
            or not intent_mchid or gateway.merchant_id != intent_mchid
            or not isinstance(intent_appid, str) or not intent_appid or gateway.app_id != intent_appid):
        raise WeChatPayInputError('Gateway merchant/application does not match the stored payment intent')
    if notification is not None:
        if (not isinstance(notification, PaymentEvidenceBundle) or not notification.notification_id
                or notification.verified_payment.mchid != intent_mchid
                or notification.verified_payment.appid != intent_appid
                or notification.verified_payment.merchant_order_no != out_trade_no):
            raise WeChatPayOutcomeUnknown('Payment notification does not match stored intent identity')
    payment = _payment(gateway.query_order(out_trade_no))
    if payment.mchid != intent_mchid or payment.appid != intent_appid or payment.merchant_order_no != out_trade_no:
        raise WeChatPayOutcomeUnknown('Payment query does not match stored intent identity')
    result = PaymentEvidenceBundle(payment)
    if notification is not None:
        if notification.verified_payment != payment:
            raise WeChatPayOutcomeUnknown('Payment notification and query do not agree')
        result = replace(result, notification_id=notification.notification_id)
    return result
