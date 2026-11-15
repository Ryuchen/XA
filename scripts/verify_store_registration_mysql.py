"""Race two synthetic directory registrations for the same physical DB name.

No target schema is connected to or created. Only the exact synthetic store
record is deleted after validation; synthetic operator and audit are retained.
"""
import copy
import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services' / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.fronted_acceptance_settings')
import django
django.setup()
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connections, transaction
from django.test import override_settings
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership, AdminRole
from stores.context import resolve_store, store_context
from stores.models import Store, StorePlatformGrant
from stores.serializers import StoreCreateSerializer

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

marker = uuid.uuid4().hex[:12]
store = resolve_store('shenglang')
if store.database_alias != 'default': raise RuntimeError('平台门店映射不一致')
slug_a, slug_b = f'qa-a-{marker}', f'qa-b-{marker}'
alias_a, alias_b = f'qa_a_{marker}', f'qa_b_{marker}'
database_name = f'qa_uncreated_{marker}'
fake_databases = copy.deepcopy(settings.DATABASES)
for alias in (alias_a, alias_b):
    fake_databases[alias] = {**copy.deepcopy(settings.DATABASES['default']), 'NAME': database_name}
fake_settings = SimpleNamespace(DATABASES=fake_databases,
    STORE_DATABASE_ALIASES=tuple(settings.STORE_DATABASE_ALIASES) + (alias_a, alias_b))
User = get_user_model()
with override_settings(LOGIN_OTP_REQUIRED=False), store_context(store), transaction.atomic(using='default'):
    role = AdminRole.objects.filter(code='super_admin', is_active=True).first()
    if role is None: raise RuntimeError('隔离库无可用测试角色，拒绝创建共享角色')
    username = 'registration_guard_' + marker
    user = User.objects.create_user(username=username, password='temporary-test-only', role='ADMIN')
    account = get_or_create_account_for_legacy_user(user)
    membership = AdminMembership.objects.create(user=user, account=account); membership.roles.add(role)
    grant = StorePlatformGrant.objects.create(account_id=account.pk)
    token = issue_account_tokens(account)[0]
    prefixes = []
    for lead in ('Q', 'Z'):
        candidate = lead + marker[:3].upper()
        if Store.objects.filter(entity_prefix=candidate).exists(): raise RuntimeError('测试前缀冲突')
        prefixes.append(candidate)

first_inserted, second_attempt, release_first = (threading.Event() for _ in range(3))
thread_data = threading.local()
real_create = StoreCreateSerializer.create
from stores.database_identity import lock_store_registration


def hold_first(serializer, data):
    result = real_create(serializer, data)
    if result.slug == slug_a:
        first_inserted.set()
        if not release_first.wait(10): raise AssertionError('first registration did not resume')
    return result


def observe_lock():
    if getattr(thread_data, 'second', False): second_attempt.set()
    return lock_store_registration()


def request_second_or_first(second):
    close_old_connections()
    thread_data.second = second
    try:
        with store_context(store):
            client = APIClient(HTTP_HOST='sl.esports.club', HTTP_X_STORE_SLUG=store.slug)
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
            payload = {'slug': slug_b if second else slug_a, 'name': f'隔离登记 {marker}',
                       'database_alias': alias_b if second else alias_a,
                       'entity_prefix': prefixes[1 if second else 0]}
            if not second:
                response = client.post('/api/stores/manage/', payload, format='json')
            else:
                with connections['default'].cursor() as cursor:
                    cursor.execute('SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                with transaction.atomic(using='default'):
                    # The directory query establishes a snapshot before the
                    # other registration commits; the guard must use a current read.
                    Store.objects.count()
                    response = client.post('/api/stores/manage/', payload, format='json')
            return response.status_code, response.data
    finally:
        connections.close_all()


try:
    with override_settings(LOGIN_OTP_REQUIRED=False, LEGACY_FORCE_AUTH_COMPAT=False,
            CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
            CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}), \
            patch('stores.database_identity.settings', fake_settings), \
            patch('stores.serializers.settings', fake_settings), \
            patch('stores.database_identity.lock_store_registration', side_effect=observe_lock), \
            patch('stores.serializers.StoreCreateSerializer.create', new=hold_first), \
            ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(request_second_or_first, False)
        try:
            if not first_inserted.wait(10): raise AssertionError(f'first did not insert: {first.result(timeout=1)}')
            second = pool.submit(request_second_or_first, True)
            if not second_attempt.wait(10): raise AssertionError(f'second did not lock: {second.result(timeout=1)}')
            if wait([second], timeout=1).done: raise AssertionError(f'second did not wait: {second.result()}')
        finally:
            release_first.set()
        status_a, data_a = first.result(timeout=10)
        status_b, data_b = second.result(timeout=10)
        assert status_a == 201 and data_a['code'] == 0, data_a
        assert status_b == 400, data_b
        assert Store.objects.filter(slug=slug_a, database_alias=alias_a).count() == 1
        assert not Store.objects.filter(slug=slug_b).exists()
finally:
    with transaction.atomic(using='default'):
        synthetic = Store.objects.filter(slug=slug_a, database_alias=alias_a)
        if synthetic.exists():
            row = synthetic.select_for_update().get()
            if row.status != Store.Status.PROVISIONING or row.initialized_at is not None or row.name != f'隔离登记 {marker}':
                raise RuntimeError('测试门店已发生非预期变化，保留供核对')
            row.delete()
        StorePlatformGrant.objects.filter(pk=grant.pk, account_id=account.pk).update(is_active=False)
        ClubAccount.objects.filter(pk=account.pk, username=username).update(is_active=False, can_login=False)
        User.objects.filter(pk=user.pk, username=username).update(is_active=False)

print('concurrent registrations: one synthetic store accepted, duplicate physical name rejected; no schema created')
