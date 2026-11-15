"""Verify the facts needed for a follow-up after the teammate finishes.

This helper is read-only and does not make a proposal or refund available. A
caller must authorize the actor and hold the tenant transaction. Execution and
settlement still need their own atomic integration and completion-time snapshot.
"""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from club_accounts.models import LegacyAccountMap
from users.models import EscortProfile
from wallet.models import ProviderReport
from .financial_lock import lock_existing_order_money
from .models import Order, OrderStatusLog
from .negotiation_proposals import _lock_case
from .negotiation_termination import continuing_team
from .settlement import SettlementError


def inspect_completed_followup(case_id):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('已完成订单的售后核对必须在门店事务内执行')
    order, case = _lock_case(case_id)
    try:
        lock_existing_order_money()
    except SettlementError as exc:
        raise ValidationError('订单资金互斥记录待核对') from exc
    prior = continuing_team(order, verify=True, allow_completed=True)
    if (not prior or order.status != Order.Status.COMPLETED or order.amount <= 0 or order.settled_at
            or order.consumption_confirmed_at or not order.in_service_at or not order.completed_at
            or not prior.accepted_at <= prior.termination_processed_at <= order.completed_at <= timezone.now()
            or order.service_clock_version is None or order.service_aftersale_hours is None
            or order.service_freeze_hours is None or order.service_deposit_percent is None
            or not 0 <= order.service_deposit_percent <= 100):
        raise ValidationError('原协商或完成时间待核对')
    logs = list(order.status_logs.select_for_update().filter(action=OrderStatusLog.Action.COMPLETE,
        from_status=Order.Status.IN_SERVICE, to_status=Order.Status.COMPLETED))
    if (len(logs) != 1 or not order.completed_at <= logs[0].created_at <= timezone.now()):
        raise ValidationError('订单完成凭证待核对')
    if (case.status != 'OPEN' or case.account_id != order.customer_account_id or not case.client_request_id
            or not case.created_at or not prior.termination_processed_at <= case.created_at <= timezone.now()
            or case.created_at >= order.completed_at + timedelta(hours=order.service_aftersale_hours)):
        raise ValidationError('售后创建时间或身份待核对')
    rows = list(order.providers.select_for_update().order_by('pk'))
    active = [row for row in rows if row.released_at is None]
    shares = list(prior.shares.select_for_update().select_related('assignment').order_by('pk'))
    if (len(rows) != 2 or len(active) != 1 or len(shares) != 1 or active[0].settled_at
            or case.affected_provider_account_ids != [active[0].provider_account_id]
            or not active[0].provider_id or not active[0].provider_account_id
            or len({row.provider_account_id for row in rows}) != 2
            or shares[0].assignment_id == active[0].pk):
        raise ValidationError('受影响陪玩或结算状态待核对')
    provider, previous = active[0], shares[0]
    if (not LegacyAccountMap.objects.select_for_update().filter(account_id=provider.provider_account_id,
            legacy_user_id=provider.provider_id).exists()
            or not EscortProfile.objects.select_for_update().filter(account_id=provider.provider_account_id,
                                                                    user_id=provider.provider_id).exists()):
        raise ValidationError('陪玩账号归属待核对')
    refunds = list(order.refund_records.select_for_update().order_by('pk'))
    if prior.zero_order_id:
        if refunds or order.payment_status != Order.PaymentStatus.PAID or order.refunded_at is not None:
            raise ValidationError('前次零退款后的资金记录待核对')
    else:
        expected = (Order.PaymentStatus.REFUNDED if prior.refund.amount == order.amount
                    else Order.PaymentStatus.PARTIALLY_REFUNDED)
        if (not 0 < prior.refund.amount <= order.amount or len(refunds) != 1
                or refunds[0].pk != prior.refund_id or order.payment_status != expected
                or order.refunded_at is None):
            raise ValidationError('前次退款后的资金记录待核对')
    payments = list(order.transactions.select_for_update().filter(
        tx_type='PAY', status='SUCCESS', reversal_of__isnull=True).select_related('wallet'))
    if (len(payments) != 1 or payments[0].amount != -order.amount
            or payments[0].wallet.account_id != order.customer_account_id
            or payments[0].wallet.user_id != order.customer_id
            or payments[0].balance_after != payments[0].balance_before + payments[0].amount
            or payments[0].reversals.exists()):
        raise ValidationError('原付款凭证待核对')
    other = previous.assignment
    allowed_credit_id = None
    if other.settled_at:
        from .negotiation_settlement import _verify_settled_assignment
        report = ProviderReport.objects.select_for_update().filter(order=order, provider_account_id=previous.account_id).first()
        try:
            _verify_settled_assignment(order, prior, other, previous, report)
        except SettlementError as exc:
            raise ValidationError('已结算陪玩的收益凭证待核对') from exc
        allowed_credit_id = previous.income_transaction_id
    elif previous.settled_at or previous.income_transaction_id or previous.settled_report_id:
        raise ValidationError('前次协商的收益状态待核对')
    credits = list(order.transactions.select_for_update().filter(
        Q(tx_type__in=['INCOME', 'SHOP_INCOME', 'GIFT']), status='SUCCESS', reversal_of__isnull=True))
    if {row.pk for row in credits} != ({allowed_credit_id} if allowed_credit_id else set()):
        raise ValidationError('订单已有未核验的收益流水')
    return {'order_id':order.pk, 'case_id':case.pk, 'prior_execution_id':prior.pk,
            'provider_account_id':provider.provider_account_id, 'completed_at':order.completed_at,
            'completion_log_id':logs[0].pk, 'previous_already_settled':bool(other.settled_at)}
