"""Informational funding breakdown for a proposed in-service refund.

This is not a refund plan or reservation. Execution must independently lock and
reconcile the order, wallets, deposit backing and refund ledgers before posting.
"""
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from users.models import EscortProfile
from wallet.models import Wallet


def funding_preview(order, terms):
    rows = {row.provider_account_id: row for row in order.providers.all()}
    results = []
    for term in terms:
        account_id = term['account_id']
        share = rows.get(account_id)
        profile = EscortProfile.objects.filter(account_id=account_id).first()
        if not share or not profile or not share.provider_id or profile.user_id != share.provider_id:
            raise ValidationError('陪玩身份或收益归属待核对')
        wallets = list(Wallet.objects.filter(Q(account_id=account_id) | Q(user_id=share.provider_id)))
        if (len(wallets) > 1 or any(not wallet.is_active or wallet.balance < 0
                or wallet.account_id not in (None, account_id)
                or wallet.user_id not in (None, share.provider_id) for wallet in wallets)
                or profile.deposit_paid < 0):
            raise ValidationError('陪玩钱包或押金记录待核对')
        available = wallets[0].balance if wallets else 0
        remaining = term['burden_units']
        pending = min(remaining, term['original_income_units'])
        remaining -= pending
        wallet_part = min(remaining, available)
        remaining -= wallet_part
        deposit_part = min(remaining, profile.deposit_paid)
        remaining -= deposit_part
        results.append({'account_id':account_id, 'pending_income_units':pending,
                        'wallet_units':wallet_part, 'deposit_units':deposit_part,
                        'shortfall_units':remaining})
    return {'providers':results, 'book_amounts_sufficient':all(row['shortfall_units'] == 0 for row in results),
            'calculated_at':timezone.now(), 'executable':False}
