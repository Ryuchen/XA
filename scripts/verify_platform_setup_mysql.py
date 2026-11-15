"""Real platform setup APIs against the two registered isolated MySQL stores."""
from contextlib import ExitStack
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
from console.models import AdminAuditLog
from orders.capacity import CONFIG_KEY
from stores.context import resolve_store, store_context
from stores.models import PlatformAccount, PlatformAuditEvent, PlatformRoleGrant
from stores.platform_auth import issue_tokens
from wallet.models import SystemConfig, MIN_WITHDRAW_AMOUNT_KEY, WITHDRAW_TAX_RATE_KEY
from wallet.settlement_clocks import CONFIG_KEY as CLOCK_KEY
from wallet.club_rules import DEPOSIT_TARGET_KEY, DEPOSIT_PERCENT_KEY
from wallet.withdraw_rules import BALANCE_THRESHOLD_KEY, LOCK_KEY

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


def verify(slug, alias):
    store = resolve_store(slug)
    if store.database_alias != alias:
        raise RuntimeError('门店与测试库映射不一致')
    marker = 'platform-setup-' + uuid.uuid4().hex[:14]
    with store_context(store):
        config_keys = [CONFIG_KEY, CLOCK_KEY, DEPOSIT_TARGET_KEY, DEPOSIT_PERCENT_KEY, MIN_WITHDRAW_AMOUNT_KEY, WITHDRAW_TAX_RATE_KEY, BALANCE_THRESHOLD_KEY, LOCK_KEY]
        before = list(SystemConfig.objects.filter(key__in=config_keys).order_by('key').values_list('key', 'id', 'value', 'updated_at'))
    with ExitStack() as stack:
        stack.enter_context(transaction.atomic(using='default'))
        if alias != 'default':
            stack.enter_context(transaction.atomic(using=alias))
        account = PlatformAccount(username=marker, phone='13800001111', is_phone_verified=True)
        account.set_password('Synthetic-Platform-Password-2026!'); account.save()
        PlatformRoleGrant.objects.create(account=account, role='SUPER_ADMIN', scope_slug='*')
        token, _ = issue_tokens(account, 'SMS', account.phone)
        client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        url = f'/api/admin/platform/store-management/{store.pk}/setup'
        view = client.get(url)
        assert view.status_code == 200, view.content
        capacity = view.data['data']['capacity']
        assert capacity is not None
        change = client.put(url + '/capacity', {'max_concurrent_orders': 4, 'version': capacity['version']}, format='json')
        assert change.status_code == 200, change.content
        stale = client.put(url + '/capacity', {'max_concurrent_orders': 5, 'version': capacity['version']}, format='json')
        assert stale.status_code == 409, stale.content
        rows = {row['key']: row for row in client.get(url).data['data']['evidence']}
        confirmed = client.post(url + '/confirm/capacity', {'fingerprint': rows['capacity']['snapshot']['fingerprint']}, format='json')
        assert confirmed.status_code == 200, confirmed.content
        clocks = client.get(url).data['data']['clocks']
        assert clocks is not None
        clock_change = {'aftersale_hours': 48, 'income_freeze_hours': 36, 'expected_version': clocks['version']}
        assert client.put(url + '/clocks', clock_change, format='json').status_code == 200
        assert client.put(url + '/clocks', clock_change, format='json').status_code == 409
        clock_row = {row['key']: row for row in client.get(url).data['data']['evidence']}['settlement_clocks']
        assert client.post(url + '/confirm/settlement_clocks', {'fingerprint': clock_row['snapshot']['fingerprint']}, format='json').status_code == 200
        deposit = client.get(url).data['data']['deposit']
        assert deposit is not None
        target_change = {'cash_target': 50000, 'earnings_target': 90000, 'expected_version': deposit['targets']['version']}
        assert client.put(url + '/deposit/targets', target_change, format='json').status_code == 200
        assert client.put(url + '/deposit/targets', target_change, format='json').status_code == 409
        percent_change = {'deposit_percent': 55, 'fingerprint': deposit['percent_fingerprint']}
        assert client.put(url + '/deposit/percent', percent_change, format='json').status_code == 200
        assert client.put(url + '/deposit/percent', percent_change, format='json').status_code == 409
        deposit_rows = {row['key']: row for row in client.get(url).data['data']['evidence']}
        for key in ('deposit_targets', 'deposit_percent'):
            assert client.post(url + '/confirm/' + key, {'fingerprint': deposit_rows[key]['snapshot']['fingerprint']}, format='json').status_code == 200
        withdraw = client.get(url).data['data']['withdrawal']
        withdraw_change = {'min_amount': 1000, 'balance_threshold': 5000, 'tax_rate': 3, 'fingerprint': withdraw['fingerprint']}
        assert client.put(url + '/withdraw', withdraw_change, format='json').status_code == 200
        assert client.put(url + '/withdraw', withdraw_change, format='json').status_code == 409
        withdraw_row = {row['key']: row for row in client.get(url).data['data']['evidence']}['withdraw']
        assert client.post(url + '/confirm/withdraw', {'fingerprint': withdraw_row['snapshot']['fingerprint']}, format='json').status_code == 200
        with store_context(store):
            record = AdminAuditLog.objects.filter(resource='provider-capacity', operator_name='平台超级管理员：' + marker).latest('id')
            assert record.operator_account_id is None and record.request_body['actor_kind'] == 'platform'
            record = AdminAuditLog.objects.filter(resource='settlement-clocks', operator_name='平台超级管理员：' + marker).latest('id')
            assert record.operator_account_id is None and record.request_body['actor_kind'] == 'platform'
            for resource in ('deposit-targets', 'deposit-percent'):
                record = AdminAuditLog.objects.filter(resource=resource, operator_name='平台超级管理员：' + marker).latest('id')
                assert record.operator_account_id is None and record.request_body['actor_kind'] == 'platform'
            record = AdminAuditLog.objects.filter(resource='withdraw-rules', operator_name='平台超级管理员：' + marker).latest('id')
            assert record.operator_account_id is None and record.request_body['actor_kind'] == 'platform'
        assert PlatformAuditEvent.objects.filter(account=account, action='setup.capacity_result').exists()
        if alias != 'default':
            transaction.set_rollback(True, using=alias)
        transaction.set_rollback(True, using='default')
    with store_context(store):
        assert list(SystemConfig.objects.filter(key__in=config_keys).order_by('key').values_list('key', 'id', 'value', 'updated_at')) == before
        assert not AdminAuditLog.objects.filter(resource='provider-capacity', operator_name='平台超级管理员：' + marker).exists()
        assert not AdminAuditLog.objects.filter(resource='settlement-clocks', operator_name='平台超级管理员：' + marker).exists()
        assert not AdminAuditLog.objects.filter(resource__in=['deposit-targets', 'deposit-percent'], operator_name='平台超级管理员：' + marker).exists()
        assert not AdminAuditLog.objects.filter(resource='withdraw-rules', operator_name='平台超级管理员：' + marker).exists()
    assert not PlatformAccount.objects.filter(username=marker).exists()
    print(f'{slug}: platform setup API and store audit verified; transactions rolled back')


with override_settings(PLATFORM_IDENTITY_ENABLED=True, ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False):
    verify('shenglang', 'default')
    verify('xinan', 'store_b')
