"""Real gift payment against registered isolated MySQL; all writes roll back."""
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services' / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.fronted_acceptance_settings')
import django
django.setup()
from django.conf import settings
from django.db import connections
from django.test import override_settings
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import PointFundingLot
from engagement.services import wallet_funding_parts
from orders.tests.factories import make_user, make_escort_profile, make_service, make_service_category
from stores.context import resolve_store, store_context
from stores.db import transaction
from users.models import CustomUser
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


def verify(slug, alias, host):
    store = resolve_store(slug)
    if store.database_alias != alias:
        raise RuntimeError('门店与测试库映射不一致')
    marker = 'gift-principal-' + uuid.uuid4().hex[:16]
    with store_context(store), transaction.atomic():
        boss = make_user(username=marker + '-boss')
        provider = make_user(username=marker + '-player', role=CustomUser.Role.PROVIDER)
        boss_account = get_or_create_account_for_legacy_user(boss)
        provider_account = get_or_create_account_for_legacy_user(provider)
        make_escort_profile(provider, account=provider_account, display_name=marker, is_verified=True)
        wallet = get_wallet(account=boss_account, user=boss)
        wallet.balance = 8000; wallet.save(update_fields=['balance'])
        gift = PointFundingLot.objects.create(account=boss_account, source_key=marker + '-gift', kind='GIFT', remaining=5000)
        principal = PointFundingLot.objects.create(account=boss_account, source_key=marker + '-principal', kind='PREPAID', remaining=3000)
        category = make_service_category(name=marker, is_gift=True)
        service = make_service(name=marker, price=2100, service_category=category)
        client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss_account)[0])
        context = client.get('/api/orders/gifts/context/', {'provider_id': provider_account.pk})
        assert context.data['data']['prepaid_balance'] == 3000, context.data
        result = client.post('/api/orders/gifts/send/', {'gift_service_id': service.pk, 'provider_id': provider_account.pk,
            'quantity': 1, 'expected_amount': 2100, 'client_request_id': marker}, format='json')
        assert result.data['code'] == 0, result.data
        wallet.refresh_from_db(); principal.refresh_from_db(); gift.refresh_from_db()
        assert (wallet.balance, principal.remaining, gift.remaining) == (5900, 900, 5000)
        assert wallet_funding_parts(wallet) == {'prepaid_balance': 900, 'gift_balance': 5000, 'compensation_balance': 0}
        transaction.set_rollback(True)
    with store_context(store):
        assert not CustomUser.objects.filter(username=marker + '-boss').exists()
        assert not PointFundingLot.objects.filter(source_key__startswith=marker).exists()
    print(f'{slug}: gift paid from principal only; all test writes rolled back')


with override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False):
    verify('shenglang', 'default', 'sl.esports.club')
    verify('xinan', 'store_b', 'xa.esports.club')
