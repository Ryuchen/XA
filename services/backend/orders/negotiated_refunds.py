"""Negotiated termination of historical, partly settled transfer orders."""
from django.db import transaction
from django.utils import timezone

from wallet.models import Transaction
from wallet.notifier import notify_wallet_update
from wallet.income import hold_provider_income
from users.models import EscortProfile
from .models import Order
from .refunds import (RefundError, authorized_refund, build_refund_plan,
                      parse_refund_amount, replay_refund, schedule_refund_notices)


def _intent(value):
    if not isinstance(value, list) or not value:
        raise RefundError('请逐项填写协商承担金额', code='REFUND_ALLOCATION_INVALID')
    result = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {'key', 'amount'} or not isinstance(item['key'], str):
            raise RefundError('协商分摊格式不正确', code='REFUND_ALLOCATION_INVALID')
        amount = item['amount']
        if isinstance(amount, str) and amount.isascii() and amount.isdigit():
            amount = int(amount)
        if type(amount) is not int or amount < 0:
            raise RefundError('每项承担金额须为非负整数', code='REFUND_ALLOCATION_INVALID')
        result.append({'key': item['key'], 'amount': amount})
    result.sort(key=lambda item: item['key'])
    if len({item['key'] for item in result}) != len(result):
        raise RefundError('分摊项重复', code='REFUND_ALLOCATION_INVALID')
    return result


def options(order):
    if (order.status != Order.Status.IN_SERVICE or order.payment_status != Order.PaymentStatus.PAID
            or order.settlement_mode != Order.SettlementMode.COMPLETION or order.settled_at
            or order.refund_records.exists()):
        raise RefundError('此订单不属于服务中部分结算转单协商退款范围', code='REFUND_NEGOTIATION_UNAVAILABLE')
    shares = list(order.providers.order_by('pk'))
    if not any(row.settled_at and row.provider_income for row in shares) or not any(not row.settled_at for row in shares):
        raise RefundError('缺少已结算和待结算的转单份额，请人工核账', code='REFUND_LEDGER_INCOMPLETE')
    if any(not row.provider_id for row in shares if row.provider_income):
        raise RefundError('陪玩身份不完整，请人工核账', code='REFUND_LEDGER_INCOMPLETE')
    rows = [{'key': f'provider:{row.pk}', 'role': 'PROVIDER', 'name': row.provider_name_snapshot or f'陪玩 #{row.provider_id}',
             'account_id': row.provider_account_id, 'user_id': row.provider_id, 'entitlement': row.provider_income,
             'settled': bool(row.settled_at), 'share_id': row.pk} for row in shares]
    if order.inviter_id:
        rows.append({'key': 'inviter', 'role': 'INVITER', 'name': '推荐人', 'account_id': order.inviter_account_id,
                     'user_id': order.inviter_id, 'entitlement': order.inviter_commission, 'settled': False})
    elif order.inviter_commission:
        raise RefundError('推荐收益缺少收款身份，请人工核账', code='REFUND_LEDGER_INCOMPLETE')
    rows.append({'key': 'platform', 'role': 'PLATFORM', 'name': '平台', 'account_id': None,
                 'user_id': None, 'entitlement': order.shop_income, 'settled': False})
    if sum(row['entitlement'] for row in rows) != order.amount:
        raise RefundError('参与方份额与实付不一致，请人工核账', code='REFUND_LEDGER_INCOMPLETE')
    credits = list(order.transactions.filter(status='SUCCESS', reversal_of__isnull=True,
                    tx_type__in=['INCOME', 'GIFT', 'SHOP_INCOME'], amount__gt=0).select_related('wallet'))
    used = set()
    for row in rows:
        if not row['settled'] or not row['entitlement']:
            continue
        matches = [tx for tx in credits if tx.pk not in used and tx.tx_type == 'INCOME'
                   and tx.amount == row['entitlement'] and tx.remark.startswith('转单结算：')
                   and (tx.wallet.account_id == row['account_id'] if row['account_id']
                        else tx.wallet.account_id is None and tx.wallet.user_id == row['user_id'])]
        if len(matches) != 1:
            raise RefundError('已结算份额无法唯一对应原入账流水，请人工核账', code='REFUND_LEDGER_INCOMPLETE')
        row['transaction_id'] = matches[0].pk
        used.add(matches[0].pk)
    if used != {tx.pk for tx in credits}:
        raise RefundError('发现未匹配的结算流水，请人工核账', code='REFUND_LEDGER_INCOMPLETE')
    return rows


def plan(order, amount, allocation):
    amount = parse_refund_amount(amount, order.amount)
    intent = _intent(allocation)
    rows = options(order)
    amounts = {item['key']: item['amount'] for item in intent}
    if set(amounts) != {row['key'] for row in rows}:
        raise RefundError('请逐项填写所有参与方承担金额', code='REFUND_ALLOCATION_INVALID')
    if any(amounts[row['key']] > row['entitlement'] for row in rows) or sum(amounts.values()) != amount:
        raise RefundError('各项承担不能超过原份额，合计须等于协商退款金额', code='REFUND_ALLOCATION_INVALID')
    for row in rows:
        row['amount'] = amounts[row['key']]
        row['retained'] = row['entitlement'] - row['amount']
    negotiated = {'intent': intent, 'allocation': rows,
                  'recovery': {row['transaction_id']: row['amount'] for row in rows if row.get('transaction_id')},
                  'fingerprint': rows}
    preview, _ = build_refund_plan(order, refund_amount=amount, negotiated=negotiated)
    preview['allocations'] = rows
    return preview, negotiated


@transaction.atomic
def apply(order_id, *, amount, allocation, reason, request_id, plan_token, operator, operator_account):
    from .financial_lock import lock_order_money
    from .services import _credit_wallet, refund_to_customer, refresh_escort_status
    from .state_machine import transition
    from .notifier import notify_order_update
    if not authorized_refund(operator_account):
        raise RefundError('仅有退款权限的后台账号可操作退款')
    order = Order.objects.select_for_update().get(pk=order_id)
    intent = _intent(allocation)
    if not request_id:
        raise RefundError('协商退款须提供操作凭证', code='REFUND_INVALID_REQUEST')
    if replay_refund(order, request_id, amount, reason, intent):
        order._refund_replayed = True
        return order
    lock_order_money()
    preview, negotiated = plan(order, amount, intent)
    if not preview['eligible']:
        raise RefundError('追回余额不足，本次退款未执行', code='REFUND_RECOVERY_INSUFFICIENT',
                          data={'shortfalls': preview['shortfalls'], 'preview': preview})

    def effect(locked):
        refund_to_customer(locked, reason=reason, operator=operator, operator_account=operator_account,
                           allow_settled_recovery=True, plan_token=plan_token, refund_amount=amount,
                           client_request_id=request_id, negotiated=negotiated)
        profiles = {p.user_id: p for p in EscortProfile.objects.select_for_update().filter(
            user_id__in=[row['user_id'] for row in negotiated['allocation'] if row['role'] == 'PROVIDER']).order_by('user_id')}
        for row in negotiated['allocation']:
            if row['settled']:
                continue
            if row['role'] == 'PROVIDER':
                tx = _credit_wallet(order=locked, amount=row['retained'], tx_type=Transaction.TxType.INCOME,
                    remark=f'订单收入：{locked.order_no}', event='order.settle.provider', trace_id=None,
                    user_id=row['user_id'], account_id=row['account_id'])
                hold_provider_income(tx, profile=profiles.get(row['user_id']), hold_hours=locked.income_hold_hours)
                locked.providers.filter(pk=row['share_id']).update(settled_at=timezone.now())
            elif row['role'] == 'INVITER':
                tx = _credit_wallet(order=locked, amount=row['retained'], tx_type=Transaction.TxType.INCOME,
                    remark=f'推荐分佣：{locked.order_no}', event='order.settle.inviter', trace_id=None,
                    user_id=row['user_id'], account_id=row['account_id'])
                if tx:
                    transaction.on_commit(lambda tx=tx: notify_wallet_update(tx.wallet))
            else:
                tx = _credit_wallet(order=locked, amount=row['retained'], tx_type=Transaction.TxType.SHOP_INCOME,
                    remark=f'平台收入：{locked.order_no}', event='order.settle.platform', trace_id=None, platform=True)
                if tx:
                    transaction.on_commit(lambda tx=tx: notify_wallet_update(tx.wallet))
        locked.settled_at = timezone.now()
        locked.cancel_reason = reason

    order = transition(order.pk, Order.Status.CANCELLED, operator=operator, operator_account=operator_account,
        action='REFUND', reason=reason, side_effect=effect, allow_settled_refund=True,
        update_fields=['cancel_reason', 'payment_status', 'refunded_at', 'settled_at'])
    refresh_escort_status(provider_ids=[order.provider_id, *order.providers.values_list('provider_id', flat=True)],
        account_ids=[order.provider_account_id, *order.providers.values_list('provider_account_id', flat=True)], exclude_order_ids=[order.pk])
    schedule_refund_notices(order, int(amount), partial=int(amount) < order.amount)
    transaction.on_commit(lambda: notify_order_update(order))
    return order
