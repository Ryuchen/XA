"""Exercise concurrent deposits only on isolated, uniquely named QA identities."""
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
from console.models import AdminMembership, AdminRole  # noqa: E402
from stores.context import resolve_store, store_context  # noqa: E402
from stores.db import transaction  # noqa: E402
from users.models import EscortProfile  # noqa: E402
from wallet.club_rules import deposit_target  # noqa: E402
from wallet.models import CashDepositTopup, Transaction, Wallet, get_platform_wallet  # noqa: E402
from wallet.services import get_wallet  # noqa: E402

stores = {'shenglang': ('default', 'xa_test_fronted_acceptance_20261104'),
          'xinan': ('store_b', 'xa_test_store_preview_b_20261104')}
slug = sys.argv[1] if len(sys.argv) == 2 else 'shenglang'
if slug not in stores or len(sys.argv) > 2:
    raise SystemExit('Expected store: shenglang or xinan')
alias, database = stores[slug]
config = settings.DATABASES[alias]
if config['NAME'] != database or config['HOST'] != '127.0.0.1' or str(config['PORT']) != '13306':
    raise SystemExit('Isolated database boundary mismatch')
with connections[alias].cursor() as cursor:
    cursor.execute('SELECT DATABASE()')
    if cursor.fetchone()[0] != database:
        raise SystemExit('Unexpected actual database')
store = resolve_store(slug)
if store.database_alias != alias:
    raise SystemExit('Unexpected store binding')

nonce = uuid.uuid4().hex
names = ['qa-cash-provider-' + nonce, 'qa-cash-staff-' + nonce]
with override_settings(PLATFORM_SYSTEM_USERNAME='qa-cash-system-' + nonce):
    try:
        with store_context(store), transaction.atomic():
            User = get_user_model()
            provider = User.objects.create_user(username=names[0], password=secrets.token_urlsafe(24), role='PROVIDER')
            staff = User.objects.create_user(username=names[1], password=secrets.token_urlsafe(24), role='OPERATOR')
            provider_account = get_or_create_account_for_legacy_user(provider)
            staff_account = get_or_create_account_for_legacy_user(staff)
            staff_account.is_phone_verified = True
            staff_account.save(update_fields=['is_phone_verified'])
            membership = AdminMembership.objects.create(user=staff, account=staff_account)
            role = AdminRole.objects.create(name='隔离押金测试-' + nonce, code='qa-cash-' + nonce,
                permissions=['escort:view', 'escort:deposit_topup'])
            membership.roles.add(role)
            target = deposit_target('CASH')
            amount = max(1, target // 2)
            profile = EscortProfile.objects.create(user=provider, account=provider_account, display_name='隔离押金测试',
                deposit_mode='CASH', deposit_paid=target - amount, is_verified=True)
            wallet = get_wallet(account=provider_account, user=provider, for_update=True)
            platform = get_platform_wallet(for_update=True)
            assert platform.balance == 0
            if profile.deposit_paid:
                Transaction.objects.create(wallet=wallet, amount=-profile.deposit_paid, tx_type='DEPOSIT',
                    balance_before=profile.deposit_paid, balance_after=0, remark='隔离测试历史押金')
                platform.balance = profile.deposit_paid
                platform.save(update_fields=['balance'])
                Transaction.objects.create(wallet=platform, amount=profile.deposit_paid, tx_type='DEPOSIT_INCOME',
                    balance_before=0, balance_after=profile.deposit_paid, remark='隔离测试历史押金')
            wallet.balance = 1234
            wallet.save(update_fields=['balance'])
            Transaction.objects.create(wallet=wallet, amount=1234, tx_type='ADJUST', balance_before=0,
                balance_after=1234, remark='隔离测试初始资金')
            token = issue_account_tokens(staff_account, auth_method='password_sms')[0]
        barrier, mutex, results = threading.Barrier(2), threading.Lock(), []

        def submit(index):
            try:
                with connections[alias].cursor() as cursor:
                    cursor.execute('SET SESSION innodb_lock_wait_timeout = 10')
                client = APIClient(HTTP_HOST='admin.esports.club')
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                barrier.wait(timeout=10)
                response = client.post(f'/api/admin/escorts/{profile.pk}/deposit-topups/', {
                    'amount': amount, 'reason': '隔离并发测试', 'reference': f'qa-{nonce}-{index}',
                    'client_request_id': f'qa-cash-{nonce}-{index}',
                }, format='json')
                result = response.data.get('code')
            except Exception as error:
                result = type(error).__name__
            finally:
                connections[alias].close()
            with mutex:
                results.append(result)

        workers = [threading.Thread(target=submit, args=(i,)) for i in range(2)]
        for worker in workers: worker.start()
        for worker in workers: worker.join(timeout=25)
        if any(worker.is_alive() for worker in workers):
            raise RuntimeError('Concurrent cash deposit test did not terminate')
        with store_context(store):
            profile.refresh_from_db(); wallet.refresh_from_db(); platform.refresh_from_db()
            records = CashDepositTopup.objects.filter(profile=profile)
            if sorted(results, key=str) != [0, 400] or records.count() != 1:
                raise RuntimeError(f'Unexpected concurrent result: {results}')
            if profile.deposit_paid != target or wallet.balance != 1234 or platform.balance != target:
                raise RuntimeError('Cash deposit balances do not reconcile')
            if records.get().platform_transaction.wallet_id != platform.pk or records.get().platform_transaction.amount != amount:
                raise RuntimeError('Store deposit ledger is not unique')
        print(json.dumps({'store': slug, 'isolated_mysql': True, 'concurrent_deposit': 'passed',
                          'provider_wallet': 'unchanged', 'store_deposit_ledger': 'reconciled'}))
    finally:
        with store_context(store), transaction.atomic():
            for user in get_user_model().objects.filter(username__in=names):
                user.is_active = False
                user.save(update_fields=['is_active'])
                from club_accounts.models import LegacyAccountMap
                mapping = LegacyAccountMap.objects.filter(legacy_user_id=user.pk).select_related('account').first()
                if mapping:
                    mapping.account.is_active = False
                    mapping.account.can_login = False
                    mapping.account.save(update_fields=['is_active', 'can_login', 'updated_at'])
                    Wallet.objects.filter(account=mapping.account).update(is_active=False)
