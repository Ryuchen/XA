"""Read each authorized business database separately; failures stay visible."""
from django.db import DatabaseError, InterfaceError
from django.db.models import F, Q, Sum
from django.utils import timezone

from club_accounts.models import ClubAccount
from orders.models import Order
from wallet.models import Transaction, WithdrawRequest
from .context import store_context
from .db import transaction
from .platform_permissions import platform_may, platform_stores_for


def _read_store(store):
    with store_context(store), transaction.atomic():
        paid = WithdrawRequest.objects.filter(workflow_version=2, status=WithdrawRequest.Status.PAID)
        verified = (Q(paid_at__isnull=False, payout_recorded_at__isnull=False,
                      platform_payer_id__isnull=False, transaction__isnull=False)
                    & ~Q(payout_reference='')
                    & Q(actual_amount=F('amount') - F('tax_amount'),
                        transaction__tx_type=Transaction.TxType.WITHDRAW,
                        transaction__status=Transaction.Status.SUCCESS,
                        transaction__amount=-F('amount'), transaction__wallet__account_id=F('account_id')))
        needs_review = paid.exclude(verified).exists()
        if needs_review:
            paid_summary = {'status': 'REVIEW', 'count': None, 'actual_amount': None, 'actual_amount_fen': None}
        else:
            from console.payroll_calculation import raw_to_fen
            amount = paid.aggregate(value=Sum('actual_amount'))['value'] or 0
            paid_summary = {'status': 'OK', 'count': paid.count(), 'actual_amount': amount,
                            'actual_amount_fen': raw_to_fen(amount)}
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
