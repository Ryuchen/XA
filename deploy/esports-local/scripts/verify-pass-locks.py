"""Real MySQL pass-purchase race, restricted to fresh disabled QA accounts."""
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'services' / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.fronted_acceptance_settings'
import django  # noqa: E402
django.setup()
from django.conf import settings  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.db import connections  # noqa: E402
from django.test import override_settings  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402
from club_accounts.services import get_or_create_account_for_legacy_user  # noqa: E402
from club_accounts.tokens import issue_account_tokens  # noqa: E402
from stores.context import resolve_store, store_context  # noqa: E402
from stores.db import transaction  # noqa: E402
from users.models import EscortProfile, ProviderPassPurchase  # noqa: E402
from wallet.models import Transaction, Wallet, get_platform_wallet  # noqa: E402
from wallet.services import get_wallet  # noqa: E402

stores = {
    'shenglang': ('default', 'xa_test_fronted_acceptance_20261104', 'sl.esports.club'),
    'xinan': ('store_b', 'xa_test_store_preview_b_20261104', 'xa.esports.club'),
}
slug = sys.argv[1] if len(sys.argv) == 2 else 'shenglang'
if slug not in stores or len(sys.argv) > 2:
    raise SystemExit('Expected store: shenglang or xinan')
alias, expected_database, host = stores[slug]
if (settings.DATABASES[alias]['NAME'] != expected_database
        or settings.DATABASES[alias]['HOST'] != '127.0.0.1'
        or str(settings.DATABASES[alias]['PORT']) != '13306'):
    raise SystemExit('Isolated acceptance boundary mismatch')
with connections[alias].cursor() as cursor:
    cursor.execute('SELECT DATABASE()')
    if cursor.fetchone()[0] != expected_database:
        raise SystemExit('Unexpected database')

nonce = uuid.uuid4().hex
provider = account = None
store = resolve_store(slug)
if store.database_alias != alias:
    raise SystemExit('Unexpected store database binding')
with override_settings(PLATFORM_SYSTEM_USERNAME='qa-pass-system-' + nonce):
    try:
        with store_context(store), transaction.atomic():
            User = get_user_model()
            provider = User.objects.create_user(username='qa-pass-provider-' + nonce, password=secrets.token_urlsafe(24), role='PROVIDER')
            account = get_or_create_account_for_legacy_user(provider)
            account.is_phone_verified = True
            account.save(update_fields=['is_phone_verified'])
            EscortProfile.objects.create(user=provider, account=account, display_name='隔离通行证测试', is_verified=True, deposit_mode='EARNINGS')
            wallet = get_wallet(account=account, user=provider, for_update=True)
            before = wallet.balance
            wallet.balance = before + 100000
            wallet.save(update_fields=['balance'])
            Transaction.objects.create(wallet=wallet, tx_type='ADJUST', amount=100000, balance_before=before,
                                       balance_after=wallet.balance, remark='隔离测试初始资金')
            platform = get_platform_wallet(for_update=True)
            assert platform.balance == 0
            token = issue_account_tokens(account, auth_method='password_sms')[0]
        barrier = threading.Barrier(2)
        mutex = threading.Lock()
        results = []
        def buy(index):
            try:
                with connections[alias].cursor() as cursor:
                    cursor.execute('SET SESSION innodb_lock_wait_timeout = 10')
                client = APIClient(HTTP_HOST=host)
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                barrier.wait(timeout=10)
                response = client.post('/api/users/provider-pass/', {'tier': 'BRONZE', 'days': 1,
                    'client_request_id': f'qa-pass-race-{nonce}-{index}'}, format='json')
                value = response.data.get('code')
            except Exception as error:
                value = type(error).__name__
            finally:
                connections[alias].close()
            with mutex:
                results.append(value)
        workers = [threading.Thread(target=buy, args=(index,)) for index in range(2)]
        for worker in workers: worker.start()
        for worker in workers: worker.join(timeout=25)
        if any(worker.is_alive() for worker in workers):
            raise RuntimeError('Concurrent purchase test did not terminate')
        with store_context(store):
            purchases = list(ProviderPassPurchase.objects.filter(account=account))
            wallet.refresh_from_db()
            platform.refresh_from_db()
            if sorted(results, key=str) != [0, 400] or len(purchases) != 1:
                raise RuntimeError('Concurrent purchase results did not match expectations')
            paid = purchases[0].paid_amount
            if wallet.balance != 100000 - paid or platform.balance != paid:
                raise RuntimeError('Concurrent purchase balances do not reconcile')
            if (wallet.transactions.filter(tx_type='PASS_PURCHASE').count() != 1
                    or platform.transactions.filter(tx_type='PASS_INCOME').count() != 1):
                raise RuntimeError('Concurrent purchase ledger is not unique')
        print(json.dumps({'store': slug, 'isolated_mysql': True, 'concurrent_pass_purchase': 'passed', 'wallet_ledger': 'reconciled'}))
    finally:
        if account and provider:
            with store_context(store), transaction.atomic():
                # Disable only the uniquely created QA identities; retain their ledger.
                account_model = type(account)
                saved_account = account_model.objects.filter(pk=account.pk).first()
                saved_provider = get_user_model().objects.filter(pk=provider.pk, username='qa-pass-provider-' + nonce).first()
                if saved_account and saved_provider:
                    saved_account.is_active = False
                    saved_account.can_login = False
                    saved_account.save(update_fields=['is_active', 'can_login', 'updated_at'])
                    saved_provider.is_active = False
                    saved_provider.save(update_fields=['is_active'])
                    Wallet.objects.filter(account=saved_account).update(is_active=False)
