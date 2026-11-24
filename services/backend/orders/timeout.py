"""Pending-order deadlines: new-order snapshot, legacy fallback and config access."""
import logging

from stores.db import transaction
from django.utils import timezone

from .models import OrderTimeoutConfig

DEFAULT_TIMEOUT_MINUTES = 30
logger = logging.getLogger(__name__)


def record_timeout_notice(order, *, refund=None, refunded_to_wallet=None, refunded_points=None):
    """Capture a timeout notice from the cancellation and actual refund facts."""
    from site_messages.utils import create_cycle_message
    from stores.context import current_currency_name
    if refund is None:
        refund = order.refund_records.order_by('-pk').first()
    if refunded_to_wallet is None:
        refunded_to_wallet = bool(refund and refund.amount)
    from .points_payment_models import OrderPointsRefund
    points = OrderPointsRefund.objects.filter(refund_id=refund.pk).first() if refund else None
    if points is not None:
        refunded_to_wallet = bool(points.coin_units)
        refunded_points = bool(points.points_hundredths)
    elif refunded_points is None:
        refunded_points = False
    preview = (f'订单超时未接单，已自动取消，款项已退回{current_currency_name()}余额。'
               + ('抵扣积分已退回。' if refunded_points else '') if refunded_to_wallet
               else ('订单超时未接单，已自动取消，抵扣积分已退回。' if refunded_points
                     else '订单超时未接单，已自动取消。'))
    from site_messages.order_timeout_notice import enabled, record
    if enabled():
        record(order, refund=refund, refunded_to_wallet=refunded_to_wallet,
               refunded_points=refunded_points, preview=preview)
    else:
        # Preserve the existing independent savepoint around immediate delivery.
        with transaction.atomic():
            create_cycle_message(recipient_id=order.customer_id, recipient_account_id=order.customer_account_id,
                recipient_business_cycle=order.business_cycle, title='订单超时已取消', preview=preview,
                msg_type='ORDER', action_url=f'/pages/orderList/index?orderId={order.pk}', related_order_id=order.pk)


def expired_order_candidates(now=None):
    from .models import Order
    # REVIEW remains visible to operations but is not re-enqueued forever.
    return Order.objects.filter(status=Order.Status.PENDING, auto_cancel_at__lte=now or timezone.now()).exclude(
        direct_payment__status='REVIEW',
        direct_payment__review_reason__in=['FEE_REFUND_POLICY_PENDING', 'FEE_QUOTE_SNAPSHOT_CONFLICT'],
    )


def current_timeout_config(*, lock=False):
    if not lock:
        row = OrderTimeoutConfig.objects.filter(pk=1).first()
        if row is not None:
            return row
        return OrderTimeoutConfig.objects.get_or_create(pk=1)[0]
    OrderTimeoutConfig.objects.get_or_create(pk=1)
    return OrderTimeoutConfig.objects.select_for_update().get(pk=1)


def order_timeout_minutes(order):
    # Historical rows are deliberately not backfilled from today's config.
    return order.pending_timeout_minutes or DEFAULT_TIMEOUT_MINUTES


def reserve_expired_direct_refund(order_id):
    """Test-only: cancel an expired direct order and reserve its refund, no network.

    The scanner's read is only a hint. Recheck the current deadline and all
    assignment facts while holding the same order lock used by grabs. Repeated
    scans cannot create another refund: only the first locked pending round
    is eligible. The existing refund service additionally enforces the test
    gate, identity, full-refund constraints and stable merchant refund ID.
    """
    # A disabled test domain is not evidence of a conflicting quote. Check
    # the gate before classifying the quote or mutating its payment intent.
    from .direct_payment import _require_test_domain
    _require_test_domain()
    from .direct_payment_models import DirectPaymentIntent
    from .direct_refund import reserve_full_refund
    from .grab_round_facts import pool_exists
    from .models import Order, OrderGrabPoolOpeningIntent
    from club_accounts.models import ClubAccount
    from wallet.models import Wallet

    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if (order.status != Order.Status.PENDING or not order.auto_cancel_at
                or order.auto_cancel_at > timezone.now()
                or order.payment_status != Order.PaymentStatus.PAID
                or order.provider_id or order.provider_account_id or order.providers.exists()):
            return None
        if pool_exists(order.pk) or OrderGrabPoolOpeningIntent.objects.filter(order=order).exists():
            return None
        intent = DirectPaymentIntent.objects.select_for_update().filter(order=order).first()
        if intent is None:
            return None
        from .direct_payment_quote_models import DirectPaymentQuote
        from .direct_payment_quote import quote_matches_intent
        fee_quote = DirectPaymentQuote.objects.filter(intent=intent).first()
        quote_valid = quote_matches_intent(intent, order)
        if not quote_valid or (fee_quote and fee_quote.fee_fen):
            # No guessed refund of the surcharge. Persist a review item and
            # stop new dispatch while retaining the real paid/order facts.
            intent.status = DirectPaymentIntent.Status.REVIEW
            intent.review_reason = 'FEE_REFUND_POLICY_PENDING' if quote_valid else 'FEE_QUOTE_SNAPSHOT_CONFLICT'
            intent.save(update_fields=['status', 'review_reason', 'updated_at'])
            return None
        if intent.account_id != order.customer_account_id:
            logger.error('超时直付订单付款账号不一致，需人工核对 order_id=%s', order.pk)
            return None
        # Account retirement takes wallet then account; preserve the same
        # order -> intent -> wallet -> account lock order as payment confirm.
        wallet = Wallet.objects.select_for_update().filter(account_id=intent.account_id, user_id=order.customer_id).first()
        if wallet is None:
            logger.error('超时直付订单钱包身份不完整，需人工核对 order_id=%s', order.pk)
            return None
        account = ClubAccount.objects.select_for_update().get(pk=intent.account_id)
        if account.business_cycle != order.business_cycle:
            logger.error('超时直付订单账号周期已变化，需人工核对 order_id=%s', order.pk)
            return None
        reserve_full_refund(order.pk, request_id=f'timeout-{order.pk}', reason='超时未接单自动退款')
        order.refresh_from_db()
        return order
