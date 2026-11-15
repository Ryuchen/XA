"""只读盘点历史待生效通行证；不创建账户、钱包、退款或流水。"""
import json
from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.models import Count
from django.utils import timezone

from club_accounts.models import LegacyAccountMap
from users.models import EscortProfile, ProviderPassPurchase
from wallet.models import Transaction


def audit_pending(database, *, now=None):
    """Return store-local evidence, never a decision to refund or an estimate of funding."""
    now = now or timezone.now()
    purchases = list(
        ProviderPassPurchase.objects.using(database)
        .filter(starts_at__gt=now, refund__isnull=True)
        .select_related('transaction__wallet')
        .order_by('provider_id', 'starts_at', 'id')
    )
    provider_ids = {p.provider_id for p in purchases}
    profiles = {
        p.user_id: p for p in EscortProfile.objects.using(database)
        .filter(user_id__in=provider_ids)
    }
    mappings = dict(LegacyAccountMap.objects.using(database)
                    .filter(legacy_user_id__in=provider_ids)
                    .values_list('legacy_user_id', 'account_id'))
    tx_ids = {p.transaction_id for p in purchases}
    # Count all purchases sharing a payment, including already active records.
    uses = dict(ProviderPassPurchase.objects.using(database)
                .filter(transaction_id__in=tx_ids).values_list('transaction_id')
                .annotate(n=Count('id')))
    platform_rows = list(Transaction.objects.using(database).filter(
        tx_type=Transaction.TxType.PASS_INCOME,
        status=Transaction.Status.SUCCESS,
        wallet__user__username=settings.PLATFORM_SYSTEM_USERNAME,
        external_tx_id__in=[p.transaction.tx_no for p in purchases],
    ).values_list('external_tx_id', 'amount'))
    platform_refs = Counter(platform_rows)
    platform_ref_counts = Counter(ref for ref, _ in platform_rows)

    active_ends = {}
    for provider_id, starts, ends in ProviderPassPurchase.objects.using(database).filter(
        provider_id__in=provider_ids, starts_at__lte=now, expires_at__gt=now,
    ).values_list('provider_id', 'starts_at', 'expires_at'):
        active_ends[provider_id] = max(active_ends.get(provider_id, ends), ends)

    result, previous = [], {}
    for p in purchases:
        issues = []
        profile, tx, wallet = profiles.get(p.provider_id), p.transaction, p.transaction.wallet
        mapped = mappings.get(p.provider_id)
        if not profile:
            issues.append('missing_profile')
        else:
            if profile.account_id and mapped and profile.account_id != mapped:
                issues.append('profile_mapping_conflict')
            if p.account_id and profile.account_id and p.account_id != profile.account_id:
                issues.append('purchase_profile_account_conflict')
            if not profile.pass_expires_at or profile.pass_expires_at < p.expires_at:
                issues.append('profile_expiry_before_pending_end')
        if p.account_id and mapped and p.account_id != mapped:
            issues.append('purchase_mapping_conflict')
        expected_account = p.account_id or (profile.account_id if profile else None) or mapped
        if not expected_account:
            issues.append('missing_business_account')
        if wallet.user_id and wallet.user_id != p.provider_id:
            issues.append('wallet_user_conflict')
        if wallet.account_id and expected_account and wallet.account_id != expected_account:
            issues.append('wallet_account_conflict')
        if wallet.user_id is None and wallet.account_id is None:
            issues.append('wallet_owner_missing')
        if tx.tx_type != Transaction.TxType.PASS_PURCHASE or tx.status != Transaction.Status.SUCCESS:
            issues.append('payment_not_successful_pass_purchase')
        if tx.amount != -p.paid_amount:
            issues.append('payment_amount_mismatch')
        if uses.get(p.transaction_id, 0) != 1:
            issues.append('payment_shared_by_purchases')
        if platform_refs[(tx.tx_no, p.paid_amount)] != 1 or platform_ref_counts[tx.tx_no] != 1:
            issues.append('platform_receipt_missing_or_duplicate')
        prior = previous.get(p.provider_id)
        if active_ends.get(p.provider_id) and p.starts_at < active_ends[p.provider_id]:
            issues.append('active_period_overlap')
        if prior and p.starts_at < prior.expires_at:
            issues.append('pending_period_overlap')
        previous[p.provider_id] = p if not prior or p.expires_at > prior.expires_at else prior
        result.append({
            'purchase_id': p.pk, 'provider_id': p.provider_id,
            'account_id': expected_account, 'paid_amount_units': p.paid_amount,
            'starts_at': p.starts_at.isoformat(), 'expires_at': p.expires_at.isoformat(),
            'issues': issues,
        })
    return {
        'database_alias': database, 'observed_at': now.isoformat(),
        'pending_count': len(result),
        'paid_amount_units': sum(row['paid_amount_units'] for row in result),
        'records_with_warnings': sum(bool(row['issues']) for row in result),
        'records': result,
    }


class Command(BaseCommand):
    help = '只读盘点指定门店数据库的历史待生效通行证；不执行退款'

    def add_arguments(self, parser):
        parser.add_argument('--database', required=True, help='明确指定单个门店业务库别名')

    def handle(self, *args, **options):
        database = options['database']
        if database not in connections:
            raise CommandError('未知数据库别名')
        self.stdout.write(json.dumps(audit_pending(database), ensure_ascii=False))
