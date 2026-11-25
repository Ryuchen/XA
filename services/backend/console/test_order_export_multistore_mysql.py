import csv
import io
from unittest import skipUnless

from django.conf import settings
from django.db import connection
from django.test import override_settings
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from common.mysql_test_support import require_disposable_mysql
from console.models import AdminAuditLog, AdminMembership
from orders.models import Order
from orders.tests.factories import make_console_user, make_order, make_service, make_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store


URL = '/api/admin/orders/export/'


@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
@override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False,
                   LOGIN_OTP_REQUIRED=False, LICENSE_BENEFIT_ENFORCEMENT_ENABLED=False)
class StaffOrderExportStoreIsolationTest(APITransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    reset_sequences = True

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if connection.vendor == 'mysql':
            require_disposable_mysql(connection)

    def create_store_export(self, store, label):
        with store_context(store):
            staff = make_console_user(['order:export', 'order:view'])
            account = get_or_create_account_for_legacy_user(staff)
            link_legacy_relations(staff, account)
            AdminMembership.objects.filter(user=staff).update(account=account)
            customer = make_user(username=f'export-customer-{label}')
            customer_account = get_or_create_account_for_legacy_user(customer)
            link_legacy_relations(customer, customer_account)
            order = make_order(customer, service=make_service(name=f'导出隔离服务-{label}'),
                               amount=1000, customer_account=customer_account,
                               remark=f'仅属于 {store.slug} 的导出记录')
            token, _ = issue_account_tokens(account)
        return token, order.order_no, order.pk, account.pk

    def client_for(self, token, **headers):
        client = APIClient(HTTP_HOST='admin.esports.club',
                           HTTP_AUTHORIZATION='Bearer ' + token, **headers)
        return client

    def rows(self, response):
        self.assertEqual(response.status_code, 200, getattr(response, 'data', response.content))
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
        return dict(zip(rows[9], rows[10])), rows

    def test_same_numeric_ids_and_store_hints_never_cross_export_boundaries(self):
        first, second = resolve_store(), xinan_store()
        first_token, first_no, first_id, first_account = self.create_store_export(first, 'first')
        second_token, second_no, second_id, second_account = self.create_store_export(second, 'second')
        self.assertEqual(first_id, second_id)
        self.assertNotEqual(first_no, second_no)

        exports = []
        for token, store, own_no, other_no, other_slug, account_id in (
            (first_token, first, first_no, second_no, second.slug, first_account),
            (second_token, second, second_no, first_no, first.slug, second_account),
        ):
            with self.subTest(store=store.slug):
                client = self.client_for(token)
                response = client.get(URL, {'order_no_exact': own_no})
                row, rows = self.rows(response)
                watermark = dict(rows[:8])
                self.assertEqual(row['订单号'], own_no)
                self.assertEqual(row['门店'], store.slug)
                self.assertNotIn(other_no.encode(), response.content)
                self.assertEqual(watermark['门店'], f'{store.name}（{store.slug}）')
                self.assertNotIn(other_slug, watermark['门店'])
                self.assertIn(response['X-Export-Id'].encode(), response.content)
                audit = AdminAuditLog.objects.using('default').get(object_id=response['X-Export-Id'])
                self.assertEqual(audit.operator_account_id, account_id)
                self.assertEqual(audit.request_body['filters']['order_no_exact'], '***')

                wrong_header = client.get(URL, {'order_no_exact': own_no},
                                          HTTP_X_STORE_SLUG=other_slug)
                self.assertEqual(wrong_header.status_code, 400, getattr(wrong_header, 'data', wrong_header.content))
                wrong_query = client.get(URL + '?store=' + other_slug)
                self.assertEqual(wrong_query.status_code, 400, getattr(wrong_query, 'data', wrong_query.content))
                exports.append((audit.object_id, store.slug))

        self.assertEqual(len(set(exports)), 2)
        self.assertEqual(AdminAuditLog.objects.using('default').filter(
            resource='order-export', status_code=200).count(), 2)

    @skipUnless(connection.vendor == 'mysql', 'SHOW SESSION STATUS requires the registered MySQL test database')
    def test_read_only_export_does_not_write_any_store_business_table(self):
        for store in (resolve_store(), xinan_store()):
            with store.subTest(store=store.slug):
                token, order_no, _, _ = self.create_store_export(store, 'read-only')
                before = business_write_counters(store)
                response = self.client_for(token).get(URL, {'order_no_exact': order_no})
                self.rows(response)
                self.assertEqual(business_write_counters(store), before)


def business_write_counters(store):
    from django.db import connections
    with store_context(store):
        alias = store.database_alias
        with connections[alias].cursor() as cursor:
            cursor.execute("SHOW SESSION STATUS LIKE 'Com_insert'")
            inserts = cursor.fetchone()[1]
            cursor.execute("SHOW SESSION STATUS LIKE 'Com_update'")
            updates = cursor.fetchone()[1]
            cursor.execute("SHOW SESSION STATUS LIKE 'Com_delete'")
            deletes = cursor.fetchone()[1]
            cursor.execute("SHOW SESSION STATUS LIKE 'Com_replace'")
            replaces = cursor.fetchone()[1]
        return tuple(int(value) for value in (inserts, updates, deletes, replaces))
