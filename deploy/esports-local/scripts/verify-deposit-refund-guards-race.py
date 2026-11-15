"""Race real order/aftersale paths against refunds on synthetic isolated accounts."""
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import uuid
from unittest.mock import patch

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
from console.models import AdminMembership, AdminRole  # noqa: E402
import console.deposit_refund as refund_module  # noqa: E402
from orders.models import Order, ServiceItem  # noqa: E402
from orders.services import ensure_provider_dispatch_locks  # noqa: E402
from orders.state_machine import transition  # noqa: E402
from stores.context import resolve_store, store_context  # noqa: E402
from stores.db import transaction  # noqa: E402
from users.models import EscortProfile  # noqa: E402
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
names = [f'qa-dr-{role}-{nonce}' for role in ('player', 'staff', 'boss')]
shop_name = 'qa-dr-system-' + nonce

with override_settings(PLATFORM_SYSTEM_USERNAME=shop_name,
                       CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}):
    try:
        with store_context(store), transaction.atomic():
            User = get_user_model()
            player = User.objects.create_user(username=names[0], password=secrets.token_urlsafe(24), role='PROVIDER')
            staff = User.objects.create_user(username=names[1], password=secrets.token_urlsafe(24), role='OPERATOR')
            boss = User.objects.create_user(username=names[2], password=secrets.token_urlsafe(24), role='CUSTOMER')
            player_account, staff_account, boss_account = [get_or_create_account_for_legacy_user(u) for u in (player, staff, boss)]
            for account in (staff_account, boss_account):
                account.is_phone_verified = True; account.save(update_fields=['is_phone_verified'])
            membership = AdminMembership.objects.create(user=staff, account=staff_account)
            role = AdminRole.objects.create(name='隔离退押金测试-' + nonce, code='qa-dr-' + nonce,
                                            permissions=['escort:view', 'escort:deposit_refund'])
            membership.roles.add(role)
            profile = EscortProfile.objects.create(user=player, account=player_account, display_name='隔离退押金', deposit_paid=40000)
            wallet = get_wallet(account=player_account, user=player, for_update=True)
            platform = get_platform_wallet(for_update=True)
            platform.balance = 40000; platform.save(update_fields=['balance'])
            Transaction.objects.create(wallet=platform, tx_type='DEPOSIT_INCOME', amount=40000,
                balance_before=0, balance_after=40000, remark='隔离测试押金池')
            service = ServiceItem.objects.create(name='隔离退押金服务-' + nonce, price=10000)
            pending = Order.objects.create(customer=boss, customer_account=boss_account, provider=player,
                provider_account=player_account, service=service, amount=10000, status='PENDING', payment_status='PAID')
            completed = Order.objects.create(customer=boss, customer_account=boss_account, provider=player,
                provider_account=player_account, service=service, amount=10000, status='COMPLETED',
                payment_status='PAID', completed_at=timezone.now(), settled_at=timezone.now())
            staff_token = issue_account_tokens(staff_account, auth_method='password_sms')[0]
            boss_token = issue_account_tokens(boss_account, auth_method='password_sms')[0]
        with store_context(store): ensure_provider_dispatch_locks([player_account.pk])

        def client(site, token):
            c = APIClient(HTTP_HOST=site); c.credentials(HTTP_AUTHORIZATION='Bearer ' + token); return c
        def refund_request(key):
            return client('admin.esports.club', staff_token).post(f'/api/admin/escorts/{profile.pk}/deposit-refund/',
                {'amount':1000, 'reason':'隔离并发测试', 'client_request_id':key}, format='json').data
        def check_unchanged():
            with store_context(store):
                profile.refresh_from_db(); wallet.refresh_from_db(); platform.refresh_from_db()
                if (profile.deposit_paid, wallet.balance, platform.balance) != (40000, 0, 40000):
                    raise RuntimeError('Rejected refund changed synthetic money')

        def run_race(kind):
            held, entered, release, results = threading.Event(), threading.Event(), threading.Event(), {}
            original_lock = refund_module.lock_provider_dispatch
            def observed_lock(ids):
                if threading.current_thread().name == 'refund': entered.set()
                return original_lock(ids)
            def first():
                try:
                    with store_context(store), transaction.atomic():
                        if kind == 'order':
                            transition(pending.pk, 'GRABBED', dispatch_account_ids=[player_account.pk])
                            results['first'] = 0
                        else:
                            response = client(host, boss_token).post(f'/api/orders/orders/{completed.pk}/aftersale/',
                                {'reason':'隔离售后申请', 'client_request_id':'qa-aftersale-' + nonce}, format='json')
                            results['first'] = response.data.get('code')
                        held.set()
                        if not release.wait(15): raise RuntimeError('Timeout holding competing operation')
                except Exception as error: results['first'] = type(error).__name__; held.set()
                finally: connections[alias].close()
            def second():
                try:
                    with connections[alias].cursor() as cursor: cursor.execute('SET SESSION innodb_lock_wait_timeout = 10')
                    response = refund_request('qa-refund-' + kind + nonce)
                    results['refund'] = response.get('code')
                    results['data'] = response.get('data')
                except Exception as error: results['refund'] = type(error).__name__
                finally: connections[alias].close()
            with patch.object(refund_module, 'lock_provider_dispatch', side_effect=observed_lock):
                left, right = threading.Thread(target=first), threading.Thread(target=second, name='refund')
                left.start()
                if not held.wait(10) or results.get('first') != 0: raise RuntimeError(f'Competing {kind} failed: {results}')
                right.start()
                if not entered.wait(10): raise RuntimeError(f'Refund did not enter {kind} race')
                release.set()
                left.join(timeout=20); right.join(timeout=20)
                if left.is_alive() or right.is_alive(): raise RuntimeError(f'{kind} race did not finish')
            expected = 'active_order_count' if kind == 'order' else 'open_aftersale_count'
            if results.get('refund') != 409 or not (results.get('data') or {}).get(expected):
                raise RuntimeError(f'Unexpected {kind} result: {results}')
            check_unchanged()

        run_race('order')
        with store_context(store): Order.objects.filter(pk=pending.pk).update(status='CANCELLED', payment_status='REFUNDED')
        run_race('aftersale')
        print(json.dumps({'store':slug, 'isolated_mysql':True, 'order_vs_refund':'passed',
                          'aftersale_vs_refund':'passed', 'wallet':'unchanged'}))
    finally:
        with store_context(store), transaction.atomic():
            from club_accounts.models import LegacyAccountMap
            for user in get_user_model().objects.filter(username__in=names + [shop_name]):
                user.is_active = False; user.save(update_fields=['is_active'])
                mapping = LegacyAccountMap.objects.filter(legacy_user_id=user.pk).select_related('account').first()
                if mapping:
                    mapping.account.is_active = False; mapping.account.can_login = False
                    mapping.account.save(update_fields=['is_active','can_login','updated_at'])
                    Wallet.objects.filter(account=mapping.account).update(is_active=False)
