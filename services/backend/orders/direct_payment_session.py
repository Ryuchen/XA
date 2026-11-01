"""Test-only prepayment/cancel intent, using an explicitly injected fake.

No public API, real HTTP or scheduler exists here. A fake prepay ID is not a
WeChat payment parameter. Unknown operations are never automatically resent.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as datetime_timezone
import hashlib
import json
import logging
import re
import uuid

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from club_accounts.models import ClubAccount
from wallet.models import Wallet
from .direct_payment import DirectPaymentError, _require_test_domain
from .direct_payment_models import DirectPaymentIntent
from .direct_payment_quote import quote_matches_intent
from .direct_payment_quote_models import DirectPaymentQuote
from .direct_payment_session_models import DirectPaymentSession, DirectPaymentSessionObservation
from .models import Order

logger = logging.getLogger(__name__)


class FakePrepayGateway:
    """Injected simulation only; handler receives an immutable test snapshot."""
    def __init__(self, handler):
        if not callable(handler):
            raise ValueError('fake handler required')
        self._handler = handler

    def request_prepay(self, snapshot):
        return self._handler(snapshot)


@dataclass(frozen=True)
class FakePrepayRequest:
    appid: str
    mchid: str
    openid: str
    merchant_order_no: str
    amount_fen: int
    currency: str
    quote_hash: str
    payment_deadline: datetime


@dataclass(frozen=True)
class SessionView:
    state: str
    cancel_requested: bool
    payment_deadline: datetime
    fake_prepay_id: str | None


def _locked_intent_session(intent_id):
    if type(intent_id) is not int or intent_id <= 0:
        raise DirectPaymentError('支付意图无效')
    head = DirectPaymentIntent.objects.filter(pk=intent_id).values('order_id').first()
    if not head:
        raise DirectPaymentError('支付意图不存在')
    order = Order.objects.select_for_update().get(pk=head['order_id'])
    intent = DirectPaymentIntent.objects.select_for_update().get(pk=intent_id, order=order)
    session = DirectPaymentSession.objects.select_for_update().filter(intent=intent).first()
    return order, intent, session


def _owner_eligible(order, intent):
    # Match payment confirmation's order -> intent -> wallet -> account order.
    wallet = Wallet.objects.select_for_update().filter(account_id=intent.account_id, user_id=order.customer_id).first()
    account = ClubAccount.objects.select_for_update().get(pk=intent.account_id)
    return bool(wallet and intent.account_id == order.customer_account_id and account.is_active and account.can_login
                and account.business_cycle == order.business_cycle and account.is_openid_bound
                and account.openid == intent.openid and account.wechat_appid == intent.appid)


def _context(intent_id, account_id):
    if type(account_id) is not int or account_id <= 0:
        raise DirectPaymentError('账号无效')
    order, intent, session = _locked_intent_session(intent_id)
    if intent.account_id != account_id or order.customer_account_id != account_id:
        raise DirectPaymentError('无权操作此支付意图')
    if not _owner_eligible(order, intent):
        raise DirectPaymentError('账号身份已变化，请核对支付意图')
    return order, intent, session


def _snapshot_digest(intent, payment_deadline):
    value = {'intent_id': intent.pk, 'account_id': intent.account_id, 'appid': intent.appid,
             'mchid': intent.mchid, 'openid': intent.openid, 'merchant_order_no': intent.merchant_order_no,
             'amount_fen': intent.amount_fen, 'currency': intent.currency,
             'payment_deadline': payment_deadline.astimezone(datetime_timezone.utc).isoformat(timespec='microseconds')}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _snapshot_matches(session, intent, order):
    quote = DirectPaymentQuote.objects.filter(intent=intent).first()
    return bool(quote and quote_matches_intent(intent, order) and session.quote_hash == quote.quote_hash
                and session.appid == intent.appid and session.mchid == intent.mchid and session.openid == intent.openid
                and session.merchant_order_no == intent.merchant_order_no and session.amount_fen == intent.amount_fen
                and session.currency == intent.currency
                and session.snapshot_hash == _snapshot_digest(intent, session.payment_deadline))


def _view(session, intent, order, *, allow_prepay=True):
    now = timezone.now()
    can_resume = (allow_prepay and session.state == DirectPaymentSession.State.PAYABLE and not session.cancel_requested_at
                  and not session.fake_closed_seen_at and not session.fake_payment_risk_at and not intent.evidence_risk_at
                  and intent.status == DirectPaymentIntent.Status.AWAITING and not intent.transaction_id
                  and order.status == Order.Status.PENDING and order.payment_status == Order.PaymentStatus.UNPAID
                  and session.payment_deadline > now)
    if intent.status == DirectPaymentIntent.Status.REVIEW:
        displayed_state = 'PAYMENT_REVIEW'
    elif intent.status == DirectPaymentIntent.Status.PAID:
        displayed_state = (order.payment_status if order.payment_status in ('PAID', 'REFUNDED', 'PARTIALLY_REFUNDED')
                           else 'PAYMENT_REVIEW')
    elif order.payment_status != Order.PaymentStatus.UNPAID:
        displayed_state = 'PAYMENT_REVIEW'
    elif session.fake_closed_seen_at:
        displayed_state = 'SIMULATED_CLOSE_REVIEW'
    elif session.fake_payment_risk_at or intent.evidence_risk_at:
        displayed_state = 'SIMULATED_PAYMENT_REVIEW'
    elif session.state == DirectPaymentSession.State.PAYABLE and session.payment_deadline <= now:
        displayed_state = 'EXPIRED'
    else:
        displayed_state = session.state
    return SessionView(displayed_state, bool(session.cancel_requested_at), session.payment_deadline,
                       session.fake_prepay_id if can_resume else None)


@transaction.atomic
def create_test_session(intent_id, account_id, *, payment_deadline):
    """Bind one test session to frozen order facts; caller supplies server deadline."""
    _require_test_domain()
    if not isinstance(payment_deadline, datetime) or timezone.is_naive(payment_deadline):
        raise DirectPaymentError('支付截止时间无效')
    order, intent, session = _context(intent_id, account_id)
    if session:
        if not _snapshot_matches(session, intent, order) or session.payment_deadline != payment_deadline:
            raise DirectPaymentError('支付会话快照不一致，请核对')
        return _view(session, intent, order)
    if (intent.status != DirectPaymentIntent.Status.AWAITING or intent.transaction_id
            or order.status != Order.Status.PENDING or order.payment_status != Order.PaymentStatus.UNPAID
            or payment_deadline <= timezone.now() or not quote_matches_intent(intent, order)):
        raise DirectPaymentError('当前订单无法创建支付会话')
    quote = DirectPaymentQuote.objects.get(intent=intent)
    session = DirectPaymentSession.objects.create(intent=intent, appid=intent.appid, mchid=intent.mchid,
        openid=intent.openid, merchant_order_no=intent.merchant_order_no, amount_fen=intent.amount_fen,
        currency=intent.currency, quote_hash=quote.quote_hash, payment_deadline=payment_deadline,
        snapshot_hash=_snapshot_digest(intent, payment_deadline))
    return _view(session, intent, order)


@transaction.atomic
def resume_test_session(intent_id, account_id):
    _require_test_domain()
    order, intent, session = _context(intent_id, account_id)
    if not session or not _snapshot_matches(session, intent, order):
        raise DirectPaymentError('支付会话不存在或快照不一致')
    return _view(session, intent, order)


@transaction.atomic
def request_test_cancellation(intent_id, account_id):
    """Record intent only; neither cancel the order nor close the channel."""
    _require_test_domain()
    order, intent, session = _context(intent_id, account_id)
    if not session or not _snapshot_matches(session, intent, order):
        raise DirectPaymentError('支付会话不存在或快照不一致')
    if session.cancel_requested_at:
        return _view(session, intent, order)
    if intent.status != DirectPaymentIntent.Status.AWAITING or intent.transaction_id:
        raise DirectPaymentError('付款结果已确认，不能当作未付款关闭')
    session.state = DirectPaymentSession.State.CANCEL_REQUESTED
    session.cancel_requested_at = timezone.now()
    session.cancel_token = uuid.uuid4().hex
    session.save(update_fields=['state', 'cancel_requested_at', 'cancel_token', 'updated_at'])
    return _view(session, intent, order)


def request_fake_prepay(intent_id, account_id, *, gateway, lease_seconds=30):
    """Run one simulated request outside any transaction; never auto-resend."""
    _require_test_domain()
    if type(gateway) is not FakePrepayGateway:
        raise DirectPaymentError('仅支持注入测试假网关')
    if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
        raise DirectPaymentError('租约无效')
    if transaction.get_connection().in_atomic_block:
        raise DirectPaymentError('模拟渠道调用不能在数据库事务内执行')
    with transaction.atomic():
        order, intent, session = _context(intent_id, account_id)
        if not session or not _snapshot_matches(session, intent, order):
            raise DirectPaymentError('支付会话不存在或快照不一致')
        if (intent.status != DirectPaymentIntent.Status.AWAITING or intent.transaction_id
                or order.status != Order.Status.PENDING or order.payment_status != Order.PaymentStatus.UNPAID
                or session.cancel_requested_at or session.fake_closed_seen_at or session.fake_payment_risk_at or intent.evidence_risk_at
                or session.payment_deadline <= timezone.now()):
            raise DirectPaymentError('当前订单不能继续模拟支付')
        if session.state == DirectPaymentSession.State.PAYABLE:
            return _view(session, intent, order)
        if session.state == DirectPaymentSession.State.SENDING and session.lease_until and session.lease_until <= timezone.now():
            session.state = DirectPaymentSession.State.UNKNOWN
            session.save(update_fields=['state', 'updated_at'])
            return _view(session, intent, order)
        if session.state != DirectPaymentSession.State.PREPARED:
            return _view(session, intent, order)
        token = uuid.uuid4().hex
        session.state, session.attempt_token = DirectPaymentSession.State.SENDING, token
        session.lease_until = timezone.now() + timedelta(seconds=lease_seconds)
        session.save(update_fields=['state', 'attempt_token', 'lease_until', 'updated_at'])
        snapshot = FakePrepayRequest(session.appid, session.mchid, session.openid, session.merchant_order_no,
                                     session.amount_fen, session.currency, session.quote_hash, session.payment_deadline)
    try:
        result = gateway.request_prepay(snapshot)
        if not isinstance(result, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', result):
            outcome, result = 'INVALID', None
        else:
            outcome = 'VALID'
    except Exception:
        outcome, result = 'ERROR', None
    with transaction.atomic():
        # Internal reconciliation is not customer authorization. Even if the
        # account was disabled, changed cycle/OpenID, or lost its wallet while
        # the fake request ran, preserve the bounded fake outcome first.
        order, intent, session = _locked_intent_session(intent_id)
        if not session:
            raise DirectPaymentError('支付会话缺失，请核对')
        DirectPaymentSessionObservation.objects.get_or_create(
            session=session, attempt_token=token,
            defaults={'outcome': outcome, 'fake_prepay_id': result or ''})
        owner_eligible = _owner_eligible(order, intent)
        # Do not let a late response overrule cancellation, a changed lease,
        # confirmed payment or changed frozen intent. No real channel proof is
        # created by this fake-only result.
        if (session.state != DirectPaymentSession.State.SENDING or session.attempt_token != token
                or session.cancel_requested_at or session.fake_closed_seen_at or session.fake_payment_risk_at or intent.evidence_risk_at
                or intent.status != DirectPaymentIntent.Status.AWAITING
                or intent.transaction_id):
            return _view(session, intent, order, allow_prepay=owner_eligible)
        if (not owner_eligible or not _snapshot_matches(session, intent, order)
                or order.status != Order.Status.PENDING or order.payment_status != Order.PaymentStatus.UNPAID
                or session.payment_deadline <= timezone.now()
                or not session.lease_until or session.lease_until <= timezone.now()):
            session.state = DirectPaymentSession.State.UNKNOWN
            session.save(update_fields=['state', 'updated_at'])
            return _view(session, intent, order, allow_prepay=owner_eligible)
        session.state = DirectPaymentSession.State.PAYABLE if result is not None else DirectPaymentSession.State.UNKNOWN
        session.fake_prepay_id = result or ''
        session.lease_until = None
        session.save(update_fields=['state', 'fake_prepay_id', 'lease_until', 'updated_at'])
        return _view(session, intent, order, allow_prepay=owner_eligible)


def recover_test_sessions(*, limit=100):
    """Internal recovery of expired fake send leases; never calls a gateway."""
    _require_test_domain()
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('invalid scan limit')
    now = timezone.now()
    ids = list(DirectPaymentSession.objects.filter(state=DirectPaymentSession.State.SENDING).filter(
        Q(lease_until__lte=now) | Q(lease_until__isnull=True)
    ).filter(
        Q(recovery_retry_at__lte=now) | Q(recovery_retry_at__isnull=True)
    ).order_by('pk').values_list('intent_id', flat=True)[:limit])
    results = []
    for intent_id in ids:
        try:
            with transaction.atomic():
                _, _, session = _locked_intent_session(intent_id)
                if (session and session.state == DirectPaymentSession.State.SENDING
                        and (not session.lease_until or session.lease_until <= timezone.now())):
                    session.state = DirectPaymentSession.State.UNKNOWN
                    session.recovery_retry_at = None
                    session.save(update_fields=['state', 'recovery_retry_at', 'updated_at'])
                    results.append(intent_id)
        except Exception:
            # No exception text: it may contain channel or customer data. A
            # short persistent backoff prevents one bad row starving the scan.
            logger.warning('模拟支付恢复失败: intent_id=%s', intent_id)
            try:
                retry_at = timezone.now() + timedelta(seconds=60)
                DirectPaymentSession.objects.filter(intent_id=intent_id, state=DirectPaymentSession.State.SENDING).update(
                    recovery_failures=F('recovery_failures') + 1, recovery_retry_at=retry_at,
                    recovery_last_error='RECOVERY_FAILED', updated_at=timezone.now())
            except Exception:
                logger.warning('模拟支付恢复退避保存失败: intent_id=%s', intent_id)
    return results
