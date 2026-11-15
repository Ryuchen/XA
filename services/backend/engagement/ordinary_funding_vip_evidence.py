"""Prove an ordinary funded service was settled before License review."""
from datetime import timedelta

from rest_framework.exceptions import ValidationError
from stores.db import transaction
from wallet.models import Transaction


def verify_locked(order, decision):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('会员实销核对须在订单事务中执行')
    from .license_refund_vip_review import _verify_original_denial
    from .license_points_review import _verify_award_sources
    from orders.services import missing_report_evidence
    from orders.settlement import assert_order_conserved
    from wallet.models import ProviderReport

    _verify_original_denial(order, decision)
    if (order.refund_records.exists() or decision.refunded_units
            or order.status != 'COMPLETED' or order.payment_status != 'PAID'
            or not order.service_clock_version or order.service_aftersale_hours is None
            or order.service_freeze_hours is None or not order.completed_at or not order.settled_at
            or order.settled_at > decision.decided_at
            or order.completed_at + timedelta(hours=order.service_aftersale_hours) > order.settled_at
            or not order.consumption_ready_at or order.consumption_ready_at > decision.decided_at):
        raise ValidationError('普通服务结算时间或退款事实待核对')
    _verify_award_sources(order)
    rows = list(order.providers.select_for_update().order_by('pk'))
    if rows:
        rows = [row for row in rows if row.settlement_base > 0 or not row.released_at]
        if (len(rows) != order.required_provider_count or any(not row.provider_id or not row.settled_at
                or row.settled_at > order.settled_at for row in rows)
                or sum(row.settlement_base for row in rows) != order.amount):
            raise ValidationError('陪玩结算份额待核对')
        people = [(row.provider_id, row.provider_account_id, row.provider_income, row.settled_at) for row in rows]
    elif order.provider_id and order.required_provider_count == 1:
        people = [(order.provider_id, order.provider_account_id, order.provider_income, order.settled_at)]
    else:
        raise ValidationError('陪玩结算身份待核对')
    if sum(item[2] for item in people) != order.provider_income or assert_order_conserved(
            order.amount, [item[2] for item in people],
            order.inviter_commission if order.inviter_id or order.inviter_account_id else 0,
            order.shop_income) != order.amount:
        raise ValidationError('订单结算金额待核对')

    def credit(query, amount, deadline):
        records = list(query.filter(status=Transaction.Status.SUCCESS, reversal_of__isnull=True).order_by('pk')[:2])
        if not amount:
            if records:
                raise ValidationError('零收益存在入账流水，请先核对')
            return
        if (len(records) != 1 or records[0].amount != amount or records[0].created_at > deadline
                or records[0].balance_after != records[0].balance_before + amount
                or records[0].reversals.exists()):
            raise ValidationError('结算收益流水待核对')

    for user_id, account_id, amount, settled_at in people:
        reports = list(order.provider_reports.filter(provider_id=user_id).order_by('pk')[:2])
        if (len(reports) != 1 or reports[0].status != ProviderReport.Status.APPROVED
                or not reports[0].submitted_at
                or reports[0].submitted_at + timedelta(hours=order.service_freeze_hours) > settled_at
                or reports[0].payout_amount != amount
                or (reports[0].provider_account_id and reports[0].provider_account_id != account_id)
                or missing_report_evidence(reports[0])):
            raise ValidationError('陪玩报单或结算时钟待核对')
        query = order.transactions.filter(tx_type=Transaction.TxType.INCOME,
            remark=f'订单收入：{order.order_no}', wallet__user_id=user_id)
        if account_id:
            query = query.filter(wallet__account_id=account_id)
        credit(query, amount, decision.decided_at)
    inviter = order.transactions.filter(tx_type=Transaction.TxType.INCOME,
        remark=f'推荐分佣：{order.order_no}')
    if order.inviter_account_id:
        inviter = inviter.filter(wallet__account_id=order.inviter_account_id)
    elif order.inviter_id:
        inviter = inviter.filter(wallet__user_id=order.inviter_id)
    credit(inviter, order.inviter_commission if order.inviter_id or order.inviter_account_id else 0, decision.decided_at)
    credit(order.transactions.filter(tx_type=Transaction.TxType.SHOP_INCOME,
        remark=f'平台收入：{order.order_no}'), order.shop_income, decision.decided_at)
