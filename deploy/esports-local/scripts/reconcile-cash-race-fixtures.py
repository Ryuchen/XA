"""Reconcile two specifically identified synthetic QA fixtures; never touches real users."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'services' / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.fronted_acceptance_settings'
import django  # noqa: E402
django.setup()
from django.conf import settings  # noqa: E402
from stores.context import resolve_store, store_context  # noqa: E402
from stores.db import transaction  # noqa: E402
from wallet.models import CashDepositTopup, Transaction, Wallet  # noqa: E402

fixtures = (
    ('shenglang', 'default', 'xa_test_fronted_acceptance_20261104', 34, 'qa-cash-system-eeb7b3ba57c343f39a51c316079029f3'),
    ('xinan', 'store_b', 'xa_test_store_preview_b_20261104', 12, 'qa-cash-system-a09a56cb068249e5986ef43208ca4e90'),
)
for slug, alias, database, profile_id, system_username in fixtures:
    config = settings.DATABASES[alias]
    if config['NAME'] != database or config['HOST'] != '127.0.0.1' or str(config['PORT']) != '13306':
        raise SystemExit('Isolated database boundary mismatch')
    store = resolve_store(slug)
    if store.database_alias != alias:
        raise SystemExit('Unexpected store binding')
    with store_context(store), transaction.atomic():
        record = CashDepositTopup.objects.select_for_update().select_related('account', 'profile', 'operator',
            'platform_transaction__wallet__user').get(pk=1, profile_id=profile_id)
        if (record.account.is_active or record.account.can_login or record.operator.is_active
                or not record.account.username.startswith('qa-cash-provider-')
                or not record.operator.username.startswith('qa-cash-staff-')
                or record.platform_transaction.wallet.user.username != system_username
                or record.profile.display_name != '隔离押金测试' or record.paid_before != 20000
                or record.amount != 20000 or record.profile.deposit_paid != 40000):
            raise SystemExit('Fixture identity or amounts do not match')
        wallet = Wallet.objects.select_for_update().get(account=record.account)
        platform = Wallet.objects.select_for_update().get(pk=record.platform_transaction.wallet_id)
        if Transaction.objects.filter(wallet=wallet, remark='隔离测试历史押金校正').exists():
            print(slug, 'already-reconciled')
            continue
        if wallet.balance != 1234 or platform.balance != 20000:
            raise SystemExit('Fixture wallet amounts do not match')
        Transaction.objects.create(wallet=wallet, amount=20000, tx_type='ADJUST',
            balance_before=1234, balance_after=21234, remark='隔离测试历史押金夹具资金')
        Transaction.objects.create(wallet=wallet, amount=-20000, tx_type='DEPOSIT',
            balance_before=21234, balance_after=1234, remark='隔离测试历史押金校正')
        platform.balance += 20000
        platform.save(update_fields=['balance'])
        Transaction.objects.create(wallet=platform, amount=20000, tx_type='DEPOSIT_INCOME',
            balance_before=20000, balance_after=40000, remark='隔离测试历史押金校正')
        print(slug, 'reconciled-qa-only')
