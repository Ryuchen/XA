"""Pending-order deadlines: new-order snapshot, legacy fallback and config access."""
import logging

from django.db import transaction
from django.utils import timezone

from .models import OrderTimeoutConfig

DEFAULT_TIMEOUT_MINUTES = 30
logger = logging.getLogger(__name__)


def record_timeout_notice(order, *, refunded_to_wallet):
    """Keep the result available to the customer after they return online.

    Called inside the successful cancellation transaction, after the wallet
    refund. Direct-payment refund reservations have a separate state and do
    not use this notice.
    """
    from site_messages.utils import create_message
    # The existing message helper treats delivery failures as non-fatal.
    # A savepoint keeps a database error there from aborting the refund.
    with transaction.atomic():
        create_message(
            recipient_id=order.customer_id,
            recipient_account_id=order.customer_account_id,
            title='订单超时已取消',
            preview=('订单超时未接单，已自动取消，款项已退回兴安币余额。'
                     if refunded_to_wallet else '订单超时未接单，已自动取消。'),
            msg_type='ORDER',
            action_url=f'/pages/orderList/index?orderId={order.pk}',
            related_order_id=order.pk,
        )


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
    from .models import Order
    from club_accounts.models import ClubAccount
    from wallet.models import Wallet

    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if (order.status != Order.Status.PENDING or not order.auto_cancel_at
                or order.auto_cancel_at > timezone.now()
                or order.payment_status != Order.PaymentStatus.PAID
                or order.provider_id or order.provider_account_id or order.providers.exists()):
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
