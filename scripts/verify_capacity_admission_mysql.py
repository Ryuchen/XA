"""Race a real admin capacity update against a synthetic provider grab.

Only registered loopback MySQL schemas are allowed. This check temporarily
creates an explicitly marked config row and synthetic identities/orders; it
removes only its exact config row and retires its own marked fixtures.
"""
import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
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
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog, AdminMembership, AdminRole
from orders.capacity import CONFIG_KEY, UNCONFIGURED, lock_capacity_rule_for_admission
from orders.services import count_capacity_orders
from orders.models import Order
from orders.tests.factories import make_service
from stores.context import resolve_store, store_context
from stores.db import transaction
from users.models import EscortProfile
from wallet.models import SystemConfig

EXPECTED = {'default': 'xa_test_fronted_acceptance_20261104', 'store_b': 'xa_test_store_preview_b_20261104'}
if settings.SETTINGS_MODULE != 'config.fronted_acceptance_settings':
    raise RuntimeError('仅允许已登记的隔离开发配置')
for alias, expected in EXPECTED.items():
    connection = connections[alias]
    if (connection.vendor != 'mysql' or connection.settings_dict['HOST'] != '127.0.0.1'
            or str(connection.settings_dict['PORT']) != '13306'):
        raise RuntimeError('数据库连接不在已登记的本机隔离范围')
    with connection.cursor() as cursor:
        cursor.execute('SELECT DATABASE()')
        if cursor.fetchone()[0] != expected:
            raise RuntimeError('数据库名称与已登记测试库不一致')


def run_store(slug, domain):
    store = resolve_store(slug)
    if store.database_alias != {'shenglang': 'default', 'xinan': 'store_b'}[slug]:
        raise RuntimeError('门店与测试库映射不一致')
    marker = 'capacity_race_' + uuid.uuid4().hex[:12]
    User = get_user_model()
    with store_context(store), transaction.atomic():
        if SystemConfig.objects.filter(key=CONFIG_KEY).exists():
            raise RuntimeError(f'{slug} 已有配置，拒绝改写；请在无配置的隔离库验证')
        config = SystemConfig.objects.create(key=CONFIG_KEY, value=UNCONFIGURED, remark=marker)
        def person(suffix, role):
            user = User.objects.create_user(username=marker + suffix, password='temporary-test-only', role=role)
            account = get_or_create_account_for_legacy_user(user)
            if role == 'PROVIDER':
                EscortProfile.objects.create(user=user, account=account, display_name=marker + suffix,
                                             deposit_mode='EARNINGS', is_verified=True)
            return user, account
        boss, boss_account = person('_boss', 'CUSTOMER')
        provider, provider_account = person('_provider', 'PROVIDER')
        staff, staff_account = person('_staff', 'OPERATOR')
        role = AdminRole.objects.create(name=marker, code=marker, permissions=['order:view', 'dictionary:edit'])
        membership = AdminMembership.objects.create(user=staff, account=staff_account)
        membership.roles.add(role)
        service = make_service(name=marker, price=1000, commission_rate=20)
        active = Order.objects.create(customer=boss, customer_account=boss_account, provider=provider,
            provider_account=provider_account, service=service, status='IN_SERVICE', payment_status='PAID', amount=1000)
        waiting = Order.objects.create(customer=boss, customer_account=boss_account, service=service,
            status='PENDING', payment_status='PAID', amount=1000, provider_income=800, shop_income=200)
        Order.objects.filter(pk=waiting.pk).update(created_at=timezone.now() - timedelta(hours=1))
        staff_token = issue_account_tokens(staff_account)[0]
        provider_token = issue_account_tokens(provider_account)[0]

    editor_locked, grab_attempted, grab_done, release_editor = (threading.Event() for _ in range(4))
    real_audit = AdminAuditLog.objects.create
    def hold_editor(*args, **kwargs):
        editor_locked.set()
        if not release_editor.wait(10):
            raise AssertionError('editor did not resume')
        return real_audit(*args, **kwargs)
    def observe_grab_lock():
        grab_attempted.set()
        return lock_capacity_rule_for_admission()
    def request(token, path, data, method):
        close_old_connections()
        try:
            with store_context(store):
                if method == 'post':
                    # Exercise the stricter isolation mode as well: an older
                    # snapshot may already exist before the locking read.
                    from stores.db import connection
                    with connection.cursor() as cursor:
                        cursor.execute('SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                client = APIClient(HTTP_HOST=domain)
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                return getattr(client, method)(path, data, format='json').data
        finally:
            connections.close_all()
            if method == 'post':
                grab_done.set()
    try:
        with patch('console.capacity_config.AdminAuditLog.objects.create', side_effect=hold_editor), \
             patch('orders.capacity.lock_capacity_rule_for_admission', side_effect=observe_grab_lock), \
             ThreadPoolExecutor(max_workers=2) as workers:
            editor = workers.submit(request, staff_token, '/api/admin/config/provider-capacity/',
                                    {'max_concurrent_orders': 1, 'version': 0}, 'put')
            try:
                if not editor_locked.wait(10):
                    raise AssertionError('configuration update did not acquire its lock')
                grab = workers.submit(request, provider_token, f'/api/orders/orders/{waiting.pk}/grab/', {}, 'post')
                if not grab_attempted.wait(10):
                    if grab_done.is_set():
                        raise AssertionError(f'grab stopped before its lock: {grab.result()}')
                    raise AssertionError('grab did not attempt its capacity lock')
                if grab_done.wait(1):
                    raise AssertionError(f'grab did not wait for configuration: {grab.result()}')
            finally:
                release_editor.set()
            update_result, grab_result = editor.result(timeout=10), grab.result(timeout=10)
        assert update_result['code'] == 0, update_result
        assert grab_result['code'] != 0 and '1 单' in grab_result['msg'], grab_result
        with store_context(store):
            waiting.refresh_from_db()
            assert waiting.status == 'PENDING' and waiting.provider_account_id is None

            # Restore headroom for the inverse race: the grab acquires its
            # shared rule lock before the admin tries lowering the limit.
            client = APIClient(HTTP_HOST=domain)
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
            raised = client.put('/api/admin/config/provider-capacity/', {
                'max_concurrent_orders': 3, 'version': 1}, format='json').data
            assert raised['code'] == 0, raised

        grab_locked, editor_attempted, editor_done, release_grab = (threading.Event() for _ in range(4))
        real_select_for_update = SystemConfig.objects.select_for_update
        def hold_grab(*args, **kwargs):
            result = count_capacity_orders(*args, **kwargs)
            if transaction.get_connection().in_atomic_block:
                grab_locked.set()
                if not release_grab.wait(10):
                    raise AssertionError('grab did not resume')
            return result
        def observe_editor(*args, **kwargs):
            editor_attempted.set()
            return real_select_for_update(*args, **kwargs)
        def update_request():
            try:
                return request(staff_token, '/api/admin/config/provider-capacity/',
                               {'max_concurrent_orders': 1, 'version': 2}, 'put')
            finally:
                editor_done.set()
        with patch('orders.views.count_capacity_orders', side_effect=hold_grab), \
             patch('console.capacity_config.SystemConfig.objects.select_for_update', side_effect=observe_editor), \
             ThreadPoolExecutor(max_workers=2) as workers:
            grab = workers.submit(request, provider_token, f'/api/orders/orders/{waiting.pk}/grab/', {}, 'post')
            try:
                if not grab_locked.wait(10):
                    raise AssertionError('grab did not acquire its rule lock')
                editor = workers.submit(update_request)
                if not editor_attempted.wait(10):
                    raise AssertionError('editor did not attempt the rule lock')
                if editor_done.wait(1):
                    raise AssertionError(f'configuration update did not wait: {editor.result()}')
            finally:
                release_grab.set()
            grab_result, update_result = grab.result(timeout=10), editor.result(timeout=10)
        assert grab_result['code'] == 0, grab_result
        assert update_result['code'] == 0, update_result
        with store_context(store):
            waiting.refresh_from_db()
            assert waiting.status == 'GRABBED' and waiting.provider_account_id == provider_account.pk
    finally:
        with store_context(store), transaction.atomic():
            row = SystemConfig.objects.select_for_update().get(pk=config.pk, key=CONFIG_KEY)
            if row.remark != marker or row.value not in (UNCONFIGURED,
                    '{"max_concurrent_orders":1,"version":1}', '{"max_concurrent_orders":3,"version":2}',
                    '{"max_concurrent_orders":1,"version":3}'):
                raise RuntimeError('测试配置已被外部修改，保留记录供核对')
            row.delete()
            # These rows were created with this unique marker in this script.
            Order.objects.filter(pk__in=[active.pk, waiting.pk], customer_account=boss_account).update(
                status='CANCELLED', payment_status='UNPAID')
            ClubAccount.objects.filter(pk__in=[boss_account.pk, provider_account.pk, staff_account.pk],
                username__startswith=marker).update(is_active=False, can_login=False)
            User.objects.filter(pk__in=[boss.pk, provider.pk, staff.pk], username__startswith=marker).update(is_active=False)
            role.is_active = False
            role.save(update_fields=['is_active'])
            service.is_active = False
            service.save(update_fields=['is_active'])
        with store_context(store):
            assert not SystemConfig.objects.filter(pk=config.pk).exists()
    print(f'{slug}: real config update serialized against grab; synthetic fixtures retired')


with override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
        CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}},
        LOGIN_OTP_REQUIRED=False, PROVIDER_WECHAT_GATE_ENABLED=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False):
    run_store('shenglang', 'sl.esports.club')
    run_store('xinan', 'xa.esports.club')
