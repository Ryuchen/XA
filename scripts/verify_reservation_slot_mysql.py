"""Verify assignment guards and converted slot queries; always roll back."""
import os
import sys
import uuid
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services' / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.fronted_acceptance_settings')
import django
django.setup()
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connections
from django.db.models import Count, IntegerField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderProvider, Reservation
from orders.reservation_services import find_conflicting_reservations
from orders.services import capacity_orders_for, count_active_orders, count_capacity_orders
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


def run_store(slug, domain):
    store = resolve_store(slug)
    if store.database_alias != {'shenglang': 'default', 'xinan': 'store_b'}[slug]:
        raise RuntimeError('门店与测试库映射不一致')
    User = get_user_model()
    marker = 'reservation_check_' + uuid.uuid4().hex[:12]
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
            service = make_service(name=marker, price=10000, commission_rate=20)
            order = Order.objects.create(customer=boss, customer_account=boss_account, provider=first,
                provider_account=first_account, service=service, status='GRABBED', payment_status='PAID',
                amount=10000, provider_income=8000, shop_income=2000, commission_rate=20)
            assignment = OrderProvider.objects.create(order=order, provider=first, provider_account=first_account,
                settlement_base=10000, provider_income=8000, commission_rate=20)
            start = timezone.now() + timedelta(days=1)
            end = start + timedelta(hours=1)
            booking = Reservation.objects.create(customer=boss, customer_account=boss_account, provider=first,
                provider_account=first_account, service=service, order=order, status='CONVERTED',
                start_time=start, end_time=end, duration_minutes=60)
            def blocked(account):
                return find_conflicting_reservations(account, start, end).filter(pk=booking.pk).exists()
            assert blocked(first_account) and not blocked(second_account)
            assert count_active_orders(first_account) == 1
            assert count_capacity_orders(first_account) == 0
            # Exercise the same correlated subquery used by the roster and
            # teammate picker against each store's actual MySQL database.
            counts = capacity_orders_for(account_id=OuterRef('account_id')).order_by().values(
                group=Value(1)).annotate(total=Count('pk', distinct=True)).values('total')
            annotated = EscortProfile.objects.filter(account=first_account).annotate(
                capacity=Coalesce(Subquery(counts, output_field=IntegerField()), 0)).get()
            assert annotated.capacity == 0
            client = APIClient(HTTP_HOST=domain)
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(first_account)[0])
            stats = client.get('/api/users/provider-stats/').data
            assert stats['code'] == 0 and stats['data']['active_orders'] == 0, stats
            response = client.post(f'/api/orders/orders/{order.pk}/reject/', {}, format='json')
            assert response.data['errorCode'] == 'ORDER_SELF_REJECTION_DISABLED', response.data
            order.refresh_from_db()
            assert order.status == 'GRABBED' and order.provider_account_id == first_account.pk
            order.status = 'IN_SERVICE'
            order.save(update_fields=['status'])
            assert count_capacity_orders(first_account) == 1
            order.status = 'GRABBED'
            order.save(update_fields=['status'])
            assignment.released_at = timezone.now()
            assignment.save(update_fields=['released_at'])
            OrderProvider.objects.create(order=order, provider=second, provider_account=second_account,
                settlement_base=10000, provider_income=8000, commission_rate=20)
            order.provider = second
            order.provider_account = second_account
            order.save(update_fields=['provider', 'provider_account'])
            assert not blocked(first_account) and blocked(second_account)
            order.status = 'COMPLETED'
            order.save(update_fields=['status'])
            assert not blocked(second_account)
            transaction.set_rollback(True)
        assert not User.objects.filter(username__startswith=marker).exists()
    print(f'{slug}: reservation slot and rejection checks passed; transaction rolled back')


with override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
        CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}},
        LOGIN_OTP_REQUIRED=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False):
    run_store('shenglang', 'sl.esports.club')
    run_store('xinan', 'xa.esports.club')
