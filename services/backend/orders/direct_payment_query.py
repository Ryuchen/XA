"""Test-only simulated query of a single frozen direct-payment intent.

There is no HTTP endpoint, scheduler, real channel or signature verification
here. Fake data must never be treated as real WeChat evidence in production.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import re
import uuid

from django.db import transaction
from django.utils import timezone

from .direct_payment import DirectPaymentError, VerifiedPayment, _require_test_domain, confirm_verified_payment
from .direct_payment_evidence_compare import contradicts_saved_payment, flag_payment_evidence_risk
from .direct_payment_delivery_models import DirectPaymentOutbox
from .direct_payment_models import DirectPaymentIntent
from .direct_payment_session import _context, _locked_intent_session, _snapshot_matches
from .direct_payment_session_models import DirectPaymentTestQuery


class FakeQueryGateway:
    """Explicitly injected fake; no real WeChat transport is accepted."""
    def __init__(self, handler):
        if not callable(handler):
            raise ValueError('fake handler required')
        self._handler = handler

    def query_order(self, snapshot):
        return self._handler(snapshot)


@dataclass(frozen=True)
class FakeQueryRequest:
    appid: str
    mchid: str
    merchant_order_no: str


@dataclass(frozen=True)
class FakeQueryResult:
    appid: str
    mchid: str
    merchant_order_no: str
    trade_state: str
    trade_type: str | None
    payment: VerifiedPayment | None = None


@dataclass(frozen=True)
class TestQueryView:
    request_id: str
    status: str
    outcome: str


def _view(row):
    return TestQueryView(row.request_id, row.status, row.outcome)


def _same_payer(intent, payment):
    return (payment.mchid == intent.mchid and payment.appid == intent.appid
            and payment.merchant_order_no == intent.merchant_order_no and payment.openid == intent.openid)


def _fake_payment_payload(payment):
    """Bounded test data only; never retain OpenID or raw exception messages."""
    if (not isinstance(payment, VerifiedPayment) or not isinstance(payment.transaction_id, str)
            or not payment.transaction_id or payment.transaction_id != payment.transaction_id.strip() or len(payment.transaction_id) > 64
            or type(payment.amount_fen) is not int or not 0 < payment.amount_fen < 2**63
            or not isinstance(payment.currency, str) or len(payment.currency) > 16
            or not isinstance(payment.paid_at, datetime) or timezone.is_naive(payment.paid_at)
            or (payment.payer_total_fen is not None and (type(payment.payer_total_fen) is not int
                or not -(2**63) <= payment.payer_total_fen < 2**63))
            or (payment.payer_currency is not None and (not isinstance(payment.payer_currency, str)
                or len(payment.payer_currency) > 16))):
        return None
    promotion_hash = None
    if payment.promotion_detail is not None:
        if not isinstance(payment.promotion_detail, list):
            return None
        try:
            encoded = json.dumps(payment.promotion_detail, sort_keys=True, separators=(',', ':'),
                                 ensure_ascii=False, allow_nan=False).encode('utf-8')
        except (TypeError, ValueError, UnicodeError):
            return None
        if len(encoded) > 256 * 1024:
            return None
        promotion_hash = hashlib.sha256(encoded).hexdigest()
    return {'transaction_id': payment.transaction_id, 'total_fen': payment.amount_fen,
            'currency': payment.currency, 'paid_at': payment.paid_at.isoformat(),
            'payer_total_fen': payment.payer_total_fen, 'payer_currency': payment.payer_currency,
            'promotion_present': payment.promotion_detail is not None, 'promotion_sha256': promotion_hash}


def _mark_conflict(intent, reason):
    if intent.status == DirectPaymentIntent.Status.PAID:
        intent.status, intent.review_reason = DirectPaymentIntent.Status.REVIEW, reason
        intent.save(update_fields=['status', 'review_reason', 'updated_at'])
    DirectPaymentOutbox.objects.filter(intent=intent).update(
        status=DirectPaymentOutbox.Status.REVIEW, last_error=reason, lease_token='', lease_until=None)


def request_fake_query(intent_id, account_id, *, request_id, gateway, lease_seconds=30):
    """Customer-authorized simulated query of the stored order."""
    return _request_fake_query(intent_id, account_id, request_id=request_id, gateway=gateway,
                               lease_seconds=lease_seconds, internal=False)


def request_internal_fake_query(intent_id, *, request_id, gateway, lease_seconds=30):
    """Internal fake reconciliation, independent of a customer's current authorization.

    No client identity or payment parameters are accepted or returned. This
    remains test-only, with no scheduler, route or actual channel connection.
    """
    return _request_fake_query(intent_id, None, request_id=request_id, gateway=gateway,
                               lease_seconds=lease_seconds, internal=True)


def _request_fake_query(intent_id, account_id, *, request_id, gateway, lease_seconds, internal):
    """Query only the stored order once per request ID, using a fake outside locks."""
    _require_test_domain()
    if type(gateway) is not FakeQueryGateway:
        raise DirectPaymentError('仅支持注入模拟查单网关')
    if (not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}', request_id)
            or type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600):
        raise DirectPaymentError('查单请求编号或租约无效')
    if transaction.get_connection().in_atomic_block:
        raise DirectPaymentError('模拟查单不能在数据库事务内执行')
    with transaction.atomic():
        if internal:
            order, intent, session = _locked_intent_session(intent_id)
        else:
            order, intent, session = _context(intent_id, account_id)
        if not session or not _snapshot_matches(session, intent, order):
            raise DirectPaymentError('支付会话不存在或快照不一致')
        row = DirectPaymentTestQuery.objects.select_for_update().filter(session=session, request_id=request_id).first()
        if row:
            if row.status == 'SENDING' and (not row.lease_until or row.lease_until <= timezone.now()):
                row.status, row.outcome = 'UNKNOWN', 'QUERY_RESULT_UNKNOWN'
                row.save(update_fields=['status', 'outcome', 'updated_at'])
            return _view(row)
        other = DirectPaymentTestQuery.objects.select_for_update().filter(session=session, status='SENDING').order_by('pk').first()
        if other:
            if not other.lease_until or other.lease_until <= timezone.now():
                other.status, other.outcome = 'UNKNOWN', 'QUERY_RESULT_UNKNOWN'
                other.save(update_fields=['status', 'outcome', 'updated_at'])
            return _view(other)
        row = DirectPaymentTestQuery.objects.create(session=session, request_id=request_id, attempt_token=uuid.uuid4().hex,
                                                     cancel_token=session.cancel_token,
                                                     lease_until=timezone.now()+timedelta(seconds=lease_seconds))
        token, row_id = row.attempt_token, row.pk
        snapshot = FakeQueryRequest(session.appid, session.mchid, session.merchant_order_no)
    try:
        result = gateway.query_order(snapshot)
    except Exception:
        result = None
    with transaction.atomic():
        order, intent, session = _locked_intent_session(intent_id)
        if not session:
            raise DirectPaymentError('支付会话缺失，请核对')
        row = DirectPaymentTestQuery.objects.select_for_update().get(pk=row_id, session=session, attempt_token=token)
        # The fake result is kept in bounded, controlled fields. Even if the
        # lease expired, matching late evidence must not be silently discarded.
        if result is None:
            row.status, row.outcome = 'UNKNOWN', 'QUERY_RESULT_UNKNOWN'
        elif (not isinstance(result, FakeQueryResult) or result.trade_state not in ('NOTPAY', 'CLOSED', 'SUCCESS')
              or result.appid != intent.appid or result.mchid != intent.mchid
              or result.merchant_order_no != intent.merchant_order_no
              or (result.trade_state != 'SUCCESS' and result.trade_type not in (None, 'JSAPI'))):
            row.status, row.outcome = 'REVIEW', 'QUERY_IDENTITY_OR_STATE_INVALID'
        elif not _snapshot_matches(session, intent, order):
            row.status, row.outcome = 'REVIEW', 'SESSION_SNAPSHOT_INVALID'
        else:
            row.fake_evidence = {'trade_state': result.trade_state}
            if result.trade_state == 'NOTPAY':
                row.status, row.outcome = 'DONE', 'SIMULATED_NOTPAY'
            elif result.trade_state == 'CLOSED':
                if not session.fake_closed_seen_at:
                    session.fake_closed_seen_at = timezone.now()
                    session.save(update_fields=['fake_closed_seen_at', 'updated_at'])
                if intent.transaction_id:
                    _mark_conflict(intent, 'QUERY_CLOSE_PAYMENT_CONFLICT')
                row.status, row.outcome = 'REVIEW', 'SIMULATED_CLOSED_REVIEW'
            else:
                payment = result.payment
                payload = _fake_payment_payload(payment)
                same_payer = isinstance(payment, VerifiedPayment) and _same_payer(intent, payment)
                if result.trade_type != 'JSAPI' or payload is None or not same_payer:
                    if same_payer:
                        session.fake_payment_risk_at = session.fake_payment_risk_at or timezone.now()
                        session.save(update_fields=['fake_payment_risk_at', 'updated_at'])
                        flag_payment_evidence_risk(intent)
                    row.status, row.outcome = 'REVIEW', 'QUERY_PAYMENT_IDENTITY_INVALID'
                else:
                    row.fake_evidence.update(payload)
                    if contradicts_saved_payment(intent, payment):
                        _mark_conflict(intent, 'PAYMENT_EVIDENCE_CONFLICT')
                        row.status, row.outcome = 'REVIEW', 'QUERY_PAYMENT_CONFLICT'
                    elif payment.amount_fen != intent.amount_fen or payment.currency != intent.currency:
                        session.fake_payment_risk_at = session.fake_payment_risk_at or timezone.now()
                        session.save(update_fields=['fake_payment_risk_at', 'updated_at'])
                        flag_payment_evidence_risk(intent)
                        row.status, row.outcome = 'REVIEW', 'QUERY_PAYMENT_AMOUNT_INVALID'
                    else:
                        try:
                            updated = confirm_verified_payment(order.pk, payment)
                        except DirectPaymentError:
                            flag_payment_evidence_risk(intent)
                            row.status, row.outcome = 'REVIEW', 'QUERY_PAYMENT_REQUIRES_REVIEW'
                        else:
                            row.status = 'DONE' if updated.status == DirectPaymentIntent.Status.PAID else 'REVIEW'
                            row.outcome = 'SIMULATED_PAYMENT_APPLIED' if row.status == 'DONE' else 'QUERY_PAYMENT_REQUIRES_REVIEW'
        row.lease_until = None
        row.save(update_fields=['status', 'outcome', 'fake_evidence', 'lease_until', 'updated_at'])
        return _view(row)


@transaction.atomic
def recover_test_query(intent_id, request_id):
    """Mark an expired simulated query unknown; never call a gateway."""
    _require_test_domain()
    _, _, session = _locked_intent_session(intent_id)
    if not session:
        raise DirectPaymentError('支付会话不存在')
    row = DirectPaymentTestQuery.objects.select_for_update().get(session=session, request_id=request_id)
    if row.status == 'SENDING' and (not row.lease_until or row.lease_until <= timezone.now()):
        row.status, row.outcome = 'UNKNOWN', 'QUERY_RESULT_UNKNOWN'
        row.save(update_fields=['status', 'outcome', 'updated_at'])
    return _view(row)
