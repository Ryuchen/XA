"""Unexposed staging domain for direct WeChat order payments.

There is deliberately no HTTP endpoint or real gateway here. The domain is
test-only until refunds, purchase benefits, provider selection and reconciliation
are complete. Production must never trust a client-provided payment result.
"""
import logging
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from datetime import datetime

from django.conf import settings
from django.db import IntegrityError, connections, transaction
from django.utils import timezone

from club_accounts.models import ClubAccount, LegacyAccountMap
from wallet.receipts import fingerprint_for
from wallet.services import get_wallet

from .direct_payment_models import DirectPaymentIntent
from .models import KookDispatchRecord, Order, OrderStatusLog
from .serializers import CreateOrderSerializer
from .settlement import compute_split
from .state_machine import IllegalTransitionError, log_only

logger = logging.getLogger(__name__)


class DirectPaymentError(ValueError):
    pass


class DirectPaymentCancellationUnconfirmed(IllegalTransitionError):
    """Local cancellation cannot prove that a channel order is closed."""


def require_cancel_channel_confirmation(order):
    """Call with the order lock, after the caller's authorization checks.

    This guard applies even when the test domain is disabled, in case a saved
    staged intent exists. It does not implement query/close or cancel intent.
    Internal reconciliation remains able to use the state machine directly.
    """
    if (order.payment_status == Order.PaymentStatus.UNPAID
            and DirectPaymentIntent.objects.filter(order_id=order.pk).exists()):
        raise DirectPaymentCancellationUnconfirmed(
            '微信支付状态待核对，订单尚未取消，请稍后查看或联系客服')


@dataclass(frozen=True)
class VerifiedPayment:
    """Evidence constructed only by a trusted, signature-verifying gateway."""
    merchant_order_no: str
    transaction_id: str
    appid: str
    mchid: str
    openid: str
    amount_fen: int
    currency: str
    paid_at: datetime
    payer_total_fen: int | None = None
    payer_currency: str | None = None
    promotion_detail: list | None = None


# Test-domain clock tolerance, not a guarantee about WeChat success_time.
# Compare only to merchant-side timestamps, never a caller-provided clock.
PAYMENT_TIME_SKEW = timedelta(minutes=5)


def _require_test_domain():
    # This is a simulation, not a payment verifier. A feature flag alone must
    # never permit fabricated evidence to mutate an ordinary business database.
    # Read the active connection settings: Django swaps NAME for its test DB.
    database = connections['default'].settings_dict
    # Django's test overrides use a UserSettingsHolder whose SETTINGS_MODULE
    # may be None; the process setting remains the loaded settings module.
    module = os.environ.get('DJANGO_SETTINGS_MODULE', '')
    engine, name = database.get('ENGINE'), database.get('NAME')
    isolated_sqlite = (engine == 'django.db.backends.sqlite3' and name in (
        ':memory:', 'file:memorydb_default?mode=memory&cache=shared'))
    isolated_mysql = (engine == 'django.db.backends.mysql'
        and isinstance(name, str) and re.fullmatch(r'test_xa_test_[A-Za-z0-9_]+', name)
        and database.get('HOST') in ('127.0.0.1', 'localhost', '::1')
        and isinstance(database.get('USER'), str) and database['USER'].startswith('xa_test'))
    if (getattr(settings, 'WECHAT_DIRECT_DOMAIN_TESTING', False) is not True
            or module not in ('config.test_settings', 'config.migration_test_settings', 'config.mysql_test_settings')
            or not (isolated_sqlite or isolated_mysql)):
        raise DirectPaymentError('微信直接支付尚未开放')


def test_domain_available():
    """Fail closed for read-side filters using the same simulation boundary."""
    try:
        _require_test_domain()
    except DirectPaymentError:
        return False
    return True


def prepare_direct_order(request, *, appid, mchid):
    """Create an unpaid, private order with immutable merchant expectations.

    Caller supplies trusted server-side merchant configuration, never values
    from the client. Selected providers, coupons and experience products are
    intentionally rejected until their payment-window reservations exist.
    """
    _require_test_domain()
    account = request.account
    user = request.legacy_user
    if account.account_type != ClubAccount.AccountType.BOSS or not user:
        raise DirectPaymentError('仅老板可创建微信支付订单')
    if not LegacyAccountMap.objects.filter(account=account, legacy_user_id=user.pk).exists():
        raise DirectPaymentError('老板账号映射不存在或不一致')
    request_id = request.data.get('client_request_id')
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}', request_id):
        raise DirectPaymentError('请提供有效的下单请求编号')
    if not isinstance(appid, str) or not appid or len(appid) > 32 or not isinstance(mchid, str) or not mchid or len(mchid) > 32:
        raise DirectPaymentError('商户配置不完整')
    fingerprint = fingerprint_for('/api/orders/direct-payment/create/', request.data)
    with transaction.atomic():
        # Share the same buyer lock as balance orders and account retirement.
        get_wallet(account=account, user=user, for_update=True)
        # Ban/unban serializes on the account row, while retirement takes the
        # wallet first. Re-read under the same wallet -> account lock order.
        account = ClubAccount.objects.select_for_update().get(pk=account.pk)
        if not account.is_active or not account.can_login:
            raise DirectPaymentError('账号已停用，无法创建支付订单')
        request.account = account
        existing = DirectPaymentIntent.objects.filter(account=account, client_request_id=request_id).select_related('order').first()
        if existing:
            if existing.request_fingerprint != fingerprint:
                raise DirectPaymentError('同一请求编号不能用于不同订单')
            if (not account.is_openid_bound or account.openid != existing.openid
                    or account.wechat_appid != existing.appid):
                raise DirectPaymentError('微信身份已变更，请先核对原支付订单')
            return existing
        if not account.is_openid_bound or not account.openid or account.wechat_appid != appid:
            raise DirectPaymentError('当前账号未绑定对应小程序微信身份')
        serializer = CreateOrderSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            raise DirectPaymentError(str(serializer.errors))
        data = serializer.validated_data
        from .purchase_benefits import is_experience, lock_purchase
        if data['provider_accounts'] or data.get('user_coupon_id') or data['user_coupon'] or is_experience(data['service']):
            raise DirectPaymentError('微信支付暂不支持指定陪玩、优惠券及体验资格')
        if data.get('expected_amount') is None or data['expected_amount'] != data['amount'] or data['amount'] <= 0:
            raise DirectPaymentError('请确认有效的订单金额')
        # Serialize product price updates against the reprice and contract.
        # Other purchase flows take the buyer wallet before service rows too.
        data['service'] = type(data['service']).objects.select_for_update().get(pk=data['service'].pk)
        lock_purchase(account, data['service'], data['game_rounds'], None, data['pricing'])
        from .direct_payment_quote import quote_direct_payment, snapshot_values
        from .direct_payment_quote_models import DirectPaymentQuote
        if any(key in request.data for key in ('fee_fen', 'rate_numerator', 'rate_denominator', 'fee_basis', 'rounding', 'policy_id',
                                                'conversion_version', 'units_per_yuan', 'fen_per_yuan', 'conversion_rounding')):
            raise DirectPaymentError('手续费策略只能由服务端配置')
        quote = quote_direct_payment(service_units=data['amount'], appid=appid, mchid=mchid)
        expected_total = request.data.get('expected_payment_total')
        expected_quote = request.data.get('expected_fee_quote_hash')
        if quote.configured or expected_total is not None or expected_quote is not None:
            if (type(expected_total) is not int or expected_total != quote.total_fen
                    or not isinstance(expected_quote, str) or expected_quote != quote.quote_hash):
                raise DirectPaymentError('支付总额或手续费策略已变化，请刷新并确认')
        from .timeout import current_timeout_config
        timeout = current_timeout_config().minutes
        if data.get('expected_timeout_minutes') is not None and data['expected_timeout_minutes'] != timeout:
            raise DirectPaymentError('待接单时限已变化，请刷新后重新确认')
        inviter = data.get('inviter')
        inviter_mapping = LegacyAccountMap.objects.filter(legacy_user_id=inviter.pk).first() if inviter else None
        split = compute_split(data['amount'], inviter_commission_rate=data['inviter_commission_rate'], **data['commission_spec'])
        order = Order.objects.create(
            customer=user, customer_account=account, service=data['service'],
            support_contact=data.get('support_contact'), amount=data['amount'],
            game_rounds=data['game_rounds'], game_region=data['game_region'],
            game_nickname=data['game_nickname'], game_uid=data['game_uid'],
            remark=data['remark'], provider_gender=data['provider_gender'],
            dispatch_route=data['dispatch_route'],
            requires_boss_selection=data['dispatch_route'] == Order.DispatchRoute.PUBLIC and data['dispatch_mode'] == 'BOSS_SELECT',
            status=Order.Status.PENDING, payment_status=Order.PaymentStatus.UNPAID,
            pending_timeout_minutes=timeout, auto_cancel_at=None,
            original_amount=data['original_amount'], boss_discount=data['boss_discount'],
            promo_discount=data['promo_discount'], coupon_discount=data['coupon_discount'],
            promotion=data.get('promotion'), **data['commission_snapshot'],
            commission_rate=split.commission_rate, provider_income=split.provider_income,
            inviter=inviter, inviter_account=inviter_mapping.account if inviter_mapping else None,
            inviter_commission=split.inviter_commission, shop_income=split.shop_income,
        )
        intent = DirectPaymentIntent.objects.create(
            order=order, account=account, client_request_id=request_id,
            request_fingerprint=fingerprint, merchant_order_no='XA' + uuid.uuid4().hex[:30],
            appid=appid, mchid=mchid, openid=account.openid, amount_fen=quote.total_fen,
        )
        DirectPaymentQuote.objects.create(intent=intent, **snapshot_values(quote))
        log_only(order, action=OrderStatusLog.Action.CREATE, operator=user,
                 operator_account=account, reason='创建待支付订单')
        return intent


def _notify_paid(order_id):
    # A committed outbox survives process death before this wake-up.
    try:
        from .direct_payment_delivery_models import DirectPaymentOutbox
        from .direct_payment_outbox import process_payment_outbox
        event = DirectPaymentOutbox.objects.filter(intent__order_id=order_id).first()
        if event:
            process_payment_outbox(event.pk)
    except Exception:
        logger.warning('直接支付订单通知失败: order_id=%s', order_id, exc_info=True)


def _channel_amount_evidence(intent, evidence):
    """Retain channel amounts without guessing missing cash or coupon data.

    WeChat normally omits promotion_detail when no voucher is used. It may
    therefore remain None when payer_total equals total. Any actual coupon
    remains in REVIEW until coupon funding and settlement rules are integrated.
    """
    issues = []
    value = evidence.payer_total_fen
    payer_total = value if type(value) is int and -(2**63) <= value < 2**63 else None
    if payer_total is None:
        issues.append('缺少或无效的用户实付金额')
    elif not 0 <= payer_total <= intent.amount_fen:
        issues.append('用户实付金额超出订单总额')
    payer_currency = evidence.payer_currency if isinstance(evidence.payer_currency, str) and len(evidence.payer_currency) <= 16 else None
    if payer_currency != 'CNY':
        issues.append('用户支付币种缺失或非人民币')
    details = evidence.promotion_detail
    if details is not None:
        try:
            if not isinstance(details, list) or any(not isinstance(item, dict) for item in details):
                raise ValueError('invalid promotion list')
            serialized = json.dumps(details, ensure_ascii=False, allow_nan=False)
            if len(serialized.encode('utf-8')) > 65536:
                raise ValueError('promotion list too large')
            # A JSON round trip prevents later mutation of the verified object.
            details = json.loads(serialized)
        except (TypeError, ValueError):
            details = None
            issues.append('渠道优惠明细格式异常')
    if payer_total is not None and 0 <= payer_total <= intent.amount_fen:
        if payer_total < intent.amount_fen:
            if not details:
                issues.append('渠道优惠明细缺失')
            else:
                amounts = [item.get('amount') for item in details]
                if any(type(amount) is not int or amount < 0 for amount in amounts) or sum(amounts) != intent.amount_fen - payer_total:
                    issues.append('渠道优惠金额与付款差额不一致')
                else:
                    issues.append('渠道优惠出资及结算规则待核对')
        elif details:
            # Do not infer whether zero-valued or unexpected promotions affect
            # settlement; preserve the evidence for explicit reconciliation.
            issues.append('渠道优惠明细与实付差额待核对')
    return payer_total, payer_currency, details, '; '.join(issues)[:255]


def confirm_verified_payment(order_id, evidence, *, received_at=None):
    """Apply trusted channel evidence once; record late success for review.

    HTTP adapters must authenticate and decrypt the channel message BEFORE
    constructing ``VerifiedPayment``. This test-only function performs no
    signature verification and is not a public endpoint.
    """
    _require_test_domain()
    # Direct internal callers use the current server clock. Inbox processing
    # supplies its server-generated durable receipt timestamp so delayed work
    # cannot make an anomalous future success_time appear normal later.
    received_at = timezone.now() if received_at is None else received_at
    if not isinstance(received_at, datetime) or timezone.is_naive(received_at):
        raise DirectPaymentError('付款证据接收时间无效')
    if (not isinstance(evidence, VerifiedPayment) or not isinstance(evidence.transaction_id, str)
            or not evidence.transaction_id.strip() or evidence.transaction_id != evidence.transaction_id.strip()
            or len(evidence.transaction_id) > 64 or type(evidence.amount_fen) is not int
            or not isinstance(evidence.paid_at, datetime) or timezone.is_naive(evidence.paid_at)):
        raise DirectPaymentError('支付凭证无效')
    try:
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order_id)
            intent = DirectPaymentIntent.objects.select_for_update().get(order=order)
            from .direct_payment_session_models import DirectPaymentSession
            session = DirectPaymentSession.objects.select_for_update().filter(intent=intent).first()
            from .direct_payment_quote import quote_matches_intent
            if (intent.merchant_order_no != evidence.merchant_order_no or intent.appid != evidence.appid
                    or intent.mchid != evidence.mchid or intent.openid != evidence.openid
                    or intent.amount_fen != evidence.amount_fen or intent.currency != evidence.currency
                    or intent.account_id != order.customer_account_id):
                raise DirectPaymentError('渠道支付凭证与订单不一致')
            time_outside_policy = (evidence.paid_at < intent.created_at - PAYMENT_TIME_SKEW
                                   or evidence.paid_at > received_at + PAYMENT_TIME_SKEW)
            valid_quote = quote_matches_intent(intent, order)
            if session:
                from .direct_payment_session import _snapshot_matches
                valid_quote = valid_quote and _snapshot_matches(session, intent, order)
            if intent.transaction_id:
                if intent.transaction_id != evidence.transaction_id or intent.paid_at != evidence.paid_at:
                    raise DirectPaymentError('支付凭证与已确认交易不一致')
                if (intent.payer_total_fen is not None and evidence.payer_total_fen is not None
                        and intent.payer_total_fen != evidence.payer_total_fen):
                    raise DirectPaymentError('渠道用户实付金额与已保存凭证不一致')
                if (intent.payer_currency is not None and evidence.payer_currency is not None
                        and intent.payer_currency != evidence.payer_currency):
                    raise DirectPaymentError('渠道用户支付币种与已保存凭证不一致')
                if (intent.promotion_detail is not None and evidence.promotion_detail is not None
                        and intent.promotion_detail != evidence.promotion_detail):
                    raise DirectPaymentError('渠道优惠明细与已保存凭证不一致')
                if (not valid_quote or time_outside_policy or intent.evidence_risk_at or (session and (session.cancel_requested_at or session.fake_closed_seen_at or session.fake_payment_risk_at))) and intent.status == DirectPaymentIntent.Status.PAID:
                    intent.status = DirectPaymentIntent.Status.REVIEW
                    intent.review_reason = ('CANCEL_PAYMENT_CONFLICT' if session and session.cancel_requested_at else
                                            'QUERY_CLOSE_PAYMENT_CONFLICT' if session and session.fake_closed_seen_at else
                                            'QUERY_PAYMENT_EVIDENCE_RISK' if intent.evidence_risk_at or (session and session.fake_payment_risk_at) else
                                            'PAYMENT_TIME_REVIEW' if time_outside_policy else
                                            'QUOTE_CONVERSION_UNVERIFIED')
                    intent.save(update_fields=['status', 'review_reason', 'updated_at'])
                    from .direct_payment_delivery_models import DirectPaymentOutbox
                    DirectPaymentOutbox.objects.filter(intent=intent).update(
                        status=DirectPaymentOutbox.Status.REVIEW, last_error=intent.review_reason,
                        lease_token='', lease_until=None)
                return intent
            intent.transaction_id = evidence.transaction_id
            intent.paid_at = evidence.paid_at
            (intent.payer_total_fen, intent.payer_currency,
             intent.promotion_detail, intent.evidence_issue) = _channel_amount_evidence(intent, evidence)
            if not valid_quote or time_outside_policy or intent.evidence_risk_at or (session and (session.cancel_requested_at or session.fake_closed_seen_at or session.fake_payment_risk_at)):
                # Authenticated payment is a fact even when its historical
                # conversion contract cannot be established. Retain it for
                # reconciliation, without making the order dispatchable.
                intent.status = DirectPaymentIntent.Status.REVIEW
                intent.review_reason = ('CANCEL_PAYMENT_CONFLICT' if session and session.cancel_requested_at else
                                        'QUERY_CLOSE_PAYMENT_CONFLICT' if session and session.fake_closed_seen_at else
                                        'QUERY_PAYMENT_EVIDENCE_RISK' if intent.evidence_risk_at or (session and session.fake_payment_risk_at) else
                                        'PAYMENT_TIME_REVIEW' if time_outside_policy else
                                        'QUOTE_CONVERSION_UNVERIFIED')
                intent.save(update_fields=['transaction_id', 'paid_at', 'status', 'review_reason', 'payer_total_fen',
                    'payer_currency', 'promotion_detail', 'evidence_issue', 'updated_at'])
                return intent
            # Retirement serializes on the buyer wallet before changing the
            # business cycle. Ban/unban serializes on the account row. Lock in
            # wallet -> account order after the order/intent, matching those
            # existing paths and preventing a stale eligibility decision.
            get_wallet(account_id=intent.account_id, user_id=order.customer_id, for_update=True)
            account = ClubAccount.objects.select_for_update().get(pk=intent.account_id)
            if (intent.evidence_issue or order.status != Order.Status.PENDING or order.payment_status != Order.PaymentStatus.UNPAID
                    or account.business_cycle != order.business_cycle or not account.is_active or not account.can_login
                    or not account.is_openid_bound or account.openid != intent.openid or account.wechat_appid != intent.appid):
                intent.status = DirectPaymentIntent.Status.REVIEW
                intent.review_reason = intent.evidence_issue or '订单或账号状态变化后收到付款，待核对和渠道退款'
            else:
                intent.status = DirectPaymentIntent.Status.PAID
                order.payment_status = Order.PaymentStatus.PAID
                order.pool_entered_at = timezone.now()
                from .services import pending_timeout_deadline
                from .timeout import order_timeout_minutes
                order.auto_cancel_at = pending_timeout_deadline(from_time=order.pool_entered_at, minutes=order_timeout_minutes(order))
                order.save(update_fields=['payment_status', 'pool_entered_at', 'auto_cancel_at', 'updated_at'])
                from .direct_payment_delivery_models import DirectPaymentOutbox
                DirectPaymentOutbox.objects.create(intent=intent, pool_entered_at=order.pool_entered_at,
                                                   reject_count=order.reject_count)
                transaction.on_commit(lambda: _notify_paid(order.pk))
            intent.save(update_fields=['transaction_id', 'paid_at', 'status', 'review_reason', 'payer_total_fen',
                                       'payer_currency', 'promotion_detail', 'evidence_issue', 'updated_at'])
            return intent
    except IntegrityError as exc:
        # Only identify an actual transaction collision after the atomic block
        # has rolled back. Other integrity failures remain visible to operators.
        if DirectPaymentIntent.objects.filter(transaction_id=evidence.transaction_id).exclude(order_id=order_id).exists():
            raise DirectPaymentError('渠道交易号已关联其他订单') from exc
        raise
