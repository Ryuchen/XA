"""Observe committed payments and stage their first grab-pool round."""
import logging
import os

from django.conf import settings
from django.db import DatabaseError
from stores.db import connection, transaction
from wallet.models import Transaction

from .models import Order, OrderGrabPoolOpeningIntent, OrderGrabPoolSnapshot, OrderGrabRoundOpeningEvidence, Reservation
from .grab_receipt_probe import _db_clock

logger = logging.getLogger(__name__)


def isolated_payment_hook_enabled():
    """The integration is for disposable test databases only."""
    if not getattr(settings, 'GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY', False):
        return False
    db = transaction.get_connection()
    module = os.environ.get('DJANGO_SETTINGS_MODULE', '')
    if db.vendor == 'sqlite':
        return (module in {'config.test_settings', 'config.multitenant_test_settings', 'config.migration_test_settings'}
                and db.settings_dict.get('NAME') in {':memory:', 'file:memorydb_default?mode=memory&cache=shared',
                                                       'file:memorydb_store_b?mode=memory&cache=shared'})
    if db.vendor == 'mysql':
        from common.mysql_test_support import require_disposable_mysql
        try:
            require_disposable_mysql(db)
        except AssertionError:
            return False
        return True
    return False


def _verify_zero_subsidy(order, payment, *, require_opening=True):
    """Prove an actual consumed safety-box coupon and its funded zero payment."""
    from .grab_clock_contract import production_enabled
    from .grab_single_flow import enabled as single_enabled
    from .segment_financial_capture import subsidy_capture_enabled
    from .models import OrderPlatformSubsidy, OrderSegmentFinancialCoverage
    from coupons.models import Coupon, UserCoupon
    if ((require_opening and (not production_enabled() or not single_enabled() or not subsidy_capture_enabled()
            or not getattr(settings, 'ORIGINAL_ATTRIBUTION_ENABLED', False)))
            or order.required_provider_count != 1 or order.dispatch_route not in (Order.DispatchRoute.PUBLIC, Order.DispatchRoute.STAFF)
            or order.game_rounds != 1 or not order.commission_allocation_pending or order.amount != 0):
        raise ValueError('零实付安全箱开池条件尚未满足')
    subsidy = OrderPlatformSubsidy.objects.select_for_update().filter(order=order).first()
    coverage = OrderSegmentFinancialCoverage.objects.select_for_update().filter(attribution__order=order).first()
    root = order.game_commission_snapshot
    frozen = root.get('personal_rules') if isinstance(root, dict) else None
    coupon_id = (UserCoupon.objects.filter(pk=subsidy.coupon_id_snapshot).values_list('coupon_id', flat=True).first()
                 if subsidy else None)
    if coupon_id:
        Coupon.objects.select_for_update().filter(pk=coupon_id).first()
    coupon = (UserCoupon.objects.select_for_update().select_related('coupon').filter(
        pk=subsidy.coupon_id_snapshot, account_id=order.customer_account_id,
        business_cycle=order.business_cycle, order=order, status='USED').first() if subsidy else None)
    if (not isinstance(frozen, dict) or frozen.get('kind') != 'DELTA_PERSONAL_V1'
            or frozen.get('game') != 'DELTA' or root.get('game') != 'DELTA'
            or root.get('service_scope') != frozen.get('service_scope')
            or root.get('version') != frozen.get('version') or not isinstance(frozen.get('rules'), list)
            or not subsidy or not coupon or not coupon.used_at or coupon.coupon.scope != 'SAFE_BOX'
            or coupon.coupon.discount_type != 'RATE' or coupon.coupon.discount_rate != 0
            or not (coupon.coupon.grant_key or '').startswith(
                f'vip-safe:{order.customer_account_id}:{order.business_cycle}:')
            or not coverage or coverage.source != 'PLATFORM_SUBSIDY' or coverage.status != 'PENDING'
            or coverage.attribution.status != 'PENDING' or coverage.deal_amount != subsidy.basis
            or subsidy.basis <= 0 or subsidy.basis != order.original_amount
            or order.original_amount != order.boss_discount + order.promo_discount + order.coupon_discount
            or payment.amount != 0 or payment.balance_before != payment.balance_after
            or payment.reversal_of_id or payment.reversals.exists()
            or Transaction.objects.filter(order=order, tx_type=Transaction.TxType.PAY,
                status=Transaction.Status.SUCCESS).count() != 1):
        raise ValueError('零实付安全箱券、补贴或付款凭据不一致')


def _verify_zero_ordinary(order, payment):
    """Validate the zero payment and the discounts consumed in its transaction."""
    from coupons.models import UserCoupon
    from .models import OrderPlatformSubsidy

    coupons = list(UserCoupon.objects.select_for_update().filter(order=order, status='USED').order_by('pk'))
    if (order.commission_allocation_pending or order.experience_kind
            or order.original_amount <= 0
            or order.original_amount != order.boss_discount + order.promo_discount + order.coupon_discount
            or payment.amount != 0 or payment.balance_before != payment.balance_after
            or payment.reversal_of_id or payment.reversals.exists()
            or Transaction.objects.filter(order=order, tx_type=Transaction.TxType.PAY,
                status=Transaction.Status.SUCCESS).count() != 1
            or OrderPlatformSubsidy.objects.filter(order=order).exists()
            or len(coupons) > 1
            or (order.coupon_discount > 0 and len(coupons) != 1)
            or any(c.account_id != order.customer_account_id or c.business_cycle != order.business_cycle
                   or not c.used_at for c in coupons)):
        raise ValueError('零实付订单优惠或付款凭据不一致')


def _verify_zero_payment(order, payment):
    if order.commission_allocation_pending:
        _verify_zero_subsidy(order, payment)
    else:
        _verify_zero_ordinary(order, payment)

def capture_isolated_payment(order, payment, pool):
    """Stage evidence with payment; enqueue observation only after commit."""
    if getattr(settings, 'GRAB_COMMITTED_OPENING_ENABLED', False):
        if (pool or order.amount < 0 or order.provider_id or order.provider_account_id
                or order.requires_boss_selection or order.is_gift_snapshot or order.source_order_id
                or order.required_provider_count not in (1, 2) or Reservation.objects.filter(order=order).exists()):
            return None
        if order.amount == 0:
            from .points_service_payment import needs_integrated_processing
            if order.experience_kind or needs_integrated_processing(order):
                return None
            try:
                _verify_zero_payment(order, payment)
            except ValueError as exc:
                from rest_framework.exceptions import ValidationError
                raise ValidationError(str(exc)) from exc
        from .models import OrderPaidServiceScope
        if not OrderPaidServiceScope.objects.filter(order=order).exists():
            from rest_framework.exceptions import ValidationError
            raise ValidationError('自动抢单的付款服务范围未配置，请联系门店')
        intent = record_in_payment_transaction(order.pk, payment.pk)
        def enqueue():
            from .tasks import open_committed_grab_order
            open_committed_grab_order.delay(order.pk)
        transaction.on_commit(enqueue, robust=True)
        return intent
    if not pool or not isolated_payment_hook_enabled() or order.amount <= 0:
        return None
    intent = record_in_payment_transaction(order.pk, payment.pk)
    transaction.on_commit(lambda: observe_committed_payment(order.pk), robust=True)
    return intent


def _order_and_payment(order_id, payment_id):
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    payment = (Transaction.objects.select_for_update().select_related('wallet')
               .filter(pk=payment_id, order_id=order_id).first())
    if (not order or not payment or order.status != Order.Status.PENDING
            or order.payment_status != Order.PaymentStatus.PAID or order.provider_id
            or order.provider_account_id or order.providers.exists() or order.requires_boss_selection
            or order.is_gift_snapshot or order.source_order_id or order.required_provider_count not in (1, 2)
            or Reservation.objects.filter(order_id=order_id).exists()
            or order.amount < 0 or payment.tx_type != Transaction.TxType.PAY
            or payment.status != Transaction.Status.SUCCESS or payment.amount != -order.amount
            or payment.wallet.account_id != order.customer_account_id or not order.customer_account_id
            or payment.balance_before - payment.balance_after != order.amount):
        raise ValueError('订单或付款凭据不符合待开池条件')
    if order.amount == 0:
        _verify_zero_payment(order, payment)
    return order, payment


def record_in_payment_transaction(order_id, payment_id):
    """Caller must create this in the transaction that writes the payment.

    Requiring an atomic block cannot by itself prove caller provenance, so
    this isolated-test preparation is not authority to activate a window.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('待开池意向须在付款事务内登记')
    order, payment = _order_and_payment(order_id, payment_id)
    intent, created = OrderGrabPoolOpeningIntent.objects.get_or_create(
        order=order, defaults={'payment': payment, 'paid_amount': order.amount})
    if not created and (intent.payment_id != payment.pk or intent.paid_amount != order.amount):
        raise ValueError('已登记付款与当前待开池意向不一致')
    return intent


def _clock_observation():
    observed, precision, driver = _db_clock()
    if connection.vendor == 'mysql':
        with connection.cursor() as cursor:
            cursor.execute('SELECT @@server_uuid')
            identity = cursor.fetchone()[0]
            if connection.connection is not driver:
                raise DatabaseError('观察付款提交时数据库连接变化')
    elif connection.vendor == 'sqlite':
        identity = 'sqlite:' + str(connection.settings_dict['NAME'])
    else:
        raise ValueError('门店数据库时钟不可观察')
    if not isinstance(identity, str) or not identity or len(identity) > 100:
        raise ValueError('门店数据库身份无效')
    return observed, precision, identity


def observe_committed_payment(order_id):
    """Observe a committed intent from a new transaction; never open the pool.

    The marker and its matching payment are visible only after their creating
    transaction commits. Repeated or concurrent observations retain the first
    committed upper bound. Failure leaves PENDING recoverable on the next run.
    """
    db = transaction.get_connection()
    if db.in_atomic_block or not db.get_autocommit():
        raise RuntimeError('付款提交观察须使用独立最外层事务')
    with transaction.atomic(durable=True):
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        intent = OrderGrabPoolOpeningIntent.objects.select_for_update().filter(order_id=order_id).first()
        if not order or not intent:
            raise ValueError('待开池付款尚未提交或不存在')
        _observe_locked_payment(order, intent)
        return intent


def _observe_locked_payment(order, intent):
    _order_and_payment(order.pk, intent.payment_id)
    if intent.paid_amount != order.amount:
        raise ValueError('付款金额与待开池意向不一致')
    if intent.state == 'OBSERVED':
        return
    if intent.state != 'PENDING':
        raise ValueError('待开池意向需要人工核对')
    observed, precision, identity = _clock_observation()
    intent.state, intent.commit_observed_at = 'OBSERVED', observed
    intent.clock_precision_us, intent.database_identity = precision, identity
    intent.save(update_fields=['state', 'commit_observed_at', 'clock_precision_us', 'database_identity'])


def _lock_pending_opening(order_id):
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if not order or order.status != Order.Status.PENDING:
        return None, None
    return order, OrderGrabPoolOpeningIntent.objects.select_for_update().filter(order=order).first()


def _committed_pool_result(intent):
    pool_id = (OrderGrabPoolSnapshot.objects.filter(order_id=intent.order_id)
               .values_list('pk', flat=True).first())
    if pool_id is None:
        return None
    evidence = (OrderGrabRoundOpeningEvidence.objects.filter(round__pool_id=pool_id, round__number=1)
                .select_related('round').first())
    if intent.state != 'OBSERVED' or not evidence or evidence.reason:
        return 'REVIEW'
    if evidence.state in ('PENDING', 'PREPARED'):
        return 'WAIT_OPENING'
    if (evidence.state != 'OBSERVED' or not intent.database_identity or not intent.commit_observed_at
            or not evidence.upper_at or not evidence.activation_lower_at or not evidence.activation_upper_at
            or not intent.commit_observed_at <= evidence.round.opened_at <= evidence.lower_at
            or evidence.activation_lower_at < evidence.upper_at):
        return 'REVIEW'
    from .grab_clock_contract import valid_bounds
    identity = intent.database_identity
    if (not valid_bounds(evidence.lower_at, evidence.upper_at, evidence.clock_evidence, identity=identity)
            or not valid_bounds(evidence.activation_lower_at, evidence.activation_upper_at,
                                evidence.activation_clock_evidence, identity=identity)):
        return 'REVIEW'
    return 'OPENED'


def _window_enabled(order):
    from .grab_clock_contract import production_enabled
    from .grab_single_flow import enabled as single_window_enabled
    from .grab_team_flow import enabled as team_window_enabled

    return production_enabled() and (
        (order.required_provider_count == 1 and single_window_enabled())
        or (order.required_provider_count == 2 and team_window_enabled()))


def _prepare_opening(order_id):
    # Commit the first payment upper bound independently of a later pool
    # configuration or staging failure.
    with transaction.atomic(durable=True):
        order, intent = _lock_pending_opening(order_id)
        if intent is None:
            return 'NOT_PENDING'
        if intent.state not in ('PENDING', 'OBSERVED'):
            return 'REVIEW'
        if result := _committed_pool_result(intent):
            return result
        if not _window_enabled(order):
            return 'DISABLED'
        if intent.state == 'PENDING':
            _observe_locked_payment(order, intent)


def open_committed_payment(order_id):
    """Stage the first pool once and report its persisted activation state."""
    if not getattr(settings, 'GRAB_COMMITTED_OPENING_ENABLED', False):
        return 'DISABLED'
    db = transaction.get_connection()
    if db.in_atomic_block or not db.get_autocommit():
        raise RuntimeError('已付款开池须使用独立最外层事务')
    try:
        if result := _prepare_opening(order_id):
            return result
        with transaction.atomic(durable=True):
            order, intent = _lock_pending_opening(order_id)
            if intent is None:
                return 'NOT_PENDING'
            if intent.state != 'OBSERVED':
                return 'REVIEW'
            # Another worker can open after the payment observation commits.
            # A replay must preserve the original round and its clock bounds.
            if result := _committed_pool_result(intent):
                return result
            if not _window_enabled(order):
                return 'DISABLED'
            if intent.paid_amount != order.amount:
                raise ValueError('付款金额与待开池意向不一致')
            _order_and_payment(order.pk, intent.payment_id)
            observed, precision, identity = _clock_observation()
            if (identity != intent.database_identity or precision != intent.clock_precision_us
                    or observed < intent.commit_observed_at):
                intent.state, intent.reason = 'REVIEW', '数据库身份或付款观察时间发生变化'
                intent.save(update_fields=['state', 'reason'])
                return 'REVIEW'
            from .grab_pool_snapshot import initialize_entry
            pool = initialize_entry(order, committed_opening=True)
            if not pool:
                return 'REVIEW'
            from .grab_group_dispatch import enabled as group_dispatch_enabled
            if group_dispatch_enabled():
                from .kook_dispatch import enqueue_kook_dispatch
                from .dispatching import enqueue_wecom_dispatch
                from .models import KookDispatchRecord
                transaction.on_commit(lambda: enqueue_kook_dispatch(order, KookDispatchRecord.Trigger.NEW_ORDER), robust=True)
                transaction.on_commit(lambda: enqueue_wecom_dispatch(order, KookDispatchRecord.Trigger.NEW_ORDER), robust=True)
        return _committed_pool_result(intent)
    except (DatabaseError, ValueError):
        logger.exception('已付款订单开池待重试 order_id=%s', order_id)
        return 'RETRY'
