"""Use only uniquely named synthetic accounts in a registered isolated store."""
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import uuid
from datetime import timedelta

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'services' / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.fronted_acceptance_settings'
import django  # noqa: E402
django.setup()
from django.conf import settings  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.db import connections  # noqa: E402
from django.test import override_settings  # noqa: E402
from django.utils import timezone  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402
from club_accounts.services import get_or_create_account_for_legacy_user  # noqa: E402
from club_accounts.tokens import issue_account_tokens  # noqa: E402
from stores.context import resolve_store, store_context  # noqa: E402
from stores.db import transaction  # noqa: E402
from users.models import EscortProfile, ProviderPassPurchase, ProviderPassRefund  # noqa: E402
from wallet.models import Transaction, Wallet, get_platform_wallet  # noqa: E402
from wallet.services import get_wallet  # noqa: E402

stores = {'shenglang': ('default', 'xa_test_fronted_acceptance_20261104', 'sl.esports.club'),
          'xinan': ('store_b', 'xa_test_store_preview_b_20261104', 'xa.esports.club')}
if len(sys.argv) != 2 or sys.argv[1] not in stores:
    raise SystemExit('Expected store: shenglang or xinan')
slug = sys.argv[1]
alias, database, host = stores[slug]
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
name, shop_name = 'qa-pass-refund-' + nonce, 'qa-pass-shop-' + nonce
with override_settings(PLATFORM_SYSTEM_USERNAME=shop_name):
    try:
        with store_context(store), transaction.atomic():
            User = get_user_model()
            provider = User.objects.create_user(username=name, password=secrets.token_urlsafe(24), role='PROVIDER')
            account = get_or_create_account_for_legacy_user(provider)
            account.is_phone_verified = True
            account.save(update_fields=['is_phone_verified'])
            now = timezone.now()
            profile = EscortProfile.objects.create(user=provider, account=account, display_name='隔离退款测试',
                deposit_mode='EARNINGS', is_verified=True, pass_tier='BRONZE',
                pass_expires_at=now + timedelta(hours=26))
            wallet = get_wallet(account=account, user=provider, for_update=True)
            wallet.balance = 900
            wallet.save(update_fields=['balance'])
            Transaction.objects.create(wallet=wallet, amount=1100, tx_type='ADJUST',
                                       balance_before=0, balance_after=1100, remark='隔离测试初始资金')
            payment = Transaction.objects.create(wallet=wallet, amount=-200, tx_type='PASS_PURCHASE',
                                                 balance_before=1100, balance_after=900, remark='隔离测试购买')
            purchase = ProviderPassPurchase.objects.create(provider=provider, account=account, tier='BRONZE',
                days=1, daily_price=200, original_amount=200, paid_amount=200, transaction=payment,
                starts_at=now + timedelta(hours=2), expires_at=profile.pass_expires_at)
            shop = get_platform_wallet(for_update=True)
            shop.balance = 80
            shop.save(update_fields=['balance'])
            Transaction.objects.create(wallet=shop, amount=200, tx_type='PASS_INCOME',
                balance_before=0, balance_after=200, external_tx_id=payment.tx_no, remark='隔离测试收入')
            Transaction.objects.create(wallet=shop, amount=-120, tx_type='ADJUST',
                balance_before=200, balance_after=80, remark='隔离测试已用资金')
            token = issue_account_tokens(account, auth_method='password_sms')[0]

        barrier, guard, results = threading.Barrier(2), threading.Lock(), []
        def submit(index):
            try:
                with connections[alias].cursor() as cursor:
                    cursor.execute('SET SESSION innodb_lock_wait_timeout = 10')
                client = APIClient(HTTP_HOST=host)
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                barrier.wait(timeout=10)
                response = client.post(f'/api/users/provider-pass/{purchase.pk}/refund/',
                    {'client_request_id': f'qa-refund-{nonce}-{index}'}, format='json')
                result = response.data.get('code')
            except Exception as error:
                result = type(error).__name__
            finally:
                connections[alias].close()
            with guard:
                results.append(result)
        workers = [threading.Thread(target=submit, args=(i,)) for i in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=25)
        if any(worker.is_alive() for worker in workers):
            raise RuntimeError('Concurrent pass refund did not terminate')
        with store_context(store):
            wallet.refresh_from_db(); shop.refresh_from_db()
            rows = ProviderPassRefund.objects.filter(purchase=purchase)
            if sorted(results) != [0, 404] or rows.count() != 1:
                raise RuntimeError(f'Unexpected concurrent result: {results}')
            refund = rows.get()
            if (wallet.balance, shop.balance, refund.amount, refund.funded_amount, refund.shortfall_amount) != (1100, 0, 200, 80, 120):
                raise RuntimeError('Refund and shortfall do not reconcile')
        print(json.dumps({'store': slug, 'isolated_mysql': True, 'concurrent_refund': 'passed',
                          'refund_count': 1, 'shortfall': 'reconciled'}))
    finally:
        with store_context(store), transaction.atomic():
            from club_accounts.models import LegacyAccountMap
            for user in get_user_model().objects.filter(username__in=[name, shop_name]):
                user.is_active = False
                user.save(update_fields=['is_active'])
                mapping = LegacyAccountMap.objects.filter(legacy_user_id=user.pk).select_related('account').first()
                if mapping:
                    mapping.account.is_active = False
                    mapping.account.can_login = False
                    mapping.account.save(update_fields=['is_active', 'can_login', 'updated_at'])
                    Wallet.objects.filter(account=mapping.account).update(is_active=False)
