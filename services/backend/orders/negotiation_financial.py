"""Read-only, conservative burden calculation for a service negotiation."""
from rest_framework.exceptions import ValidationError
from wallet.models import Transaction
from .models import OrderOriginalAttribution, OrderOriginalShareTransfer
from .refund_math import provider_refund_burden
from .settlement import compute_provider_income


def preview(order, scope, refund_units, *, lock=False):
    """Verified simple unpaid shares only; transfer histories need separate reconciliation."""
    from .negotiation_transfer_preview import agreement_testing_available, preview_locked as transfer_preview
    if agreement_testing_available(order):
        case = order.aftersales.filter(status='OPEN').first()
        if not case:
            raise ValidationError('当前没有可处理的转单协商售后')
        calculation = transfer_preview(case.pk, refund_units)
        if [item['account_id'] for item in calculation['provider_terms']] != list(scope):
            raise ValidationError('转单协商受影响陪玩已变化')
        return calculation
    from .negotiation_zero import marked_execution_id
    if (order.payment_status in (order.PaymentStatus.PARTIALLY_REFUNDED, order.PaymentStatus.REFUNDED)
            or marked_execution_id(order) is not None):
        from .negotiation_followup import _preview_followup_locked, followup_enabled
        if not followup_enabled():
            raise ValidationError('追加协商方案尚未开放')
        case = order.aftersales.filter(status='OPEN').first()
        if not case:
            raise ValidationError('当前没有可处理的追加协商售后')
        result = _preview_followup_locked(case.pk, refund_units=refund_units)
        if [item['account_id'] for item in result['provider_terms']] != list(scope):
            raise ValidationError('追加协商受影响陪玩已变化')
        return result
    if lock:
        from stores.db import transaction
        if not transaction.get_connection().in_atomic_block:
            raise RuntimeError('资金快照加锁核对必须在门店事务内执行')
    if type(refund_units) is not int or not 0 <= refund_units <= order.amount or refund_units % 10:
        raise ValidationError('退款金额须为不超过实付的整数店币')
    def locked(query):
        return query.select_for_update() if lock else query

    if order.amount <= 0 or order.settled_at or locked(order.refund_records.all()).exists():
        raise ValidationError('订单已有退款或收益流水，请先人工核对')
    payments = list(locked(order.transactions.filter(tx_type='PAY', status='SUCCESS', reversal_of__isnull=True).select_related('wallet')))
    if (len(payments) != 1 or payments[0].wallet.account_id != order.customer_account_id
            or payments[0].amount != -order.amount or locked(payments[0].reversals.all()).exists()
            or payments[0].balance_after != payments[0].balance_before + payments[0].amount):
        raise ValidationError('原订单支付流水不完整，请先核对')
    rows = list(order.providers.select_for_update().order_by('pk'))
    if not rows or any(row.released_at or not row.provider_account_id for row in rows):
        raise ValidationError('转单或历史陪玩份额须先人工核对')
    if (len({row.provider_account_id for row in rows}) != len(rows)
            or sum(row.settlement_base for row in rows) != order.amount
            or sum(row.provider_income for row in rows) != order.provider_income
            or order.provider_income + order.inviter_commission + order.shop_income != order.amount
            or any(row.provider_income != compute_provider_income(row.settlement_base, row.commission_type,
                       row.commission_rate, row.commission_fixed) for row in rows)):
        raise ValidationError('订单陪玩收益或抽成快照不一致，请先核对')
    people = {row.provider_account_id:row for row in rows}
    if not set(scope).issubset(people):
        raise ValidationError('受影响陪玩的收益份额不完整')
    # An add-on refund can consume this same person's unpaid main-order
    # earnings. Those funds cannot be promised to the person a second time.
    from .addon_main_deduction import deductions_at
    addon_deductions = deductions_at(order)
    if (set(addon_deductions) - set(people) or any(amount > people[account].provider_income
            for account, amount in addon_deductions.items())):
        raise ValidationError('加购退款的主单收益扣减待核对')
    credits = list(locked(order.transactions.filter(status='SUCCESS', reversal_of__isnull=True,
        tx_type__in=['INCOME', 'SHOP_INCOME', 'GIFT']).select_related('wallet').order_by('pk')))
    matched = set()
    for row in rows:
        if not row.settled_at:
            continue
        if row.provider_account_id in scope or order.status != order.Status.COMPLETED:
            raise ValidationError('受影响陪玩或未完成订单的收益已结算，请先核对')
        own = [tx for tx in credits if tx.tx_type == 'INCOME' and tx.wallet.account_id == row.provider_account_id
               and tx.wallet.user_id == row.provider_id and tx.remark == f'订单收入：{order.order_no}']
        if ((row.provider_income > 0 and (len(own) != 1 or own[0].amount != row.provider_income))
                or (row.provider_income == 0 and own)):
            raise ValidationError('其他陪玩已结算收益流水待核对')
        for tx in own:
            if tx.balance_after != tx.balance_before + tx.amount or locked(tx.reversals.all()).exists():
                raise ValidationError('其他陪玩已结算收益流水待核对')
            matched.add(tx.pk)
    if matched != {tx.pk for tx in credits}:
        raise ValidationError('订单已有无法核对的收益流水，请先人工核对')
    attribution = locked(OrderOriginalAttribution.objects.filter(order=order)).first()
    if attribution and locked(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution=attribution)).exists():
        raise ValidationError('服务中转单的协商收益须先人工核对')
    if len(scope) > 1 and refund_units:
        if not attribution or attribution.status != 'ACTIVE' or attribution.original_amount != order.original_amount:
            raise ValidationError('多陪退款缺少原价份额快照，请先核对')
        segments = list(locked(attribution.segments.filter(started_at__isnull=False, released_at__isnull=True)))
        if (len(segments) != len(rows) or {segment.provider_account_id for segment in segments} != set(people)
                or any(not segment.original_share for segment in segments)
                or sum(segment.original_share for segment in segments) != attribution.original_amount + attribution.rounding_surplus):
            raise ValidationError('多陪退款原价份额不完整，请先核对')
        weights = {segment.provider_account_id:segment.original_share for segment in segments if segment.provider_account_id in scope}
    else:
        weights = {pk:1 for pk in scope}
    if refund_units:
        burden = provider_refund_burden(refund_equivalent_units=refund_units, original_price_weights=weights)
        amounts, surplus = burden.amounts, burden.rounding_to_store_units
    else:
        amounts, surplus = {pk:0 for pk in scope}, 0
    result = []
    for account_id in scope:
        income = people[account_id].provider_income - addon_deductions.get(account_id, 0)
        responsibility = amounts[account_id]
        result.append({'account_id':account_id, 'retained_units':max(income-responsibility, 0),
                       'original_income_units':income, 'burden_units':responsibility,
                       'additional_units':max(responsibility-income, 0)})
    return {'refund_units':refund_units, 'provider_terms':result, 'rounding_to_store_units':surplus}
