"""Plan platform-funded safe-box termination and provider recoveries."""
import hashlib
import json

from django.conf import settings
from django.core import signing
from stores.db import transaction
from django.utils import timezone

from .models import Order, OrderPlatformSubsidy, OrderPlatformSubsidyShare
from .refunds import RefundError

LEGACY_AGREEMENT_BLOCK = 'SUBSIDY_NEGOTIATION_AGREEMENT_REQUIRED'
LEGACY_AGREEMENT_MESSAGE = '安全箱协商尚未接通当事人确认及新结算规则，请先由客服核对'


def options(order):
    if (not getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False)
            or order.status not in (Order.Status.IN_SERVICE, Order.Status.COMPLETED)
            or order.payment_status != Order.PaymentStatus.PAID
            or not order.in_service_at or order.refund_records.exists()):
        raise RefundError('当前订单不能协商安全箱报酬', code='SUBSIDY_NEGOTIATION_UNAVAILABLE')
    subsidy = OrderPlatformSubsidy.objects.filter(order=order).first()
    if not subsidy:
        raise RefundError('当前订单没有安全箱平台补贴', code='SUBSIDY_NEGOTIATION_UNAVAILABLE')
    if order.status == Order.Status.COMPLETED and not order.aftersales.filter(status='OPEN').exists():
        from .refunds import can_request_aftersale
        if not can_request_aftersale(order):
            raise RefundError('订单不在售后处理范围内', code='SUBSIDY_NEGOTIATION_UNAVAILABLE')
    shares = list(OrderPlatformSubsidyShare.objects.filter(subsidy=subsidy)
                  .select_related('assignment').order_by('pk'))
    if not shares or sum(not share.assignment.released_at for share in shares) != 1:
        raise RefundError('陪玩补贴份额待核对', code='SUBSIDY_LEDGER_INCOMPLETE')
    return {
        'order_id': order.pk,
        'coupon_outcome': 'USED',
        'shares': [{
            'share_id': share.pk,
            'assignment_id': share.assignment_id,
            'provider_account_id': share.assignment.provider_account_id,
            'provider_name': share.assignment.provider_name_snapshot or f'陪玩 #{share.assignment.provider_id}',
            'expected_income': share.amount,
            'paid_income': share.amount if share.paid_at else 0,
            'paid_at': share.paid_at,
            'released': bool(share.assignment.released_at),
        } for share in shares],
    }


def _allocation(allocation):
    if not isinstance(allocation, list):
        raise RefundError('请填写每位陪玩的协商报酬', code='SUBSIDY_ALLOCATION_INVALID')
    amounts = {}
    for item in allocation:
        if not isinstance(item, dict) or set(item) != {'share_id', 'amount'}:
            raise RefundError('报酬明细格式不正确', code='SUBSIDY_ALLOCATION_INVALID')
        key, amount = item['share_id'], item['amount']
        if type(key) is not int or type(amount) is not int or amount < 0 or key in amounts:
            raise RefundError('报酬须为非负整数且每位陪玩仅填写一次', code='SUBSIDY_ALLOCATION_INVALID')
        amounts[key] = amount
    return amounts


def preview(order, allocation, *, lock=False, with_recovery=False):
    """Calculate negotiated entitlements without moving or promising funds."""
    data = options(order)
    amounts = _allocation(allocation)
    shares = data['shares']
    if set(amounts) != {row['share_id'] for row in shares}:
        raise RefundError('请逐项填写全部陪玩的报酬', code='SUBSIDY_ALLOCATION_INVALID')
    for row in shares:
        agreed = amounts[row['share_id']]
        if agreed > row['expected_income']:
            raise RefundError('协商报酬不能超过陪玩的预计份额', code='SUBSIDY_ALLOCATION_INVALID')
        row['agreed_income'] = agreed
        row['recovery_required'] = max(0, row['paid_income'] - agreed)
        row['unpaid_income'] = 0 if row['paid_at'] else agreed
    data['recovery_total'] = sum(row['recovery_required'] for row in shares)
    data['unpaid_total'] = sum(row['unpaid_income'] for row in shares)
    data['agreed_total'] = sum(row['agreed_income'] for row in shares)
    paid_shares = list(OrderPlatformSubsidyShare.objects.filter(
        pk__in=[row['share_id'] for row in shares], paid_at__isnull=False, amount__gt=0,
    ).select_related('assignment', 'income_transaction__wallet', 'expense_transaction__wallet__user'))
    recovery = {}
    for share in paid_shares:
        income, expense = share.income_transaction, share.expense_transaction
        assignment = share.assignment
        if (not income or not expense or income.order_id != order.pk or expense.order_id != order.pk
                or income.tx_type != 'INCOME' or income.status != 'SUCCESS' or income.amount != share.amount
                or expense.tx_type != 'SUBSIDY_EXPENSE' or expense.status != 'SUCCESS' or expense.amount != -share.amount
                or not expense.wallet.user or expense.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
                or income.balance_after != income.balance_before + share.amount
                or expense.balance_after != expense.balance_before - share.amount
                or (assignment.provider_account_id and income.wallet.account_id != assignment.provider_account_id)
                or (not assignment.provider_account_id and income.wallet.user_id != assignment.provider_id)):
            raise RefundError('已结算补贴流水待核对', code='SUBSIDY_LEDGER_INCOMPLETE')
        recovery[income.pk] = next(row['recovery_required'] for row in shares if row['share_id'] == share.pk)
    if recovery:
        from .refunds import build_refund_plan
        money, _ = build_refund_plan(order, funded_recovery=recovery, lock=lock)
        data['recoveries'] = money['recoveries']
        data['shortfalls'] = money['shortfalls']
        data['recovery_available'] = money['eligible']
        data['recovery_plan_token'] = money['plan_token']
    else:
        data.update(recoveries=[], shortfalls=[], recovery_available=True, recovery_plan_token=None)
    from wallet.models import Wallet
    platform = Wallet.objects.filter(
        user__username=settings.PLATFORM_SYSTEM_USERNAME,
    ).first()
    if platform:
        from .subsidies import subsidy_funds
        funds = subsidy_funds(platform)
        data['platform_balance'], data['platform_available'], data['platform_deposits'] = (
            funds['balance'], funds['available'], funds['deposits'])
    else:
        data.update(platform_balance=None, platform_available=None, platform_deposits=None)
    # The legacy subsidy calculation does not prove that the boss and each
    # affected provider agreed, nor does it implement current settlement
    # timing. Keep factual preview separate from permission to execute.
    data['execution_available'] = False
    data['execution_block_code'] = LEGACY_AGREEMENT_BLOCK
    data['execution_block_reason'] = LEGACY_AGREEMENT_MESSAGE
    facts = {key: value for key, value in data.items() if key != 'recovery_plan_token'}
    fingerprint = hashlib.sha256(json.dumps(facts, sort_keys=True, default=str).encode()).hexdigest()
    data['plan_token'] = signing.dumps({'order_id': order.pk, 'fingerprint': fingerprint},
                                       salt='orders.subsidy-negotiation.v1', compress=True)
    return (data, recovery) if with_recovery else data


@transaction.atomic
def apply(order_id, allocation, *, reason, request_id, plan_token, operator, operator_account):
    """Commit an agreed termination; unpaid shares remain retryable."""
    from .financial_lock import lock_order_money
    from .models import OrderRefund, RefundRecoveryLine
    from .refunds import authorized_refund, recover_settlement, request_fingerprint
    from .state_machine import transition
    from .subsidies import settle_terminated_subsidy
    from .services import push_message_safe, refresh_escort_status
    from .notifier import notify_order_update
    from wallet.models import Transaction, get_platform_wallet
    from wallet.notifier import notify_wallet_update

    if not authorized_refund(operator_account):
        raise RefundError('仅有退款权限的客服可确认协商结果')
    if not isinstance(reason, str) or not 2 <= len(reason.strip()) <= 255:
        raise RefundError('请填写2至255字处理说明', code='SUBSIDY_INVALID_REASON')
    reason = reason.strip()
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 64:
        raise RefundError('请提供操作凭证', code='SUBSIDY_INVALID_REQUEST')
    amounts = _allocation(allocation)
    intent = [{'share_id': key, 'amount': amounts[key]} for key in sorted(amounts)]
    fingerprint = request_fingerprint(0, reason, intent)
    order = Order.objects.select_for_update().get(pk=order_id)
    existing = OrderRefund.objects.filter(order=order, client_request_id=request_id).first()
    if existing:
        if existing.request_fingerprint != fingerprint or not existing.benefit_effects.get('subsidy_negotiated'):
            raise RefundError('同一操作凭证不能用于不同方案', code='REFUND_REQUEST_CONFLICT')
        order._refund_replayed = True
        return order
    lock_order_money()
    current, recovery = preview(order, intent, lock=True, with_recovery=True)
    if not current['execution_available']:
        raise RefundError(current['execution_block_reason'], code=current['execution_block_code'],
                          data={'preview': current})
    try:
        supplied = signing.loads(plan_token or '', salt='orders.subsidy-negotiation.v1', max_age=600)
        fresh = signing.loads(current['plan_token'], salt='orders.subsidy-negotiation.v1', max_age=600)
    except signing.BadSignature:
        supplied, fresh = None, None
    if not supplied or supplied != fresh:
        raise RefundError('协商方案或资金已变化，请重新试算', code='REFUND_PLAN_CHANGED', data={'preview': current})
    if not current['recovery_available']:
        raise RefundError('可追回资金不足，请人工处理', code='REFUND_RECOVERY_INSUFFICIENT',
                          data={'shortfalls': current['shortfalls']})
    shares = {row.pk: row for row in OrderPlatformSubsidyShare.objects.select_for_update()
              .filter(pk__in=amounts).select_related('assignment', 'expense_transaction')}

    def effect(locked):
        from coupons.models import UserCoupon
        coupons = list(UserCoupon.objects.select_for_update().filter(order=locked, status='USED').select_related('coupon'))
        records = [{'role': 'PROVIDER', 'name': row['provider_name'], 'account_id': row['provider_account_id'],
                    'entitlement': row['expected_income'], 'amount': row['expected_income'] - row['agreed_income'],
                    'retained': row['agreed_income'], 'settled': bool(row['paid_at'])}
                   for row in current['shares']]
        refund = OrderRefund.objects.create(order=locked, amount=0, reason=reason, operator=operator,
            operator_account=operator_account, client_request_id=request_id, request_fingerprint=fingerprint,
            negotiated_allocation=records, benefit_effects={'subsidy_negotiated': True,
                'order_coupon_results': [{'name': item.coupon.name, 'outcome': 'NOT_RETURNED'} for item in coupons]})
        if recovery:
            changed = recover_settlement(locked, refund, plan_token=current['recovery_plan_token'],
                operator=operator, operator_account=operator_account, funded_recovery=recovery)
            for wallet in changed:
                transaction.on_commit(lambda wallet=wallet: notify_wallet_update(wallet))
            platform = get_platform_wallet(for_update=True)
            for share in shares.values():
                taken = recovery.get(share.income_transaction_id, 0)
                if not taken:
                    continue
                before = platform.balance
                platform.balance += taken
                platform.save(update_fields=['balance'])
                reversal = Transaction.objects.create(wallet=platform, order=locked, tx_type='SUBSIDY_EXPENSE',
                    amount=taken, balance_before=before, balance_after=platform.balance,
                    reversal_of=share.expense_transaction, operator=operator, operator_account=operator_account,
                    remark=f'安全箱协商终止补贴退回：{locked.order_no}')
                RefundRecoveryLine.objects.create(refund=refund, original_transaction=share.expense_transaction,
                    reversal_transaction=reversal, wallet=platform, source='SUBSIDY', amount=taken)
            transaction.on_commit(lambda: notify_wallet_update(platform))
        refund.save(update_fields=['recovered_amount', 'plan_fingerprint'])
        for share in shares.values():
            if not share.paid_at:
                share.amount = amounts[share.pk]
                share.save(update_fields=['amount'])
        locked.payment_status = Order.PaymentStatus.REFUNDED
        locked.refunded_at = timezone.now()
        locked.cancel_reason = reason

    order = transition(order.pk, Order.Status.CANCELLED, operator=operator, operator_account=operator_account,
        action='REFUND', reason=reason, side_effect=effect, allow_settled_refund=True,
        update_fields=['payment_status', 'refunded_at', 'cancel_reason'])
    settle_terminated_subsidy(order.pk)
    order.refresh_from_db()
    refresh_escort_status(provider_ids=list(order.providers.values_list('provider_id', flat=True)),
        account_ids=list(order.providers.values_list('provider_account_id', flat=True)), exclude_order_ids=[order.pk])
    from .aftersale import append_history, notify_aftersale_reports
    for ticket in order.aftersales.select_for_update().filter(status='OPEN'):
        ticket.status, ticket.operator, ticket.resolved_at = 'RESOLVED', operator_account, timezone.now()
        ticket.resolution = f'协商终止：{reason}'[:1000]
        append_history(ticket)
        ticket.save(update_fields=['status', 'operator', 'resolved_at', 'resolution', 'history'])
    transaction.on_commit(lambda: notify_order_update(order))
    transaction.on_commit(lambda: notify_aftersale_reports(order))
    transaction.on_commit(lambda: push_message_safe(recipient_id=order.customer_id,
        recipient_account_id=order.customer_account_id, title='订单已协商终止',
        preview='免费安全箱券已使用，不退还；请查看订单详情。', msg_type='ORDER', related_order_id=order.pk))
    for share in shares.values():
        transaction.on_commit(lambda share=share: push_message_safe(recipient_id=share.assignment.provider_id,
            recipient_account_id=share.assignment.provider_account_id, title='订单已协商终止',
            preview='协商报酬及结算状态请查看订单和钱包。', msg_type='ORDER', related_order_id=order.pk))
    return order
