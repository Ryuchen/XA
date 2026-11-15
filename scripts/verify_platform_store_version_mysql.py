"""Verify platform store edit version checks in the registered isolated directory."""
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services' / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.fronted_acceptance_settings')
import django
django.setup()
from django.conf import settings
from django.db import connections, transaction
from django.test import override_settings
from rest_framework.test import APIClient
from stores.models import PlatformAccount, PlatformRoleGrant, Store
from stores.platform_auth import issue_tokens

db = connections['default']
if (settings.SETTINGS_MODULE != 'config.fronted_acceptance_settings' or db.vendor != 'mysql'
        or db.settings_dict['HOST'] != '127.0.0.1' or str(db.settings_dict['PORT']) != '13306'):
    raise RuntimeError('仅允许已登记的本机隔离测试目录')
with db.cursor() as cursor:
    cursor.execute('SELECT DATABASE()')
    if cursor.fetchone()[0] != 'xa_test_fronted_acceptance_20261104':
        raise RuntimeError('数据库名称与已登记测试库不一致')

marker = uuid.uuid4().hex
with override_settings(PLATFORM_IDENTITY_ENABLED=True, ALLOWED_HOSTS=['*']):
    with transaction.atomic(using='default'):
        account = PlatformAccount(username='store-version-' + marker[:16], phone='13800001111', is_phone_verified=True)
        account.set_password('Synthetic-Platform-Password-2026!'); account.save()
        PlatformRoleGrant.objects.create(account=account, role='SUPER_ADMIN', scope_slug='*')
        token, _ = issue_tokens(account, 'SMS', account.phone)
        store = Store.objects.create(slug='version-' + marker[:18], name='隔离测试店', database_alias='fixture_' + marker)
        client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        url = f'/api/admin/platform/store-management/{store.pk}'
        version = store.updated_at.isoformat()
        first = client.patch(url, {'name': '隔离测试已改', 'expected_updated_at': version}, format='json')
        assert first.status_code == 200, first.content
        second = client.patch(url, {'name': '不得覆盖', 'expected_updated_at': version}, format='json')
        assert second.status_code == 409, second.content
        store.refresh_from_db(); assert store.name == '隔离测试已改'
        transaction.set_rollback(True, using='default')
assert not Store.objects.filter(slug='version-' + marker[:18]).exists()
assert not PlatformAccount.objects.filter(username='store-version-' + marker[:16]).exists()
print('platform store version check passed in isolated MySQL; all fixtures rolled back')
