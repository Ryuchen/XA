"""Read-only coverage API on each registered MySQL store, with rolled-back fixtures."""
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
from orders.models import GameCategory, GameCommissionProfile, ServiceItem
from stores.context import resolve_store, store_context
from stores.db import transaction

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


def run_store(slug, expected_alias, rate):
    store = resolve_store(slug)
    if store.database_alias != expected_alias:
        raise RuntimeError('门店与测试库映射不一致')
    marker = 'coverage_' + uuid.uuid4().hex[:14]
    User = get_user_model()
    with store_context(store):
        with transaction.atomic():
            if GameCommissionProfile.objects.filter(game='LEAGUE').exists():
                raise RuntimeError('测试店已有该游戏配置，拒绝修改')
            staff = User.objects.create_user(username=marker, password='temporary-test-only', role='OPERATOR')
            account = get_or_create_account_for_legacy_user(staff)
            role = AdminRole.objects.create(name=marker, code=marker, permissions=['dictionary:view'])
            membership = AdminMembership.objects.create(user=staff, account=account); membership.roles.add(role)
            game = GameCategory.objects.create(name=marker)
            GameCommissionProfile.objects.create(game_category=game, game='LEAGUE',
                rules={'companion': 0, 'grind': rate})
            service = ServiceItem.objects.create(name=marker, price=1000, game_category=game, provider_kind='RANK')
            client = APIClient(HTTP_HOST='admin.esports.club')
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            response = client.get('/api/admin/config/game-commissions/coverage/', {'game_category_id': game.pk})
            assert response.status_code == 200, response.data
            data = response.data['data']
            assert data['status'] == 'DRAFT_NOT_APPLIED'
            assert data['counts'] == {'all': 1, 'matched': 1, 'issues': 0, 'special': 0}, data
            assert (data['list'][0]['id'], data['list'][0]['rate']) == (service.pk, rate), data
            transaction.set_rollback(True)
        assert not GameCategory.objects.filter(name=marker).exists()
        assert not User.objects.filter(username=marker).exists()
    print(f'{slug}: real coverage API matched local synthetic service; transaction rolled back')


with override_settings(LEGACY_FORCE_AUTH_COMPAT=False, LOGIN_OTP_REQUIRED=False,
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
        CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}):
    run_store('shenglang', 'default', 17)
    run_store('xinan', 'store_b', 29)
