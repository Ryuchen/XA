"""Continue a captured ordinary payment after configuration changes."""
from stores.context import current_store
from stores.db import transaction
from contextlib import contextmanager
from contextvars import ContextVar

_active = ContextVar('ordinary_funding_continuation', default=None)


def permits_unmodified_order(order, *, allow_open_case=False, allow_paid_addons=False):
    """Verify an unrefunded, untransferred order inside its tenant transaction.

    This authorizes no new payment and no refund.  Callers must already hold
    the order row; the existing settlement still rechecks every source and
    funding receipt before posting money.
    """
    store = current_store()
    connection = transaction.get_connection()
    if (not order or not order.pk or not connection.in_atomic_block or not store
            or store.database_alias != connection.alias or order.is_gift_snapshot or order.source_order_id
            or order.status != 'COMPLETED' or order.payment_status != 'PAID' or order.settled_at
            or order.funding_terms is None or not order.customer_account_id
            or order.refund_records.exists()
            or (not allow_paid_addons and order.addon_intents.filter(payment__isnull=False).exists())
            or order.aftersales.exclude(status__in=('RESOLVED', 'OPEN') if allow_open_case else ('RESOLVED',)).exists()
            or order.status_logs.filter(action='TRANSFER').exists()):
        return False
    from .models import OrderOriginalAttribution, OrderOriginalShareTransfer, OrderPrestartHandoff
    from .points_service_payment import needs_integrated_processing
    if (needs_integrated_processing(order) or OrderPrestartHandoff.objects.filter(order=order).exists()
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        return False
    if allow_paid_addons:
        from .ordinary_addon_production import eligible as ordinary_addon_eligible
        if (not order.addon_intents.filter(payment__isnull=False).exists()
                or not ordinary_addon_eligible(order, allow_open=allow_open_case)):
            return False
    from .financial_lock import lock_order_money
    from .service_funding_plan import plan_verified_ordinary_order
    from .points_funding_plan import PointsFundingPlanError
    from .settlement import SettlementError
    lock_order_money()
    # Match the settlement entry: customer wallet precedes payment and source
    # rows, avoiding a source-to-wallet reversal against payment/refund paths.
    from wallet.services import get_wallet
    get_wallet(account_id=order.customer_account_id, user_id=order.customer_id, for_update=True)
    from club_accounts.models import ClubAccount
    if not ClubAccount.objects.select_for_update().filter(pk=order.customer_account_id,
            account_type=ClubAccount.AccountType.BOSS, business_cycle=order.business_cycle).exists():
        return False
    try:
        plan_verified_ordinary_order(order.pk)
        from .models import OrderProvider, OrderSegmentFinancialSnapshot
        from .points_settlement_test import _ordinary_snapshots_verified
        if not OrderOriginalAttribution.objects.filter(order=order, status='ACTIVE').exists():
            return False
        snapshots = list(OrderSegmentFinancialSnapshot.objects.select_for_update()
            .filter(segment__attribution__order=order, segment__released_at__isnull=True)
            .select_related('segment').order_by('segment__slot'))
        assignments = {row.provider_account_id: row for row in OrderProvider.objects.select_for_update()
            .filter(order=order, released_at__isnull=True)}
        _ordinary_snapshots_verified(order, snapshots, assignments)
    except (PointsFundingPlanError, SettlementError):
        return False
    return True


def continuation_active(order):
    connection = transaction.get_connection()
    store = current_store()
    active = _active.get()
    return bool(order and store and connection.in_atomic_block and active
                and active[:3] == (connection.alias, store.slug, order.pk))


def refunded_continuation_active(order):
    return bool(continuation_active(order) and _active.get()[3] == 'refunded')


def addon_settlement_active(order):
    return bool(continuation_active(order) and _active.get()[3] == 'unrefunded_addon')


@contextmanager
def verified_continuation(order, *, allow_open_case=False, allow_paid_addons=False):
    if not permits_unmodified_order(order, allow_open_case=allow_open_case, allow_paid_addons=allow_paid_addons):
        from .settlement import SettlementError
        raise SettlementError('普通订单付款及履约来源待核对')
    kind = 'unrefunded_addon' if allow_paid_addons else 'unrefunded'
    token = _active.set((transaction.get_connection().alias, current_store().slug, order.pk, kind))
    try:
        yield
    finally:
        _active.reset(token)


@contextmanager
def verified_refunded_continuation(order, *, read_only=False, allow_open_case=False, allow_paid_addons=False):
    """Verify one closed ordinary negotiation before continuing settlement."""
    from .settlement import SettlementError
    store = current_store()
    connection = transaction.get_connection()
    if (not order or not order.pk or not connection.in_atomic_block or not store
            or store.database_alias != connection.alias or order.is_gift_snapshot or order.source_order_id
            or not order.customer_account_id or order.funding_terms is None
            or order.status != 'COMPLETED' or order.payment_status not in ('PARTIALLY_REFUNDED', 'REFUNDED')
            or (not allow_paid_addons and order.addon_intents.filter(payment__isnull=False).exists())
            or (not allow_open_case and order.aftersales.filter(status='OPEN').exists())):
        raise SettlementError('普通退款订单不在已核实的结算范围')
    from .financial_lock import lock_existing_order_money, lock_order_money
    (lock_existing_order_money if read_only else lock_order_money)()
    token = _active.set((connection.alias, store.slug, order.pk, 'refunded'))
    try:
        from .points_checkout_test import ordinary_refund_production_order
        from .ordinary_refunded_addon_funding import plan_locked
        from .points_funding_plan import PointsFundingPlanError
        from .ordinary_addon_production import eligible as ordinary_addon_eligible
        if not (ordinary_refund_production_order(order, allow_open=allow_open_case)
                or (allow_paid_addons and ordinary_addon_eligible(order, allow_open=allow_open_case))):
            raise SettlementError('普通退款订单的履约或退款凭证待核对')
        try:
            plan_locked(order.pk, _allow_settled=True, _allow_finalized=bool(order.settled_at))
        except PointsFundingPlanError as exc:
            raise SettlementError('普通退款订单资金来源和履约凭证待核对') from exc
        yield
    finally:
        _active.reset(token)
