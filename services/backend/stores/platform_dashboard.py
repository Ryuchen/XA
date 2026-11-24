"""Read each authorized business database separately; failures stay visible."""
from django.db import DatabaseError, InterfaceError
from django.db.models import Case, Exists, F, FilteredRelation, OuterRef, Q, Value, When
from django.db.models.functions import Length
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from club_accounts.models import ClubAccount
from orders.models import Order
from wallet.models import Transaction, WithdrawRequest
from .context import store_context
from .db import transaction
from .platform_permissions import platform_may, platform_stores_for
from .platform_withdrawal_audit import ACTION_TRANSITIONS


def _matches_payout_detail(detail, amount, reference, paid_at):
    if not isinstance(detail, dict) or type(detail.get('paid_amount_fen')) is not int:
        return False
    if detail['paid_amount_fen'] != amount or detail.get('payout_reference') != reference:
        return False
    value = detail.get('paid_at')
    if not isinstance(value, str):
        return False
    try:
        timestamp = parse_datetime(value)
    except (ValueError, OverflowError):
        return False
    return timestamp is not None and timezone.is_aware(timestamp) and timestamp == paid_at


def _paid_withdrawal_summary():
    from console.payroll_calculation import raw_to_fen
    from_statuses, paid = ACTION_TRANSITIONS['paid']
    candidates = WithdrawRequest.objects.filter(
        workflow_version=2, status__in=(paid, WithdrawRequest.Status.PAYMENT_UNKNOWN)).alias(
            paid_event=FilteredRelation('platform_events', condition=Q(platform_events__action='paid')),
        ).alias(payout_fingerprint_length=Length('paid_event__request_fingerprint'))
    shared_debit = WithdrawRequest.objects.filter(transaction_id=OuterRef('transaction_id')).exclude(pk=OuterRef('pk'))
    reversed_debit = Transaction.objects.filter(reversal_of_id=OuterRef('transaction_id'))
    verified = (
        Q(status=paid, actual_amount__gt=0, account_business_cycle__gt=0, release__isnull=True,
          platform_reviewer_id__isnull=False, platform_reviewed_at__gte=F('created_at'),
          paid_at__gte=F('platform_reviewed_at'), payout_recorded_at__gte=F('paid_at'),
          amount=F('actual_amount') + F('tax_amount'), amount__gt=0,
          transaction__tx_type=Transaction.TxType.WITHDRAW, transaction__status=Transaction.Status.SUCCESS,
          transaction__wallet__account_id=F('account_id'), transaction__reversal_of__isnull=True,
          transaction__balance_after=F('transaction__balance_before') + F('transaction__amount'),
          paid_event__from_status__in=from_statuses, paid_event__to_status=paid,
          paid_event__platform_actor_id=F('platform_payer_id'),
          paid_event__created_at__gte=F('payout_recorded_at'), payout_fingerprint_length=64)
        & Q(amount=-F('transaction__amount'))
        & (Q(user__isnull=True) | Q(transaction__wallet__user_id=F('user_id')))
        & ~Q(payout_reference='') & ~Q(paid_event__request_id='')
        & ~Exists(shared_debit) & ~Exists(reversed_debit)
    )
    # Join every paid event before validation so duplicate events remain visible.
    # Compare the original ISO timestamp in Python to retain its offset and precision.
    rows = candidates.annotate(evidence_valid=Case(When(verified, then=Value(True)), default=Value(False))).order_by(
        'pk', 'paid_event__pk').values_list('pk', 'actual_amount', 'payout_reference', 'paid_at',
                                          'paid_event__detail', 'evidence_valid')
    count = total = 0
    previous_id = None
    for pk, amount, reference, paid_at, detail, evidence_valid in rows.iterator(chunk_size=200):
        if pk == previous_id or not evidence_valid or not _matches_payout_detail(detail, amount, reference, paid_at):
            return {'status': 'REVIEW', 'count': None, 'actual_amount': None, 'actual_amount_fen': None}
        previous_id = pk
        count += 1
        total += amount
    return {'status': 'OK', 'count': count, 'actual_amount': total, 'actual_amount_fen': raw_to_fen(total)}


def _read_store(store):
    with store_context(store), transaction.atomic():
        paid_summary = _paid_withdrawal_summary()
        return {
            'orders': Order.objects.count(),
            'bosses': ClubAccount.objects.filter(account_type=ClubAccount.AccountType.BOSS).count(),
            'providers': ClubAccount.objects.filter(account_type=ClubAccount.AccountType.PROVIDER).count(),
            'paid_withdrawals': paid_summary,
        }


def aggregate_dashboard(account):
    if not platform_may(account, 'dashboard:view'):
        raise PermissionError('无平台汇总权限')
    records = []
    totals = {'orders': 0, 'bosses': 0, 'providers': 0}
    withdrawal_totals = {'count': 0, 'actual_amount_fen': 0}
    readable_withdrawal_stores = 0
    for store in platform_stores_for(account, 'dashboard:view').order_by('id'):
        row = {'slug': store.slug, 'name': store.name, 'store_status': store.status,
               'currency_name': store.currency_name,
               'read_at': None, 'status': 'UNAVAILABLE', 'counts': None}
        if store.status not in (store.Status.ACTIVE, store.Status.DISABLED) or not store.initialized_at:
            row['status'] = 'NOT_ACTIVE'
        else:
            try:
                from .dynamic_database import discover_store_connection
                if discover_store_connection(store):
                    row['counts'] = _read_store(store)
                    row['read_at'] = timezone.now().isoformat()
                    row['status'] = 'OK'
                    for key in totals:
                        totals[key] += row['counts'][key]
                    withdrawal = row['counts']['paid_withdrawals']
                    if withdrawal['status'] == 'OK':
                        readable_withdrawal_stores += 1
                        withdrawal_totals['count'] += withdrawal['count']
                        withdrawal_totals['actual_amount_fen'] += withdrawal['actual_amount_fen']
            except (DatabaseError, InterfaceError, ValueError):
                # Never replace an unavailable store with another database or zero.
                pass
        records.append(row)
    if not any(item['status'] == 'OK' for item in records):
        totals = None
    # Tenant reads can be slow; do not return data after platform access was revoked.
    if not platform_may(account, 'dashboard:view'):
        raise PermissionError('无平台汇总权限')
    return {'stores': records, 'partial_totals': totals,
            'partial_paid_withdrawals': withdrawal_totals if readable_withdrawal_stores else None,
            'complete': bool(records) and all(item['status'] == 'OK'
                and item['counts']['paid_withdrawals']['status'] == 'OK' for item in records),
            'generated_at': timezone.now().isoformat()}
