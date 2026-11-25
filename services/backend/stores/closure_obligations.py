"""Store-wide, query-only closure obligation inventory.

This module deliberately does not call per-account refund or retirement helpers:
those helpers have their own locking and lifecycle prerequisites.  A successful
empty query is the only way a reliable category can report zero.
"""
from __future__ import annotations

import hashlib
import json

from django.db import DatabaseError, InterfaceError
from django.db.models import Q

from console.payroll_models import PayrollRun
from engagement.models import MemberBenefitGrant, RewardClaim
from orders.models import AfterSale, Order, OrderContinuation, OrderProvider, Reservation
from wallet.models import (
    IncomeHold, ProviderReport, RechargeGiftApproval, WithdrawRequest,
)
from wallet.refund_deposit_models import RefundDepositDeficit, RefundDepositRecoveryPermit

from .context import current_database, current_store


CATEGORY_CODES = (
    'AFTERSALES',
    'DEPOSIT_DEFICITS',
    'GIFT_APPROVALS',
    'INCOME_HOLDS',
    'ORDERS',
    'OTHER_RIGHTS',
    'PAYROLL',
    'RECOVERY_PERMITS',
    'REPORTS',
    'RESERVATIONS',
    'REWARD_CLAIMS',
    'WITHDRAWALS',
)
_FACT_VERSION = 1


def _blocker(code, resource, reference):
    return {'code': code, 'resource': resource, 'reference': reference}


def _record_blocker(code, resource, pk):
    return _blocker(code, resource, f'{resource}:{pk}')


def _store_blocker(store, code, resource):
    return _blocker(code, resource, f'store:{store.pk}')


def _status_rows(queryset, *, resource, blocker_code, amounts=(), kind_field=None):
    fields = ['pk', 'status']
    if kind_field:
        fields.append(kind_field)
    fields.extend(amounts)
    rows = list(queryset.order_by('pk').values_list(*fields))
    facts, blockers = [], []
    for row in rows:
        pk, status = row[:2]
        offset = 2
        fact = {'id': pk, 'type': resource, 'status': status}
        if kind_field:
            fact['kind'] = row[offset]
            offset += 1
        for field, value in zip(amounts, row[offset:]):
            fact[field] = value
        facts.append(fact)
        blockers.append(_record_blocker(blocker_code, resource, pk))
    return len(facts), facts, blockers, 'VERIFIED'


def _read_aftersales(store):
    rows = AfterSale.objects.using(store.database_alias).filter(status='OPEN')
    return _status_rows(
        rows, resource='aftersale', blocker_code='AFTERSALES_OPEN',
    )


def _read_deposit_deficits(store):
    rows = list(
        RefundDepositDeficit.objects.using(store.database_alias)
        .filter(closed_at__isnull=True)
        .order_by('pk')
        .values_list('pk', 'amount', 'remaining')
    )
    facts = [
        {
            'id': pk, 'type': 'refund_deposit_deficit', 'status': 'OPEN',
            'amount': amount, 'remaining': remaining,
        }
        for pk, amount, remaining in rows
    ]
    blockers = [
        _record_blocker('DEPOSIT_DEFICITS_OPEN', 'refund_deposit_deficit', pk)
        for pk, _amount, _remaining in rows
    ]
    return len(facts), facts, blockers, 'VERIFIED'


def _read_gift_approvals(store):
    rows = RechargeGiftApproval.objects.using(store.database_alias).filter(status='PENDING')
    return _status_rows(
        rows, resource='recharge_gift_approval', blocker_code='GIFT_APPROVALS_PENDING',
        amounts=('standard_amount', 'requested_amount'),
    )


def _read_income_holds(store):
    rows = list(
        IncomeHold.objects.using(store.database_alias)
        .filter(released_at__isnull=True, cancelled_at__isnull=True)
        .order_by('pk')
        .values_list('pk', 'amount', 'recovered_amount')
    )
    facts = [
        {
            'id': pk, 'type': 'income_hold', 'status': 'UNRELEASED',
            'amount': amount, 'recovered_amount': recovered_amount,
        }
        for pk, amount, recovered_amount in rows
    ]
    blockers = [
        _record_blocker('INCOME_HOLDS_UNRELEASED', 'income_hold', pk)
        for pk, _amount, _recovered_amount in rows
    ]
    return len(facts), facts, blockers, 'VERIFIED'


def _read_orders(store):
    alias = store.database_alias
    outstanding = Q(status__in=[
        Order.Status.PENDING, Order.Status.GRABBED, Order.Status.IN_SERVICE,
    ])
    outstanding |= Q(status=Order.Status.COMPLETED, settled_at__isnull=True)
    orders = list(
        Order.objects.using(alias)
        .filter(outstanding)
        .order_by('pk')
        .values_list(
            'pk', 'status', 'payment_status', 'settled_at', 'amount',
            'provider_income', 'inviter_commission',
        )
    )
    # A completed header can be final while one provider share is still unpaid.
    # Count unique orders, but retain each outstanding share as a separate fact.
    shares = list(
        OrderProvider.objects.using(alias)
        .filter(order__status=Order.Status.COMPLETED, settled_at__isnull=True)
        .order_by('pk')
        .values_list(
            'pk', 'order_id', 'settlement_base', 'provider_income', 'released_at',
        )
    )
    facts, blockers, order_ids = [], [], set()
    for pk, status, payment_status, settled_at, amount, provider_income, inviter_commission in orders:
        order_ids.add(pk)
        facts.append({
            'id': pk, 'type': 'order', 'status': status,
            'payment_status': payment_status, 'settled': settled_at is not None,
            'amount': amount, 'provider_income': provider_income,
            'inviter_commission': inviter_commission,
        })
        blockers.append(_record_blocker('ORDERS_OUTSTANDING', 'order', pk))
    for pk, order_id, settlement_base, provider_income, released_at in shares:
        order_ids.add(order_id)
        facts.append({
            'id': pk, 'type': 'order_provider', 'order_id': order_id,
            'status': 'UNSETTLED', 'settlement_base': settlement_base,
            'provider_income': provider_income, 'released': released_at is not None,
        })
        blockers.append(_record_blocker('ORDERS_OUTSTANDING', 'order_provider', pk))
    return len(order_ids), facts, blockers, 'VERIFIED'


def _read_other_rights(store):
    # MemberBenefitGrant has a claim time but no fulfillment/consumption state.
    # Read safe facts for change detection without turning an empty table into 0.
    rows = list(
        MemberBenefitGrant.objects.using(store.database_alias)
        .order_by('pk')
        .values_list('pk', 'kind', 'quantity')
    )
    facts = [
        {'id': pk, 'type': 'member_benefit_grant', 'kind': kind, 'quantity': quantity}
        for pk, kind, quantity in rows
    ]
    return (
        None, facts,
        [_store_blocker(store, 'OTHER_RIGHTS_UNVERIFIABLE', 'other_rights')],
        'UNVERIFIABLE',
    )


def _read_payroll(store):
    # PayrollRun is a confirmed costing snapshot, not proof of actual payment.
    rows = list(
        PayrollRun.objects.using(store.database_alias)
        .order_by('pk')
        .values_list('pk', 'month', 'fingerprint')
    )
    facts = [
        {
            'id': pk, 'type': 'payroll_run', 'month': month.isoformat(),
            'fingerprint': fingerprint,
        }
        for pk, month, fingerprint in rows
    ]
    return (
        None, facts,
        [_store_blocker(store, 'PAYROLL_UNVERIFIABLE', 'payroll')],
        'UNVERIFIABLE',
    )


def _read_recovery_permits(store):
    rows = list(
        RefundDepositRecoveryPermit.objects.using(store.database_alias)
        .filter(closed_at__isnull=True)
        .order_by('pk')
        .values_list('pk', 'order_id')
    )
    facts = [
        {
            'id': pk, 'type': 'refund_deposit_recovery_permit',
            'order_id': order_id, 'status': 'OPEN',
        }
        for pk, order_id in rows
    ]
    blockers = [
        _record_blocker(
            'RECOVERY_PERMITS_OPEN', 'refund_deposit_recovery_permit', pk,
        )
        for pk, _order_id in rows
    ]
    return len(facts), facts, blockers, 'VERIFIED'


def _read_reports(store):
    rows = ProviderReport.objects.using(store.database_alias).filter(
        status__in=[ProviderReport.Status.DRAFT, ProviderReport.Status.PENDING],
    )
    return _status_rows(
        rows, resource='provider_report', blocker_code='REPORTS_PENDING',
        amounts=('amount', 'payout_amount'),
    )


def _read_reservations(store):
    rows = Reservation.objects.using(store.database_alias).filter(status__in=[
        Reservation.Status.PENDING, Reservation.Status.CONFIRMED,
    ])
    return _status_rows(
        rows, resource='reservation', blocker_code='RESERVATIONS_ACTIVE',
    )


def _paid_continuation_is_verified(claim, alias):
    """Read-only receipt validation; never trust reward_state='PAID' alone."""
    if not claim.has_staff_verification():
        return False
    participants = claim.participants if isinstance(claim.participants, list) else []
    if not participants:
        return False
    account_ids = []
    for item in participants:
        if not isinstance(item, dict):
            return False
        identity = (
            item.get('provider_account_id'), item.get('provider_id'),
            item.get('provider_business_cycle'),
        )
        if any(type(value) is not int or value <= 0 for value in identity):
            return False
        account_ids.append(identity[0])
    if len(set(account_ids)) != len(account_ids):
        return False
    rewards = list(
        claim.rewards.using(alias)
        .select_related('transaction__wallet')
        .order_by('pk')
    )
    if len(rewards) != len(participants):
        return False
    try:
        return all(reward.has_verified_payment() for reward in rewards)
    except (DatabaseError, InterfaceError):
        raise
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def _read_reward_claims(store):
    alias = store.database_alias
    count, facts, blockers, _state = _status_rows(
        RewardClaim.objects.using(alias).filter(status__in=[
            RewardClaim.Status.PENDING, RewardClaim.Status.FULFILLING,
        ]),
        resource='reward_claim', blocker_code='REWARD_CLAIMS_OUTSTANDING',
        amounts=('points_spent', 'balance_amount'), kind_field='kind_snapshot',
    )
    claims = list(
        OrderContinuation.objects.using(alias)
        .filter(
            Q(status='PENDING')
            | (Q(status='VERIFIED') & ~Q(reward_state='INELIGIBLE'))
        )
        .order_by('pk')
        .only(
            'id', 'status', 'reward_state', 'continuation_order',
            'verified_by', 'verified_at', 'participants',
        )
    )
    for claim in claims:
        receipt_state = 'NOT_APPLICABLE'
        if claim.status == 'VERIFIED' and claim.reward_state == 'PAID':
            if _paid_continuation_is_verified(claim, alias):
                continue
            receipt_state = 'REVIEW'
        facts.append({
            'id': claim.pk, 'type': 'order_continuation',
            'status': claim.status, 'reward_state': claim.reward_state,
            'receipt_state': receipt_state,
        })
        blockers.append(_record_blocker(
            'REWARD_CLAIMS_OUTSTANDING', 'order_continuation', claim.pk,
        ))
        count += 1
    return count, facts, blockers, 'VERIFIED'


def _read_withdrawals(store):
    rows = WithdrawRequest.objects.using(store.database_alias).filter(status__in=[
        WithdrawRequest.Status.PENDING,
        WithdrawRequest.Status.APPROVED,
        WithdrawRequest.Status.STORE_APPROVED,
        WithdrawRequest.Status.PLATFORM_APPROVED,
        WithdrawRequest.Status.PAYMENT_UNKNOWN,
    ])
    return _status_rows(
        rows, resource='withdrawal', blocker_code='WITHDRAWALS_IN_FLIGHT',
        amounts=('amount', 'tax_amount', 'actual_amount'),
    )


_READERS = {
    'AFTERSALES': _read_aftersales,
    'DEPOSIT_DEFICITS': _read_deposit_deficits,
    'GIFT_APPROVALS': _read_gift_approvals,
    'INCOME_HOLDS': _read_income_holds,
    'ORDERS': _read_orders,
    'OTHER_RIGHTS': _read_other_rights,
    'PAYROLL': _read_payroll,
    'RECOVERY_PERMITS': _read_recovery_permits,
    'REPORTS': _read_reports,
    'RESERVATIONS': _read_reservations,
    'REWARD_CLAIMS': _read_reward_claims,
    'WITHDRAWALS': _read_withdrawals,
}


def _assert_store_context(store):
    selected = current_store()
    if store is None or selected is None or current_database() != store.database_alias:
        raise ValueError('门店上下文与闭店义务扫描目标不一致')
    if any(
        getattr(selected, field, None) != getattr(store, field, None)
        for field in ('pk', 'slug', 'database_alias')
    ):
        raise ValueError('门店上下文与闭店义务扫描目标不一致')


def closure_obligation_readiness(store):
    """Return a complete store-wide inventory without writing any business row.

    ``count=None`` always means that the category cannot be proven, whether the
    cause is a failed read or a model that lacks a final fulfillment/payment
    state.  Known positive blockers from other categories remain visible.
    """
    _assert_store_context(store)
    metrics, blockers = [], []
    facts = {
        'version': _FACT_VERSION,
        'store': {'id': store.pk, 'slug': store.slug},
        'categories': {},
    }
    for code in CATEGORY_CODES:
        try:
            count, records, category_blockers, state = _READERS[code](store)
        except (DatabaseError, InterfaceError):
            count, records, state = None, [], 'READ_FAILED'
            category_blockers = [_store_blocker(
                store, f'{code}_READ_FAILED', code.lower(),
            )]
        metrics.append({'code': code, 'count': count})
        blockers.extend(category_blockers)
        facts['categories'][code] = {'state': state, 'records': records}

    blockers.sort(key=lambda row: (row['code'], row['resource'], row['reference']))
    complete = all(row['count'] is not None for row in metrics)
    if not complete:
        status = 'UNKNOWN'
    elif any(row['count'] > 0 for row in metrics):
        status = 'BLOCKED'
    else:
        status = 'CLEAR'
    canonical = json.dumps(
        facts, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
    ).encode('utf-8')
    return {
        'complete': complete,
        'status': status,
        'metrics': metrics,
        'blockers': blockers,
        'facts_sha256': hashlib.sha256(canonical).hexdigest(),
    }


__all__ = ['closure_obligation_readiness']
