"""Isolated-test-only cancellation refund for an unassigned points-paid order.

The caller must hold the order lock inside transition(). This does not open
checkout, general aftersale refunds or settlement.
"""
import os
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .financial_lock import lock_order_money
from .models import Order, OrderPointsRedemption, OrderRefund
from .settlement import SettlementError


def _require_isolated_test(*, customer_cancel=False):
    # The active tenant may use another alias; never validate the default
    # connection and then mutate a different store's database.
    connection = transaction.get_connection()
    database = connection.settings_dict
    safe_memory = {('default', ':memory:'), ('default', 'file:memorydb_default?mode=memory&cache=shared'),
                   ('store_b', 'file:memorydb_store_b?mode=memory&cache=shared')}
    flag = 'SERVICE_ORDER_POINTS_CANCEL_TESTING' if customer_cancel else 'SERVICE_ORDER_POINTS_TIMEOUT_TESTING'
    module = os.environ.get('DJANGO_SETTINGS_MODULE')
    memory_test = (module in ('config.test_settings', 'config.migration_test_settings', 'config.multitenant_test_settings')
                   and (getattr(connection, 'alias', None), database.get('NAME')) in safe_memory)
    file_test = False
    file_name = os.environ.get('XA_ASGI_NETWORK_TEST_DB', '')
    if (customer_cancel and module == 'config.asgi_network_test_settings'
            and os.environ.get('XA_SERVICE_POINTS_CANCEL_BROWSER') == '1' and file_name):
        expected = Path(file_name).resolve()
        file_test = (getattr(connection, 'alias', None) == 'default' and expected == Path(database.get('NAME', '')).resolve()
                     and expected.parent == (Path(settings.BASE_DIR).parent.parent / '.tools').resolve()
                     and expected.name.startswith('asgi-reservation-') and expected.suffix == '.sqlite3')
    if (getattr(settings, flag, False) is not True or database.get('ENGINE') != 'django.db.backends.sqlite3'
            or not (memory_test or file_test)):
        raise SettlementError('积分订单取消退款尚未开放')


def refund_expired_locked(order, *, grab_team_state=None, grab_single_state=None):
    return _refund_unassigned_locked(order, customer_cancel=False, grab_team_state=grab_team_state, grab_single_state=grab_single_state)


def refund_customer_cancel_locked(order, *, operator=None, operator_account=None, grab_team_state=None, grab_single_state=None):
    return _refund_unassigned_locked(order, customer_cancel=True, operator=operator,
                                     operator_account=operator_account, grab_team_state=grab_team_state, grab_single_state=grab_single_state)


def refund_staff_review_locked(order, *, operator, operator_account, reason, grab_team_state=None,
                               grab_single_state=None, opening_intent=None):
    from console.permissions import get_account_permissions, is_console_account
    if (not operator_account or not is_console_account(operator_account)
            or not {'order:dispatch', 'order:refund'}.issubset(get_account_permissions(operator_account))
            or not isinstance(reason, str) or not reason.strip() or len(reason) > 500):
        raise SettlementError('抢单核对退款权限或原因无效')
    return _refund_unassigned_locked(order, customer_cancel=True, operator=operator, operator_account=operator_account,
        grab_team_state=grab_team_state, grab_single_state=grab_single_state, staff_review=True,
        review_reason=reason, opening_intent=opening_intent)


def _refund_unassigned_locked(order, *, customer_cancel, operator=None, operator_account=None, grab_team_state=None,
                              grab_single_state=None, staff_review=False, review_reason='', opening_intent=None):
    """Return original assets and coupon as one all-or-nothing operation."""
    from .points_business_access import permits
    formal = permits(order, phase='assignment', kind='service')
    if not formal:
        _require_isolated_test(customer_cancel=customer_cancel)
    if not transaction.get_connection().in_atomic_block:
        raise SettlementError('积分订单退款必须在门店事务内执行')
    persisted = Order.objects.select_for_update().get(pk=order.pk)
    if (persisted.status != Order.Status.PENDING or persisted.payment_status != Order.PaymentStatus.PAID
            or (not customer_cancel and (not persisted.auto_cancel_at or persisted.auto_cancel_at > timezone.now()))
            or persisted.provider_id or persisted.provider_account_id or persisted.providers.exists()
            or persisted.in_service_at or persisted.completed_at or persisted.settled_at
            or persisted.consumption_confirmed_at or persisted.refund_records.exists()
            or persisted.aftersales.exists()):
        raise SettlementError('积分订单状态或历史需要核对')
    from engagement.models import ConsumptionConfirmation, PointLedger
    from .direct_payment_models import DirectPaymentIntent
    from .grab_pool_models import OrderGrabPoolSnapshot, OrderGrabTeamRoundState, OrderGrabSingleRoundState
    from wallet.models import ProviderReport
    pool = OrderGrabPoolSnapshot.objects.filter(order_id=order.pk).first()
    pool_statuses = ['OPEN', 'PARKED', 'REVIEW'] if staff_review else ['OPEN', 'PARKED']
    allowed_pool = bool(pool and isinstance(grab_team_state, OrderGrabTeamRoundState)
        and OrderGrabTeamRoundState.objects.select_for_update().filter(pk=grab_team_state.pk,
            round__pool=pool, status__in=pool_statuses).exists())
    allowed_pool = allowed_pool or bool(pool and isinstance(grab_single_state, OrderGrabSingleRoundState)
        and OrderGrabSingleRoundState.objects.select_for_update().filter(pk=grab_single_state.pk,
            round__pool=pool, status__in=pool_statuses).exists())
    allowed_opening = False
    if staff_review and not pool and opening_intent:
        from .models import OrderGrabPoolOpeningIntent
        allowed_opening = OrderGrabPoolOpeningIntent.objects.select_for_update().filter(
            pk=opening_intent.pk, order_id=order.pk, state__in=['PENDING', 'OBSERVED', 'REVIEW']).exists()
    if staff_review and not (allowed_pool or allowed_opening):
        raise SettlementError('抢单核对轮次已变化，请重新核对')
    if (ConsumptionConfirmation.objects.filter(order_id=order.pk).exists()
            or PointLedger.objects.filter(order_id=order.pk, event_key=f'order:{order.pk}:points').exists()
            or ProviderReport.objects.filter(order_id=order.pk).exists()
            or DirectPaymentIntent.objects.filter(order_id=order.pk).exists()
            or (pool and not allowed_pool)):
        raise SettlementError('积分订单履约或实销记录需要核对')
    lock_order_money()
    receipt = OrderPointsRedemption.objects.select_for_update().filter(order_id=order.pk).first()
    if not receipt:
        raise SettlementError('积分订单付款凭据缺失')
    funding_facts = None
    if formal:
        from .points_prestart_cancel_funding import capture_locked
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            funding_facts = capture_locked(persisted)
        except (ValidationError, DjangoValidationError) as exc:
            raise SettlementError('积分订单补资分配待核对') from exc
    reason = review_reason if staff_review else '客户取消订单' if customer_cancel else '超时未接单自动退款'
    refund = OrderRefund.objects.create(order_id=order.pk, amount=receipt.paid_units, reason=reason,
                                        operator=operator, operator_account=operator_account)
    from .points_service_refund import refund_assets_locked
    try:
        result = refund_assets_locked(order_id=order.pk, refund_id=refund.pk, equivalent_units=receipt.deal_units)
    except (ValidationError, ObjectDoesNotExist) as exc:
        raise SettlementError('积分订单原付款或退款凭据需要核对') from exc
    if not result.is_final or result.coin_units != order.amount or result.points_hundredths != receipt.points_hundredths:
        raise SettlementError('积分订单退款构成不完整')
    from coupons.benefits import restore_order_coupons
    from coupons.models import UserCoupon
    coupons = list(UserCoupon.objects.select_for_update().filter(order_id=order.pk,
        status=UserCoupon.Status.USED).select_related('coupon').order_by('pk'))
    restore_order_coupons(persisted)
    refund.benefit_effects = {'order_coupon_results': [
        {'name': row.coupon.name, 'outcome': 'RETURNED' if row.restore_on_refund else 'NOT_RETURNED'}
        for row in coupons], 'points_prestart_cancel_funding': bool(funding_facts)}
    refund.save(update_fields=['benefit_effects'])
    if funding_facts:
        from .points_prestart_cancel_funding import record_locked
        try:
            record_locked(result, funding_facts)
        except (ValidationError, DjangoValidationError) as exc:
            raise SettlementError('积分订单补资释放待核对') from exc
    from .original_attribution import mark_review
    mark_review(persisted, '退款后的个人原价份额尚未核对')
    order.payment_status = Order.PaymentStatus.REFUNDED
    order.refunded_at = timezone.now()
    from wallet.notifier import notify_wallet_update
    if result.coin_transaction_id:
        wallet = result.coin_transaction.wallet
        transaction.on_commit(lambda: notify_wallet_update(wallet))
    return refund
