"""Race game-rule edits with synthetic paid orders on registered local test DBs.

Fixtures are uniquely marked and isolated. The test removes only its own
game profile after verifying it, and retires its synthetic accounts/catalog;
the synthetic paid order, wallet and audit trail remain for inspection.
"""
import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services' / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.fronted_acceptance_settings')
import django
django.setup()
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connections
from django.test import override_settings
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog, AdminMembership, AdminRole
from orders.models import GameCategory, GameCommissionProfile, Order
from orders.tests.factories import make_service
from stores.context import resolve_store, store_context
from stores.db import transaction
from wallet.models import Transaction
from wallet.services import get_wallet

EXPECTED = {'default': 'xa_test_fronted_acceptance_20261104', 'store_b': 'xa_test_store_preview_b_20261104'}
if settings.SETTINGS_MODULE != 'config.fronted_acceptance_settings':
    raise RuntimeError('仅允许已登记的隔离开发配置')
for alias, expected in EXPECTED.items():
    db = connections[alias]
    if db.vendor != 'mysql' or db.settings_dict['HOST'] != '127.0.0.1' or str(db.settings_dict['PORT']) != '13306':
        raise RuntimeError('数据库连接不在已登记的本机隔离范围')
    with db.cursor() as cursor:
        cursor.execute('SELECT DATABASE()')
        if cursor.fetchone()[0] != expected:
            raise RuntimeError('数据库名称与已登记测试库不一致')


def run_store(slug, domain):
    store = resolve_store(slug)
    if store.database_alias != {'shenglang': 'default', 'xinan': 'store_b'}[slug]:
        raise RuntimeError('门店与测试库映射不一致')
    marker = 'game_race_' + uuid.uuid4().hex[:12]
    User = get_user_model()
    with store_context(store), transaction.atomic():
        if GameCommissionProfile.objects.filter(game='VALORANT').exists():
            raise RuntimeError(f'{slug} 已有游戏配置，拒绝改写')
        boss = User.objects.create_user(username=marker + '_boss', password='temporary-test-only', role='CUSTOMER')
        staff = User.objects.create_user(username=marker + '_staff', password='temporary-test-only', role='OPERATOR')
        boss_account = get_or_create_account_for_legacy_user(boss)
        staff_account = get_or_create_account_for_legacy_user(staff)
        role = AdminRole.objects.create(name=marker, code=marker, permissions=['dictionary:edit'])
        membership = AdminMembership.objects.create(user=staff, account=staff_account)
        membership.roles.add(role)
        game = GameCategory.objects.create(name=marker)
        profile = GameCommissionProfile.objects.create(game_category=game, game='VALORANT',
            rules={'companion': 20, 'grind': 30})
        service = make_service(name=marker, price=1000, game_category=game, provider_kind='RANK')
        wallet = get_wallet(user=boss, account=boss_account)
        wallet.balance = 10000; wallet.save(update_fields=['balance'])
        staff_token = issue_account_tokens(staff_account)[0]
        boss_token = issue_account_tokens(boss_account)[0]
    config_url = f'/api/admin/config/game-commissions/{game.pk}/'

    def request(token, path, data, method):
        close_old_connections()
        try:
            with store_context(store):
                if method == 'post':
                    from stores.db import connection
                    with connection.cursor() as cursor:
                        cursor.execute('SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                client = APIClient(HTTP_HOST=domain)
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                response = getattr(client, method)(path, data, format='json')
                return response.status_code, response.data
        finally:
            connections.close_all()

    try:
        editor_locked, payment_attempted, release_editor = (threading.Event() for _ in range(3))
        real_audit = AdminAuditLog.objects.create
        from orders.game_commission import lock_and_verify_payment_snapshot
        def hold_editor(*args, **kwargs):
            editor_locked.set()
            if not release_editor.wait(10): raise AssertionError('editor did not resume')
            return real_audit(*args, **kwargs)
        def observe_payment(*args, **kwargs):
            payment_attempted.set()
            return lock_and_verify_payment_snapshot(*args, **kwargs)
        with patch('console.game_commission.AdminAuditLog.objects.create', side_effect=hold_editor), \
             patch('orders.game_commission.lock_and_verify_payment_snapshot', side_effect=observe_payment), \
             ThreadPoolExecutor(max_workers=2) as pool:
            editor = pool.submit(request, staff_token, config_url,
                {'game': 'VALORANT', 'version': 1, 'rules': {'companion': 20, 'grind': 40}}, 'put')
            try:
                if not editor_locked.wait(10): raise AssertionError('editor did not lock')
                payment = pool.submit(request, boss_token, '/api/orders/create/',
                    {'service_id': service.pk, 'client_request_id': marker + '_stale'}, 'post')
                if not payment_attempted.wait(10): raise AssertionError(f'payment did not reach lock: {payment.result(timeout=1)}')
                if wait([payment], timeout=1).done: raise AssertionError(f'payment did not wait: {payment.result()}')
            finally:
                release_editor.set()
            assert editor.result(timeout=10)[1]['code'] == 0
            code, result = payment.result(timeout=10)
            assert code == 409 and result['errorCode'] == 'COMMISSION_CHANGED', result
        with store_context(store):
            wallet.refresh_from_db(); assert wallet.balance == 10000
            assert not Order.objects.filter(customer_account=boss_account).exists()
            assert not Transaction.objects.filter(wallet=wallet).exists()

        payment_locked, editor_entered, release_payment = (threading.Event() for _ in range(3))
        from orders import views as order_views
        from console import game_commission as editor_views
        real_wallet = order_views.get_wallet
        real_validate = editor_views.validate_rules
        def hold_payment(*args, **kwargs):
            result = real_wallet(*args, **kwargs)
            if kwargs.get('for_update'):
                payment_locked.set()
                if not release_payment.wait(10): raise AssertionError('payment did not resume')
            return result
        def observe_editor(*args, **kwargs):
            editor_entered.set()
            return real_validate(*args, **kwargs)
        with patch('orders.views.get_wallet', side_effect=hold_payment), \
             patch('console.game_commission.validate_rules', side_effect=observe_editor), \
             ThreadPoolExecutor(max_workers=2) as pool:
            payment = pool.submit(request, boss_token, '/api/orders/create/',
                {'service_id': service.pk, 'client_request_id': marker + '_paid'}, 'post')
            try:
                if not payment_locked.wait(10): raise AssertionError(f'payment did not lock: {payment.result(timeout=1)}')
                editor = pool.submit(request, staff_token, config_url,
                    {'game': 'VALORANT', 'version': 2, 'rules': {'companion': 20, 'grind': 50}}, 'put')
                if not editor_entered.wait(10): raise AssertionError('editor did not start')
                if wait([editor], timeout=1).done: raise AssertionError(f'editor did not wait: {editor.result()}')
            finally:
                release_payment.set()
            code, result = payment.result(timeout=10)
            assert code == 200 and result['code'] == 0, result
            assert editor.result(timeout=10)[1]['code'] == 0
        with store_context(store):
            order = Order.objects.get(customer_account=boss_account)
            assert (order.game_commission_snapshot['rate'], order.game_commission_snapshot['version']) == (40, 2)
            # Existing test-store promotions may change the synthetic order's
            # price. Check the paid amount actually used, not an assumed 1000.
            assert order.amount > 0 and order.provider_income == order.amount - order.amount * 40 // 100
            wallet.refresh_from_db(); assert wallet.balance == 10000 - order.amount
            assert Transaction.objects.filter(wallet=wallet, order=order, tx_type='PAY', amount=-order.amount).count() == 1
    finally:
        with store_context(store), transaction.atomic():
            locked_game = GameCategory.objects.select_for_update().get(pk=game.pk)
            current = GameCommissionProfile.objects.select_for_update().filter(pk=profile.pk, game_category=game).first()
            if current and (locked_game.name != marker or current.game != 'VALORANT' or current.version not in (1, 2, 3)
                    or current.rules not in ({'companion': 20, 'grind': 30}, {'companion': 20, 'grind': 40}, {'companion': 20, 'grind': 50})):
                raise RuntimeError('测试配置发生非预期变化，保留供核对')
            if current: current.delete()
            ClubAccount.objects.filter(pk__in=[boss_account.pk, staff_account.pk], username__startswith=marker).update(is_active=False, can_login=False)
            User.objects.filter(pk__in=[boss.pk, staff.pk], username__startswith=marker).update(is_active=False)
            AdminRole.objects.filter(pk=role.pk, code=marker).update(is_active=False)
            type(service).objects.filter(pk=service.pk, name=marker).update(is_active=False)
            GameCategory.objects.filter(pk=game.pk, name=marker).update(is_active=False)
    print(f'{slug}: game config and payment serialize; synthetic fixtures retired')


with override_settings(GAME_COMMISSION_PAYMENT_ENABLED=True,
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
        CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}},
        LOGIN_OTP_REQUIRED=False, PROVIDER_WECHAT_GATE_ENABLED=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False), \
        patch('orders.views._schedule_auto_cancel'), patch('orders.views.enqueue_kook_dispatch'), \
        patch('orders.views.enqueue_wecom_dispatch'), patch('orders.views.notify_order_update'):
    run_store('shenglang', 'sl.esports.club')
    run_store('xinan', 'xa.esports.club')
