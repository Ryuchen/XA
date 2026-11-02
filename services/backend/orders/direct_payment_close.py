"""Test-only fake close attempt; an ACK is not channel closure evidence."""
from dataclasses import dataclass
from datetime import timedelta
import re
import uuid

from django.db import transaction
from django.utils import timezone

from .direct_payment import DirectPaymentError, _require_test_domain
from .direct_payment_models import DirectPaymentIntent
from .direct_payment_session import _locked_intent_session, _snapshot_matches
from .direct_payment_session_models import DirectPaymentTestClose, DirectPaymentTestQuery
from .models import Order


@dataclass(frozen=True)
class FakeCloseRequest:
    appid: str
    mchid: str
    merchant_order_no: str


@dataclass(frozen=True)
class FakeCloseView:
    request_id: str
    status: str
    outcome: str


class FakeCloseGateway:
    def __init__(self, handler):
        if not callable(handler):
            raise ValueError('fake handler required')
        self._handler = handler

    def close(self, snapshot):
        return self._handler(snapshot)


def _view(row):
    return FakeCloseView(row.request_id, row.status, row.outcome)


def _lock_query_and_close(session, query_id):
    query = DirectPaymentTestQuery.objects.select_for_update().filter(pk=query_id, session=session).first()
    close = DirectPaymentTestClose.objects.select_for_update().filter(session=session).first()
    return query, close


def request_internal_fake_close(intent_id, *, source_query_id, request_id, gateway, lease_seconds=30):
    """Attempt exactly once per session; do not expose as a customer endpoint."""
    _require_test_domain()
    if (type(gateway) is not FakeCloseGateway or type(source_query_id) is not int or source_query_id <= 0
            or not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}', request_id)
            or type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600):
        raise DirectPaymentError('模拟关单参数无效')
    if transaction.get_connection().in_atomic_block:
        raise DirectPaymentError('模拟关单不能在数据库事务内执行')
    with transaction.atomic():
        order, intent, session = _locked_intent_session(intent_id)
        if not session:
            raise DirectPaymentError('支付会话不存在')
        query, row = _lock_query_and_close(session, source_query_id)
        if row:
            if row.status == 'SENDING' and (not row.lease_until or row.lease_until <= timezone.now()):
                row.status, row.outcome = 'UNKNOWN', 'SIMULATED_CLOSE_UNKNOWN'
                row.save(update_fields=['status', 'outcome', 'updated_at'])
            return _view(row)
        if (not _snapshot_matches(session, intent, order) or not session.cancel_requested_at
                or not session.cancel_token or not query or query.cancel_token != session.cancel_token
                or query.status != 'DONE' or query.outcome != 'SIMULATED_NOTPAY'
                or query.fake_evidence.get('trade_state') != 'NOTPAY'
                or intent.status != DirectPaymentIntent.Status.AWAITING or intent.transaction_id
                or intent.evidence_risk_at or session.fake_closed_seen_at or session.fake_payment_risk_at
                or order.status != Order.Status.PENDING or order.payment_status != Order.PaymentStatus.UNPAID):
            raise DirectPaymentError('缺少当前取消意愿后的安全模拟查单')
        row = DirectPaymentTestClose.objects.create(session=session, source_query=query, request_id=request_id,
            attempt_token=uuid.uuid4().hex, cancel_token=session.cancel_token, snapshot_hash=session.snapshot_hash,
            lease_until=timezone.now() + timedelta(seconds=lease_seconds))
        token, row_id = row.attempt_token, row.pk
        snapshot = FakeCloseRequest(session.appid, session.mchid, session.merchant_order_no)
    try:
        response = gateway.close(snapshot)
        observed = 'SIMULATED_ACK' if response is None else 'SIMULATED_INVALID_RESPONSE'
    except Exception:
        observed = 'SIMULATED_CLOSE_UNKNOWN'
    with transaction.atomic():
        order, intent, session = _locked_intent_session(intent_id)
        if not session:
            raise DirectPaymentError('支付会话缺失，请核对')
        query, row = _lock_query_and_close(session, source_query_id)
        if not row or row.pk != row_id or row.attempt_token != token:
            raise DirectPaymentError('模拟关单尝试缺失，请核对')
        # Never infer a closed channel from this fake acknowledgement.
        row.late_outcome = observed
        if row.status == 'SENDING' and row.lease_until and row.lease_until > timezone.now():
            if (not query or query.cancel_token != session.cancel_token or row.cancel_token != session.cancel_token
                    or row.snapshot_hash != session.snapshot_hash or not _snapshot_matches(session, intent, order)
                    or intent.status != DirectPaymentIntent.Status.AWAITING or intent.transaction_id
                    or intent.evidence_risk_at or session.fake_closed_seen_at or session.fake_payment_risk_at
                    or order.status != Order.Status.PENDING or order.payment_status != Order.PaymentStatus.UNPAID):
                row.status, row.outcome = 'REVIEW', 'SIMULATED_CLOSE_REVIEW'
            elif observed == 'SIMULATED_ACK':
                row.status, row.outcome = 'ACK', 'SIMULATED_ACK_ONLY'
            else:
                row.status, row.outcome = 'UNKNOWN', 'SIMULATED_CLOSE_UNKNOWN'
        elif row.status == 'SENDING':
            row.status, row.outcome = 'UNKNOWN', 'SIMULATED_CLOSE_UNKNOWN'
        row.lease_until = None
        row.save(update_fields=['status', 'outcome', 'late_outcome', 'lease_until', 'updated_at'])
        return _view(row)


@transaction.atomic
def recover_internal_fake_close(intent_id):
    """Expire a lease without resending; a late fake result is still recorded."""
    _require_test_domain()
    _, _, session = _locked_intent_session(intent_id)
    if not session:
        raise DirectPaymentError('支付会话不存在')
    head = DirectPaymentTestClose.objects.filter(session=session).values('source_query_id').first()
    if not head:
        raise DirectPaymentError('模拟关单尝试不存在')
    _, row = _lock_query_and_close(session, head['source_query_id'])
    if row.status == 'SENDING' and (not row.lease_until or row.lease_until <= timezone.now()):
        row.status, row.outcome = 'UNKNOWN', 'SIMULATED_CLOSE_UNKNOWN'
        row.save(update_fields=['status', 'outcome', 'updated_at'])
    return _view(row)
