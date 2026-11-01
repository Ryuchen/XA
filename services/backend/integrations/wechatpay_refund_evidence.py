"""Staged conversion of authenticated WeChat refund evidence.

No HTTP endpoint or database write exists here. A caller must obtain the
notification from WeChatPayV3.decrypt_notification, or the response from the
gateway call in this module; never construct these objects from client input.
The order domain must still compare the evidence with locked persisted intent
and refund records before recording any change.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING

from .wechatpay_gateway import WeChatPayGateway, WeChatPayInputError, WeChatPayOutcomeUnknown
from .wechatpay_v3 import VerifiedNotification

if TYPE_CHECKING:
    from orders.direct_refund import VerifiedRefund


# datetime stores microseconds; reject greater precision instead of silently
# truncating distinct authenticated channel timestamps to the same value.
_TIME = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z')
_EVENTS = {'REFUND.SUCCESS': 'SUCCESS', 'REFUND.CLOSED': 'CLOSED', 'REFUND.ABNORMAL': 'ABNORMAL'}
_STATES = frozenset(('SUCCESS', 'CLOSED', 'PROCESSING', 'ABNORMAL'))


@dataclass(frozen=True)
class RefundNotificationCandidate:
    """Authenticated notification facts; currency is not supplied by WeChat.

    This candidate deliberately cannot become a ``VerifiedRefund`` until an
    authenticated query supplies the currency and is matched with this notice.
    """
    notification_id: str
    mchid: str
    merchant_order_no: str
    transaction_id: str
    merchant_refund_no: str
    channel_refund_id: str
    amount_fen: int
    total_fen: int
    payer_refund_fen: int
    payer_total_fen: int
    status: str
    success_at: datetime | None


@dataclass(frozen=True)
class RefundEvidenceBundle:
    """Verified domain evidence plus authenticated cash amounts.

    This is an in-memory transport object, not an audit record. Callers must
    retain the cash fields and event identity durably.
    """
    verified_refund: VerifiedRefund
    payer_total_fen: int
    payer_refund_fen: int
    notification_id: str | None = None
    settlement_total_fen: int | None = None
    settlement_refund_fen: int | None = None
    discount_refund_fen: int | None = None


def _field(payload, name, max_length):
    value = payload.get(name)
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > max_length or any(ord(c) < 32 for c in value)):
        raise WeChatPayOutcomeUnknown('Invalid authenticated refund identity')
    return value


def _amounts(payload, *, require_payer):
    amount = payload.get('amount')
    if not isinstance(amount, dict):
        raise WeChatPayOutcomeUnknown('Invalid authenticated refund amounts')
    total, refund = amount.get('total'), amount.get('refund')
    if (type(total) is not int or type(refund) is not int
            or not 0 < refund <= total <= 2**63 - 1):
        raise WeChatPayOutcomeUnknown('Invalid authenticated refund amounts')
    payer_total, payer_refund = amount.get('payer_total'), amount.get('payer_refund')
    if require_payer or payer_total is not None or payer_refund is not None:
        if (type(payer_total) is not int or type(payer_refund) is not int
                or not 0 <= payer_refund <= payer_total <= total or payer_refund > refund):
            raise WeChatPayOutcomeUnknown('Invalid authenticated payer refund amounts')
    return total, refund, payer_total, payer_refund


def _success_time(payload, status):
    value = payload.get('success_time')
    if status != 'SUCCESS':
        if value is not None:
            raise WeChatPayOutcomeUnknown('Unexpected refund success time')
        return None
    if not isinstance(value, str) or len(value) > 64 or not _TIME.fullmatch(value):
        raise WeChatPayOutcomeUnknown('Missing or invalid refund success time')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise WeChatPayOutcomeUnknown('Missing or invalid refund success time') from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WeChatPayOutcomeUnknown('Missing or invalid refund success time')
    return parsed


def parse_refund_notification(notice: VerifiedNotification) -> RefundNotificationCandidate:
    """Parse an already signature-verified/decrypted refund event, not client data."""
    if (not isinstance(notice, VerifiedNotification) or not isinstance(notice.event_type, str)
            or notice.event_type not in _EVENTS
            or notice.resource_type != 'encrypt-resource' or notice.original_type != 'refund'
            or not isinstance(notice.payload, dict)):
        raise WeChatPayOutcomeUnknown('Invalid refund notification type')
    payload = notice.payload
    status = payload.get('refund_status')
    if status != _EVENTS[notice.event_type]:
        raise WeChatPayOutcomeUnknown('Refund event type and state do not match')
    total, refund, payer_total, payer_refund = _amounts(payload, require_payer=True)
    return RefundNotificationCandidate(
        notification_id=_field({'id': notice.notification_id}, 'id', 36),
        mchid=_field(payload, 'mchid', 32), merchant_order_no=_field(payload, 'out_trade_no', 32),
        transaction_id=_field(payload, 'transaction_id', 32), merchant_refund_no=_field(payload, 'out_refund_no', 64),
        channel_refund_id=_field(payload, 'refund_id', 32), amount_fen=refund, total_fen=total,
        payer_refund_fen=payer_refund, payer_total_fen=payer_total, status=status,
        success_at=_success_time(payload, status),
    )


def _check_gateway(gateway, intent_mchid, intent_appid):
    if (not isinstance(gateway, WeChatPayGateway) or not isinstance(intent_mchid, str)
            or not intent_mchid or gateway.merchant_id != intent_mchid
            or not isinstance(intent_appid, str) or not intent_appid or gateway.app_id != intent_appid):
        raise WeChatPayInputError('Gateway merchant/application does not match the stored payment intent')


def _response_evidence(gateway, intent_mchid, intent_appid, response):
    # API response does not include mchid. It comes from the configured,
    # authenticated gateway, after matching the immutable intent snapshot.
    _check_gateway(gateway, intent_mchid, intent_appid)
    if not isinstance(response, dict) or ('mchid' in response and response['mchid'] != intent_mchid):
        raise WeChatPayOutcomeUnknown('Refund response merchant mismatch')
    status = response.get('status')
    if not isinstance(status, str) or status not in _STATES:
        raise WeChatPayOutcomeUnknown('Invalid authenticated refund state')
    total, refund, payer_total, payer_refund = _amounts(response, require_payer=True)
    if response['amount'].get('currency') != 'CNY':
        raise WeChatPayOutcomeUnknown('Refund currency not established')
    settlement = {}
    for name, maximum in (('settlement_total', total), ('settlement_refund', refund), ('discount_refund', refund)):
        value = response['amount'].get(name)
        if type(value) is not int or not 0 <= value <= maximum:
            raise WeChatPayOutcomeUnknown('Invalid authenticated refund settlement amount')
        settlement[name + '_fen'] = value
    if (settlement['settlement_total_fen'] is not None and settlement['settlement_refund_fen'] is not None
            and settlement['settlement_refund_fen'] > settlement['settlement_total_fen']):
        raise WeChatPayOutcomeUnknown('Invalid authenticated refund settlement amounts')
    # Imported lazily: this module does not register or write domain models.
    from orders.direct_refund import VerifiedRefund
    evidence = VerifiedRefund(
        mchid=intent_mchid, merchant_order_no=_field(response, 'out_trade_no', 32),
        transaction_id=_field(response, 'transaction_id', 32),
        merchant_refund_no=_field(response, 'out_refund_no', 64),
        channel_refund_id=_field(response, 'refund_id', 32), amount_fen=refund,
        total_fen=total, currency='CNY', status=status, success_at=_success_time(response, status),
        payer_total_fen=payer_total, payer_refund_fen=payer_refund, **settlement,
    )
    return RefundEvidenceBundle(verified_refund=evidence, payer_total_fen=payer_total,
                                payer_refund_fen=payer_refund, **settlement)


def query_refund_evidence(gateway: WeChatPayGateway, *, intent_mchid: str, intent_appid: str, out_refund_no: str,
                          notification: RefundNotificationCandidate | None = None):
    """Query once, verify response, and construct domain evidence; never retry."""
    _check_gateway(gateway, intent_mchid, intent_appid)
    if notification is not None and (not isinstance(notification, RefundNotificationCandidate)
            or notification.mchid != intent_mchid or notification.merchant_refund_no != out_refund_no):
        raise WeChatPayOutcomeUnknown('Refund notification does not match stored merchant and refund number')
    response = gateway.query_refund(out_refund_no)
    bundle = _response_evidence(gateway, intent_mchid, intent_appid, response)
    evidence = bundle.verified_refund
    if evidence.merchant_refund_no != out_refund_no:
        raise WeChatPayOutcomeUnknown('Refund query returned a different refund number')
    if notification is not None:
        if (notification.merchant_order_no != evidence.merchant_order_no
                or notification.transaction_id != evidence.transaction_id
                or notification.channel_refund_id != evidence.channel_refund_id
                or notification.amount_fen != evidence.amount_fen or notification.total_fen != evidence.total_fen
                or notification.payer_total_fen != bundle.payer_total_fen
                or notification.payer_refund_fen != bundle.payer_refund_fen):
            raise WeChatPayOutcomeUnknown('Refund notification and query do not agree')
        # CLOSED and SUCCESS are terminal; ABNORMAL may later become SUCCESS.
        possible = {'SUCCESS': {'SUCCESS'}, 'CLOSED': {'CLOSED'}, 'ABNORMAL': {'ABNORMAL', 'SUCCESS'}}
        if evidence.status not in possible[notification.status]:
            raise WeChatPayOutcomeUnknown('Refund notification and queried state need reconciliation')
        if notification.status == 'SUCCESS' and notification.success_at != evidence.success_at:
            raise WeChatPayOutcomeUnknown('Refund success times do not match')
        bundle = replace(bundle, notification_id=notification.notification_id)
    return bundle


def submit_refund_evidence(gateway: WeChatPayGateway, *, intent_mchid: str, intent_appid: str, out_refund_no: str,
                           amount_fen: int, total_fen: int, out_trade_no: str | None = None,
                           transaction_id: str | None = None, reason: str = '', notify_url: str | None = None):
    """Submit once; a returned PROCESSING state is never refund success."""
    _check_gateway(gateway, intent_mchid, intent_appid)
    response = gateway.create_refund(out_refund_no, amount_fen, total_fen, out_trade_no=out_trade_no,
                                     transaction_id=transaction_id, reason=reason, notify_url=notify_url)
    bundle = _response_evidence(gateway, intent_mchid, intent_appid, response)
    evidence = bundle.verified_refund
    if (evidence.merchant_refund_no != out_refund_no or evidence.amount_fen != amount_fen
            or evidence.total_fen != total_fen
            or (out_trade_no is not None and evidence.merchant_order_no != out_trade_no)
            or (transaction_id is not None and evidence.transaction_id != transaction_id)):
        raise WeChatPayOutcomeUnknown('Refund submission response does not match the request')
    return bundle
