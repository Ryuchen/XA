"""Rollback-only financial journey in the two registered local dev schemas.

No worker, external transport, real customer or production schema is used.
The fixture's opening wallet balance is synthetic; this verifies the order,
case and wallet logic, not real-world payment collection.
"""
import os
import logging
import sys
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'services' / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.fronted_acceptance_settings')

import django
django.setup()
logging.getLogger('xa.money').setLevel(logging.ERROR)

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connections
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership, AdminRole
from engagement.confirmation import process_order
from orders.models import AfterSale, Evaluation, Order, OrderProvider
from orders.tests.factories import make_service
from orders.tests.test_release_flow import screenshot
from stores.context import resolve_store, store_context
from stores.db import transaction
from users.models import EscortProfile
from wallet.models import ProviderReport, ProviderReportImage, Transaction
from wallet.services import get_wallet


EXPECTED = {'default': 'xa_test_fronted_acceptance_20261104', 'store_b': 'xa_test_store_preview_b_20261104'}
if settings.SETTINGS_MODULE != 'config.fronted_acceptance_settings':
    raise RuntimeError('仅允许已登记的本地隔离开发配置')
for alias, expected in EXPECTED.items():
    connection = connections[alias]
    if (connection.vendor != 'mysql' or connection.settings_dict['HOST'] != '127.0.0.1'
            or str(connection.settings_dict['PORT']) != '13306'):
        raise RuntimeError('数据库连接不在已登记的本机隔离范围')
    with connection.cursor() as cursor:
        cursor.execute('SELECT DATABASE()')
        if cursor.fetchone()[0] != expected:
            raise RuntimeError('数据库名称与已登记测试库不一致')


def ok(response):
    assert response.status_code == 200 and response.data.get('code') == 0, response.data
    return response.data.get('data')


def run_store(slug, domain):
    store = resolve_store(slug)
    if store.database_alias != {'shenglang': 'default', 'xinan': 'store_b'}[slug]:
        raise RuntimeError('门店与已登记测试数据库的映射不一致')
    marker = 'scope_check_' + uuid.uuid4().hex[:16]
    User = get_user_model()
    with store_context(store):
        with transaction.atomic():
            def person(suffix, role):
                user = User.objects.create_user(username=marker + suffix, password='temporary-test-only', role=role)
                account = get_or_create_account_for_legacy_user(user)
                if role == 'PROVIDER':
                    EscortProfile.objects.create(user=user, account=account, display_name=marker + suffix,
                                                 deposit_mode='EARNINGS', is_verified=True)
                return user, account

            boss, boss_account = person('_boss', 'CUSTOMER')
            first, first_account = person('_a', 'PROVIDER')
            second, second_account = person('_b', 'PROVIDER')
            staff, staff_account = person('_staff', 'OPERATOR')
            role = AdminRole.objects.create(name=marker, code=marker, permissions=['order:view', 'order:settle',
                                                                                  'evaluation:view', 'evaluation:delete'])
            membership = AdminMembership.objects.create(user=staff, account=staff_account)
            membership.roles.add(role)
            service = make_service(name=marker, price=5000, billing_provider_count=2, commission_rate=20)
            order = Order.objects.create(customer=boss, customer_account=boss_account, provider=first,
                provider_account=first_account, service=service, status='COMPLETED', payment_status='PAID',
                escort_mode='DOUBLE', completed_at=timezone.now(), amount=10000,
                provider_income=8000, shop_income=2000, commission_rate=20, settlement_mode='REPORT_APPROVAL',
                service_aftersale_hours=2, service_freeze_hours=1, service_clock_version=1, service_deposit_percent=60)
            for user, account in ((first, first_account), (second, second_account)):
                OrderProvider.objects.create(order=order, provider=user, provider_account=account,
                    provider_name_snapshot=user.username, settlement_base=5000, provider_income=4000, commission_rate=20)
                report = ProviderReport.objects.create(order=order, provider=user, provider_account=account,
                    game_name='隔离测试', amount=5000, payout_amount=4000, commission_rate=20,
                    status='APPROVED', submitted_at=timezone.now(),
                    entry_image=screenshot(), completion_image=screenshot())
                ProviderReportImage.objects.create(report=report, image=screenshot())
            wallet = get_wallet(account=boss_account, user=boss, for_update=True)
            wallet.balance = 0
            wallet.save(update_fields=['balance'])
            Transaction.objects.create(wallet=wallet, order=order, tx_type='PAY', amount=-10000,
                                       balance_before=10000, balance_after=0)
            def client(account):
                result = APIClient(HTTP_HOST=domain)
                result.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                return result
            boss_client, first_client, second_client, admin = (client(account) for account in
                (boss_account, first_account, second_account, staff_account))
            ok(boss_client.post('/api/orders/evaluate/', {'order_id': order.pk,
                'provider_account_id': first_account.pk, 'score': 5, 'skill_score': 1,
                'attitude_score': 5, 'communication_score': 5}, format='json'))
            evaluation = Evaluation.objects.get(order=order, provider=first)
            case = AfterSale.objects.get(order=order)
            assert evaluation.low_rating_aftersale_state == 'OPENED'
            assert case.affected_provider_account_ids == [first_account.pk]
            assert ok(first_client.get(f'/api/orders/orders/{order.pk}/'))['aftersale_status'] == 'OPEN'
            assert ok(second_client.get(f'/api/orders/orders/{order.pk}/'))['aftersale_status'] == ''
            with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=3)):
                assert not process_order(order.pk)
                assert OrderProvider.objects.get(order=order, provider=first).settled_at is None
                assert OrderProvider.objects.get(order=order, provider=second).settled_at is not None
                ok(admin.delete(f'/api/admin/evaluations/{evaluation.pk}/',
                                {'reply_version': 0, 'void_reason': '隔离测试作废'}, format='json'))
                case.refresh_from_db()
                assert case.status == 'OPEN'
                ok(admin.post(f'/api/admin/aftersales/{case.pk}/resolve/', {'resolution': '隔离测试已处理'}, format='json'))
                assert process_order(order.pk)
                assert not process_order(order.pk)
            assert order.transactions.filter(tx_type='INCOME', wallet__user=first).count() == 1
            assert order.transactions.filter(tx_type='INCOME', wallet__user=second).count() == 1
            transaction.set_rollback(True)
        assert not User.objects.filter(username__startswith=marker).exists()
    print(f'{slug}: isolated transaction rolled back; scoped settlement and void passed')


with tempfile.TemporaryDirectory(prefix='xa-scoped-settlement-') as media, override_settings(
        MEDIA_ROOT=media, CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
        CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}},
        LOGIN_OTP_REQUIRED=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False):
    run_store('shenglang', 'sl.esports.club')
    run_store('xinan', 'xa.esports.club')
