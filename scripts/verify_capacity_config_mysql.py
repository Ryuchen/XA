"""Exercise store configuration and capacity paths in rollback-only MySQL transactions."""
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
from console.models import AdminAuditLog, AdminMembership, AdminRole
from orders.capacity import current_capacity_rules
from orders.models import Order
from orders.tests.factories import make_service
from stores.context import resolve_store, store_context
from stores.db import transaction
from users.models import EscortProfile

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


def run_store(slug, domain, other_slug):
    store, other = resolve_store(slug), resolve_store(other_slug)
    if store.database_alias != {'shenglang': 'default', 'xinan': 'store_b'}[slug]:
        raise RuntimeError('门店与测试库映射不一致')
    marker = 'capacity_check_' + uuid.uuid4().hex[:12]
    User = get_user_model()
    with store_context(other):
        other_before = current_capacity_rules()
    with store_context(store):
        original_store_rules = current_capacity_rules()
        with transaction.atomic():
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
            role = AdminRole.objects.create(name=marker, code=marker,
                                            permissions=['order:view', 'order:dispatch', 'dictionary:edit'])
            membership = AdminMembership.objects.create(user=staff, account=staff_account)
            membership.roles.add(role)
            def client(account):
                result = APIClient(HTTP_HOST=domain)
                result.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                return result
            staff_client, provider_client = client(staff_account), client(provider_account)
            original = staff_client.get('/api/admin/config/provider-capacity/').data['data']
            result = staff_client.put('/api/admin/config/provider-capacity/', {
                'max_concurrent_orders': 2, 'version': original['version']}, format='json')
            assert result.data['code'] == 0, result.data
            assert AdminAuditLog.objects.filter(resource='provider-capacity', operator_account=staff_account).exists()
            stale = staff_client.put('/api/admin/config/provider-capacity/', {
                'max_concurrent_orders': 4, 'version': original['version']}, format='json')
            assert stale.status_code == 409, stale.data
            with store_context(other):
                assert current_capacity_rules() == other_before
            service = make_service(name=marker, price=1000)
            for _ in range(2):
                Order.objects.create(customer=boss, customer_account=boss_account, provider=provider,
                    provider_account=provider_account, service=service, status='IN_SERVICE',
                    payment_status='PAID', amount=1000)
            stats = provider_client.get('/api/users/provider-stats/').data
            assert (stats['data']['active_orders'], stats['data']['max_concurrent_orders']) == (2, 2), stats
            transaction.set_rollback(True)
        assert not User.objects.filter(username__startswith=marker).exists()
        assert current_capacity_rules() == original_store_rules
    print(f'{slug}: versioned store capacity checks passed; transaction rolled back')


with override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
        CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}},
        LOGIN_OTP_REQUIRED=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False):
    run_store('shenglang', 'sl.esports.club', 'xinan')
    run_store('xinan', 'xa.esports.club', 'shenglang')
