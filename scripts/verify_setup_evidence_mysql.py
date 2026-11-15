"""Verify the read-only setup evidence in both registered isolated MySQL stores."""
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services' / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.fronted_acceptance_settings')
import django
django.setup()
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connections
from django.test import override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership, AdminRole
from orders.capacity import CONFIG_KEY, UNCONFIGURED
from stores.context import resolve_store, store_context
from stores.db import transaction
from wallet.models import SystemConfig

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


def verify(slug, alias, value, expected):
    store = resolve_store(slug)
    if store.database_alias != alias:
        raise RuntimeError('门店与测试库映射不一致')
    marker = 'setup_evidence_' + uuid.uuid4().hex[:12]
    User = get_user_model()
    with store_context(store):
        before = SystemConfig.objects.filter(key=CONFIG_KEY).values_list('value', flat=True).first()
        existed = SystemConfig.objects.filter(key=CONFIG_KEY).exists()
        percent_key = 'provider_deposit_income_percent'
        old_percent = SystemConfig.objects.filter(key=percent_key).values_list('value', flat=True).first()
        percent_existed = SystemConfig.objects.filter(key=percent_key).exists()
        with transaction.atomic():
            SystemConfig.objects.update_or_create(key=CONFIG_KEY, defaults={'value': value})
            SystemConfig.objects.update_or_create(key=percent_key, defaults={'value': '0'})
            user = User.objects.create_user(username=marker, password='temporary-test-only', role='OPERATOR')
            account = get_or_create_account_for_legacy_user(user)
            role = AdminRole.objects.create(name=marker, code=marker,
                                             permissions=['dictionary:view', 'deposit_rule:view', 'deposit_rule:edit'])
            membership = AdminMembership.objects.create(user=user, account=account)
            membership.roles.add(role)
            client = APIClient(HTTP_HOST='admin.esports.club')
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            response = client.get('/api/admin/dictionaries/')
            assert response.status_code == 200, response.data
            rows = {row['key']: row for row in response.data['data']['setup_evidence']}
            assert rows['capacity']['status'] == expected, rows
            assert rows['withdraw']['status'] == 'RESTRICTED', rows
            url = '/api/admin/dictionaries/setup-confirmation/deposit_percent/'
            preview = client.get(url).data['data']
            row = SystemConfig.objects.get(key=percent_key)
            row.value = '1'; row.save()
            assert client.post(url, {'fingerprint': preview['fingerprint']}, format='json').status_code == 409
            preview = client.get(url).data['data']
            assert client.post(url, {'fingerprint': preview['fingerprint']}, format='json').status_code == 200
            rows = {item['key']: item for item in client.get('/api/admin/dictionaries/').data['data']['setup_evidence']}
            assert rows['deposit_percent']['status'] == 'CONFIRMED', rows
            transaction.set_rollback(True)
        assert not User.objects.filter(username=marker).exists()
        assert SystemConfig.objects.filter(key=CONFIG_KEY).exists() == existed
        assert SystemConfig.objects.filter(key=CONFIG_KEY).values_list('value', flat=True).first() == before
        assert SystemConfig.objects.filter(key=percent_key).exists() == percent_existed
        assert SystemConfig.objects.filter(key=percent_key).values_list('value', flat=True).first() == old_percent
    print(f'{slug}: real dictionary API verified; isolated transaction rolled back')


with override_settings(LEGACY_FORCE_AUTH_COMPAT=False, LOGIN_OTP_REQUIRED=False,
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
        CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}):
    verify('shenglang', 'default', UNCONFIGURED, 'MISSING')
    verify('xinan', 'store_b', '{"max_concurrent_orders":4,"version":1}', 'SAVED')
