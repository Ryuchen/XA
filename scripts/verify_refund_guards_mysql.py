"""Run synthetic refund checks in rollback-only transactions on registered dev schemas."""
import logging
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services' / 'backend'))
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
from orders.models import Order, OrderProvider, OrderRefund
from orders.tests.factories import make_service
from stores.context import resolve_store, store_context
from stores.db import transaction
from users.models import EscortProfile
from wallet.models import Transaction, get_platform_wallet
from wallet.services import get_wallet

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
    marker = 'refund_check_' + uuid.uuid4().hex[:14]
    User = get_user_model()
    with store_context(store):
        with transaction.atomic():
            def person(suffix, role, count=0):
                user = User.objects.create_user(username=marker + suffix, password='temporary-test-only', role=role)
                account = get_or_create_account_for_legacy_user(user)
                if role == 'PROVIDER':
                    EscortProfile.objects.create(user=user, account=account, display_name=marker + suffix,
                                                 deposit_mode='EARNINGS', is_verified=True,
                                                 completed_order_count=count)
                return user, account

            boss, boss_account = person('_boss', 'CUSTOMER')
            first, first_account = person('_a', 'PROVIDER', 3)
            second, second_account = person('_b', 'PROVIDER', 7)
            staff, staff_account = person('_staff', 'OPERATOR')
            role = AdminRole.objects.create(name=marker, code=marker, permissions=['order:view', 'order:refund'])
            membership = AdminMembership.objects.create(user=staff, account=staff_account)
            membership.roles.add(role)
            service = make_service(name=marker, price=10000, commission_rate=20)
            client = APIClient(HTTP_HOST=domain)
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
            customer_wallet = get_wallet(account=boss_account, user=boss, for_update=True)
            first_wallet = get_wallet(account=first_account, user=first, for_update=True)
            second_wallet = get_wallet(account=second_account, user=second, for_update=True)
            shop = get_platform_wallet()
            shop.balance += 100000000
            shop.save(update_fields=['balance'])

            def create_order(**extra):
                order = Order.objects.create(customer=boss, customer_account=boss_account, provider=first,
                    provider_account=first_account, service=service, amount=10000, status='COMPLETED',
                    payment_status='PAID', in_service_at=timezone.now(), completed_at=timezone.now(),
                    settled_at=timezone.now(), **extra)
                Transaction.objects.create(order=order, wallet=customer_wallet, tx_type='PAY', amount=-10000,
                                           balance_before=10000, balance_after=0)
                return order

            first_wallet.balance = 8500
            first_wallet.save(update_fields=['balance'])
            referral_order = create_order(provider_income=8000, inviter=first, inviter_account=first_account,
                                          inviter_commission=500, shop_income=1500)
            Transaction.objects.create(order=referral_order, wallet=first_wallet, tx_type='INCOME', amount=8000,
                balance_before=0, balance_after=8000, remark=f'订单收入：{referral_order.order_no}')
            referral = Transaction.objects.create(order=referral_order, wallet=first_wallet, tx_type='INCOME',
                amount=500, balance_before=8000, balance_after=8500, remark=f'推荐分佣：{referral_order.order_no}')
            Transaction.objects.create(order=referral_order, wallet=shop, tx_type='SHOP_INCOME', amount=1500,
                                       balance_before=shop.balance-1500, balance_after=shop.balance)
            before = (first_wallet.balance, shop.balance, customer_wallet.balance)
            preview = client.get(f'/api/admin/orders/{referral_order.pk}/refund-preview/', {'refund_amount': 5000})
            assert preview.data['errorCode'] == 'REFUND_REFERRAL_STORE_FUNDING_REQUIRED', preview.data
            response = client.post(f'/api/admin/orders/{referral_order.pk}/refund/', {
                'refund_amount': 5000, 'reason': '隔离测试', 'client_request_id': marker,
                'plan_token': 'unusable'}, format='json')
            assert response.data['errorCode'] == 'REFUND_REFERRAL_STORE_FUNDING_REQUIRED', response.data
            for wallet in (first_wallet, shop, customer_wallet):
                wallet.refresh_from_db()
            assert (first_wallet.balance, shop.balance, customer_wallet.balance) == before
            assert not referral.reversals.exists()
            assert not OrderRefund.objects.filter(order=referral_order).exists()

            order = create_order(provider_income=8000, shop_income=2000, escort_mode='DOUBLE')
            for provider, account, wallet in ((first, first_account, first_wallet), (second, second_account, second_wallet)):
                before_balance = wallet.balance
                wallet.balance += 4000
                wallet.save(update_fields=['balance'])
                OrderProvider.objects.create(order=order, provider=provider, provider_account=account,
                    settlement_base=5000, provider_income=4000, settled_at=timezone.now())
                Transaction.objects.create(wallet=wallet, order=order, tx_type='INCOME', amount=4000,
                    balance_before=before_balance, balance_after=wallet.balance, remark=f'订单收入：{order.order_no}')
            Transaction.objects.create(wallet=shop, order=order, tx_type='SHOP_INCOME', amount=2000,
                                       balance_before=shop.balance-2000, balance_after=shop.balance)
            preview = client.get(f'/api/admin/orders/{order.pk}/refund-preview/').data
            assert preview['data']['eligible'], preview
            response = client.post(f'/api/admin/orders/{order.pk}/refund/', {'reason': '隔离测试',
                'client_request_id': marker + '_full', 'plan_token': preview['data']['plan_token']}, format='json')
            assert response.data['code'] == 0, response.data
            order.refresh_from_db()
            first.escort_profile.refresh_from_db()
            second.escort_profile.refresh_from_db()
            assert (order.payment_status, first.escort_profile.completed_order_count,
                    second.escort_profile.completed_order_count) == ('REFUNDED', 3, 7)
            transaction.set_rollback(True)
        assert not User.objects.filter(username__startswith=marker).exists()
    print(f'{slug}: synthetic refund checks passed; transaction rolled back')


with override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
        CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}},
        LOGIN_OTP_REQUIRED=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False):
    run_store('shenglang', 'sl.esports.club')
    run_store('xinan', 'xa.esports.club')
