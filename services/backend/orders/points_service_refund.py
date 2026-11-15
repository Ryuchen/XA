"""Internal return of the original assets of a points-paid service order.

Not an authorization or an API: the caller must lock the order, establish the
aftersale/refund entitlement and provider recovery, create OrderRefund with
its *coin* amount, then call this in the same tenant transaction. Failure
must abort the whole transaction. The caller also owns status, coupons, VIP,
earned-points reversal, provider recovery, notifications and idempotency.
"""
from rest_framework.exceptions import ValidationError

from club_accounts.models import ClubAccount, LegacyAccountMap
from engagement.funding_wallet import refund_locked_order_funding, validate_order_funding_sources
from engagement.models import MemberProfile, OrderFundingAllocation, PointLedger
from engagement.services import change_points
from stores.db import transaction
from wallet.models import Transaction, Wallet
from .models import Order, OrderPointsRedemption, OrderPointsRefund, OrderRefund
from .refund_math import RefundCalculationError, payment_refund, wallet_source_refund


def _plan(order, current_refund_id, equivalent_units, *, history_only=False):
    if history_only and (current_refund_id is not None or equivalent_units is not None):
        raise ValidationError('退款历史核对参数无效')
    receipt = OrderPointsRedemption.objects.select_for_update().select_related('ledger', 'payment').filter(order=order).first()
    if not receipt:
        raise ValidationError('原积分付款凭据缺失，请先核对')
    if (receipt.ledger.account_id != order.customer_account_id or receipt.ledger.order_id != order.pk
            or receipt.ledger.amount != -receipt.points_hundredths
            or receipt.ledger.kind != 'ORDER_POINTS_REDEEM'
            or receipt.ledger.event_key != f'order:{order.pk}:points-redemption'
            or receipt.payment.order_id != order.pk
            or receipt.payment.amount != -receipt.paid_units or receipt.paid_units != order.amount
            or receipt.payment.tx_type != Transaction.TxType.PAY or receipt.payment.status != Transaction.Status.SUCCESS
            or receipt.payment.wallet.account_id != order.customer_account_id
            or receipt.payment.wallet.user_id != order.customer_id
            or receipt.payment.balance_after != receipt.payment.balance_before + receipt.payment.amount
            or receipt.payment.reversal_of_id or receipt.payment.reversals.exists()
            or PointLedger.objects.filter(order=order, kind='ORDER_POINTS_REDEEM').exclude(pk=receipt.ledger_id).exists()
            or Transaction.objects.filter(order=order, tx_type=Transaction.TxType.PAY,
                status=Transaction.Status.SUCCESS).exclude(pk=receipt.payment_id).exists()):
        raise ValidationError('原积分付款凭据不一致，请先核对')
    previous = list(OrderPointsRefund.objects.select_for_update().filter(redemption=receipt)
                    .select_related('refund', 'coin_transaction', 'points_ledger').order_by('refund_id'))
    other_refunds = set(OrderRefund.objects.select_for_update().filter(order=order)
                        .exclude(pk=current_refund_id).values_list('pk', flat=True))
    if other_refunds != {row.refund_id for row in previous}:
        raise ValidationError('历史退款缺少原支付构成记录，请先核对')
    for row in previous:
        if row.refund.order_id != order.pk or row.refund.amount != row.coin_units:
            raise ValidationError('历史退款记录不一致，请先核对')
        if row.coin_units and (not row.coin_transaction_id or row.coin_transaction.order_id != order.pk
                or row.coin_transaction.wallet_id != receipt.payment.wallet_id
                or row.coin_transaction.tx_type != Transaction.TxType.REFUND
                or row.coin_transaction.status != Transaction.Status.SUCCESS
                or row.coin_transaction.amount != row.coin_units or row.coin_transaction.reversal_of_id
                or row.coin_transaction.operator_id != row.refund.operator_id
                or row.coin_transaction.operator_account_id != row.refund.operator_account_id
                or row.coin_transaction.reversals.exists()
                or row.coin_transaction.balance_after != row.coin_transaction.balance_before + row.coin_transaction.amount):
            raise ValidationError('历史店币退款流水不一致，请先核对')
        if row.points_hundredths and (not row.points_ledger_id or row.points_ledger.order_id != order.pk
                or row.points_ledger.account_id != order.customer_account_id
                or row.points_ledger.kind != 'ORDER_POINTS_RETURN'
                or row.points_ledger.event_key != f'order:{order.pk}:points-return:{row.refund_id}'
                or row.points_ledger.amount != row.points_hundredths
                or row.points_ledger.operator_id != row.refund.operator_account_id):
            raise ValidationError('历史积分退款流水不一致，请先核对')
    allocations = list(OrderFundingAllocation.objects.select_for_update().select_related('lot').filter(order=order))
    validate_order_funding_sources(order, allocations)
    kinds = OrderFundingAllocation.Kind.values
    paid = {kind: sum(row.amount for row in allocations if row.kind == kind) for kind in kinds}
    returned = {kind: sum(row.refunded_amount for row in allocations if row.kind == kind) for kind in kinds}
    recorded = {'PREPAID': sum(row.prepaid_units for row in previous), 'GIFT': sum(row.gift_units for row in previous),
                'COMPENSATION': sum(row.compensation_units for row in previous)}
    if (not allocations or sum(paid.values()) != receipt.paid_units or returned != recorded
            or any(row.lot.account_id != order.customer_account_id for row in allocations)):
        raise ValidationError('历史退款资金来源不一致，请先核对')
    coin_tx_ids = {row.coin_transaction_id for row in previous if row.coin_transaction_id}
    point_ledger_ids = {row.points_ledger_id for row in previous if row.points_ledger_id}
    if (set(Transaction.objects.filter(order=order, wallet_id=receipt.payment.wallet_id,
                                       tx_type=Transaction.TxType.REFUND, status=Transaction.Status.SUCCESS)
            .values_list('pk', flat=True)) != coin_tx_ids
            or set(PointLedger.objects.filter(order=order, kind='ORDER_POINTS_RETURN')
                   .values_list('pk', flat=True)) != point_ledger_ids):
        raise ValidationError('退款流水缺少对应凭据，请先核对')
    try:
        # Verify each recorded operation, not just the aggregate: separately
        # rounded partial refunds must never be silently rewritten as another
        # mathematically plausible cumulative distribution.
        eq = coin = points = prepaid = gift = compensation = 0
        for row in previous:
            item = payment_refund(paid_coin_units=receipt.paid_units, paid_point_hundredths=receipt.points_hundredths,
                point_value_units=receipt.discount_units, refund_equivalent_units=row.equivalent_units,
                previously_refunded_equivalent_units=eq, previously_refunded_coin_units=coin,
                previously_refunded_point_hundredths=points)
            source = wallet_source_refund(coin_units=item.coin_units,
                paid_prepaid_units=paid['PREPAID'], paid_gift_units=paid['GIFT'],
                paid_compensation_units=paid['COMPENSATION'], refunded_prepaid_units=prepaid,
                refunded_gift_units=gift, refunded_compensation_units=compensation)
            if ((row.coin_units, row.points_hundredths, row.is_final, row.prepaid_units, row.gift_units,
                 row.compensation_units) != (item.coin_units, item.point_hundredths, item.is_final,
                                              source.prepaid_units, source.gift_units, source.compensation_units)):
                raise ValidationError('历史退款逐笔构成不一致，请先核对')
            eq += row.equivalent_units; coin += row.coin_units; points += row.points_hundredths
            prepaid += row.prepaid_units; gift += row.gift_units; compensation += row.compensation_units
        if history_only:
            return receipt, previous, None
        plan = payment_refund(paid_coin_units=receipt.paid_units, paid_point_hundredths=receipt.points_hundredths,
            point_value_units=receipt.discount_units, refund_equivalent_units=equivalent_units,
            previously_refunded_equivalent_units=eq, previously_refunded_coin_units=coin,
            previously_refunded_point_hundredths=points)
        sources = wallet_source_refund(coin_units=plan.coin_units,
            paid_prepaid_units=paid['PREPAID'], paid_gift_units=paid['GIFT'], paid_compensation_units=paid['COMPENSATION'],
            refunded_prepaid_units=returned['PREPAID'], refunded_gift_units=returned['GIFT'],
            refunded_compensation_units=returned['COMPENSATION'])
    except RefundCalculationError as exc:
        raise ValidationError(str(exc)) from None
    return receipt, plan, sources


def verify_refund_history_locked(refund_id):
    """Reconcile all recorded points returns; never authorize another debit."""
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('积分退款凭证必须在门店事务内核对')
    if type(refund_id) is not int or refund_id <= 0:
        raise ValidationError('积分退款编号无效')
    order_id = OrderRefund.objects.filter(pk=refund_id).values_list('order_id', flat=True).first()
    if not order_id:
        raise ValidationError('积分退款不存在')
    order = Order.objects.select_for_update().get(pk=order_id)
    if not order.customer_account_id:
        raise ValidationError('积分退款缺少老板账号')
    account = ClubAccount.objects.select_for_update().get(pk=order.customer_account_id)
    if (account.business_cycle != order.business_cycle or (order.customer_id is not None and not LegacyAccountMap.objects.filter(
            account_id=account.pk, legacy_user_id=order.customer_id).exists())):
        raise ValidationError('积分退款账号或业务周期不一致')
    from .points_service_payment import MARKER
    receipt, rows, _ = _plan(order, None, None, history_only=True)
    if not isinstance(order.funding_terms, dict) or order.funding_terms.get(MARKER) != {'receipt_id': receipt.pk}:
        raise ValidationError('订单积分付款保护标记不一致')
    found = next((row for row in rows if row.refund_id == refund_id), None)
    if not found:
        raise ValidationError('积分退款缺少原支付构成凭证')
    return found


def refund_assets_locked(*, order_id, refund_id, equivalent_units):
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('积分订单退款必须在门店事务内执行')
    order = Order.objects.select_for_update().get(pk=order_id)
    account = ClubAccount.objects.select_for_update().get(pk=order.customer_account_id)
    wallet = Wallet.objects.select_for_update().get(account_id=account.pk)
    profile = MemberProfile.objects.select_for_update().get(account_id=account.pk)
    refund = OrderRefund.objects.select_for_update().get(pk=refund_id, order_id=order.pk)
    if (account.business_cycle != order.business_cycle or order.funding_terms is None
            or OrderPointsRefund.objects.filter(refund=refund).exists()):
        raise ValidationError('退款账号周期、资金依据或操作状态异常，请先核对')
    if (wallet.user_id != order.customer_id or (order.customer_id is not None and not
            LegacyAccountMap.objects.select_for_update().filter(account_id=account.pk,
                legacy_user_id=order.customer_id).exists())):
        raise ValidationError('订单、钱包和历史账号归属不一致，请先核对')
    receipt, plan, sources = _plan(order, refund.pk, equivalent_units)
    from .points_service_payment import MARKER
    if order.funding_terms.get(MARKER) != {'receipt_id': receipt.pk}:
        raise ValidationError('订单积分付款保护标记不一致，请先核对')
    if refund.amount != plan.coin_units:
        raise ValidationError('退款店币与原支付构成不一致，请重新核对')
    if receipt.payment.wallet_id != wallet.pk:
        raise ValidationError('原付款钱包与退款账号不一致，请先核对')
    if wallet.balance + plan.coin_units > 2147483647:
        raise ValidationError('退款后钱包余额超过系统范围')
    coin_tx = None
    if plan.coin_units:
        refund_locked_order_funding(order, wallet, plan.coin_units)
        before = wallet.balance
        wallet.balance += plan.coin_units
        wallet.save(update_fields=['balance'])
        coin_tx = Transaction.objects.create(wallet=wallet, order=order, amount=plan.coin_units,
            tx_type=Transaction.TxType.REFUND, balance_before=before, balance_after=wallet.balance,
            operator=refund.operator, operator_account=refund.operator_account, remark=refund.reason[:255])
    ledger = None
    if plan.point_hundredths:
        key = f'order:{order.pk}:points-return:{refund.pk}'
        if PointLedger.objects.filter(event_key=key).exists():
            raise ValidationError('积分退款流水已存在，请先核对')
        ledger = change_points(profile, plan.point_hundredths, key, 'ORDER_POINTS_RETURN',
                               '服务订单退回抵扣积分', order=order, operator=refund.operator_account)
    posted = OrderPointsRefund.objects.create(redemption=receipt, refund=refund, coin_transaction=coin_tx,
        points_ledger=ledger, equivalent_units=equivalent_units, coin_units=plan.coin_units,
        points_hundredths=plan.point_hundredths, prepaid_units=sources.prepaid_units,
        gift_units=sources.gift_units, compensation_units=sources.compensation_units, is_final=plan.is_final)
    from engagement.license_points_review import reverse_reviewed_points
    reverse_reviewed_points(order, refund=refund)
    return posted
