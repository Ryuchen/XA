"""Real order-payment APIs serialize with an exclusive payroll event barrier."""
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep
from unittest import skipUnless
from uuid import uuid4

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from common.mysql_test_support import require_disposable_mysql
from console.payroll_event_barrier import lock_payroll_events
from console.grab_config import save_grab_window
from console.payroll_models import PayrollEventBarrier
from orders.models import Order, Reservation, ServiceItem
from orders.tests import test_dispatch_real_workflow, test_reservation_api
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.models import Store
from wallet.models import Transaction, Wallet


@skipUnless(connection.vendor == 'mysql', 'Requires disposable MySQL row locks')
@override_settings(PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True, LICENSE_ACCOUNT_ADMISSION_ENABLED=False,
    LEGACY_FORCE_AUTH_COMPAT=True, ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
    CELERY_TASK_ALWAYS_EAGER=False, GRAB_SINGLE_WINDOW_ENABLED=True,
    GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True, GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True)
class PaymentPayrollBarrierMySQLTest(TransactionTestCase):
    def _post_during_payroll(self, store, client, url, payload, service_id, reservation_id=None):
        def post():
            close_old_connections()
            try:
                with store_context(store):
                    response = client.post(url, payload, format='json')
                    return response.status_code, response.data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with store_context(store), transaction.atomic():
                lock_payroll_events()
                before = Order.objects.count()
                pending = pool.submit(post)
                deadline, found = monotonic() + 10, False
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute('SHOW FULL PROCESSLIST')
                        found = any(row[3] == connection.settings_dict['NAME'] and row[4] == 'Query'
                            and 'CONSOLE_PAYROLLEVENTBARRIER' in str(row[7] or '').upper()
                            and 'LOCK IN SHARE MODE' in str(row[7] or '').upper() for row in cursor.fetchall())
                    if found:
                        break
                    if pending.done():
                        self.fail(f'Payment returned before reaching barrier: {pending.result()}')
                    sleep(0.05)
                self.assertTrue(found, 'API did not wait for the payroll event barrier')
                self.assertFalse(pending.done())
                ServiceItem.objects.select_for_update(nowait=True).get(pk=service_id)
                if reservation_id:
                    Reservation.objects.select_for_update(nowait=True).get(pk=reservation_id)
                self.assertEqual(Order.objects.count(), before)
            return pending.result(timeout=20)

    def test_all_payment_apis_wait_before_business_locks_and_post_once(self):
        require_disposable_mysql(connection)
        Store.objects.get_or_create(slug='shenglang', defaults={'name': '测试门店',
            'database_alias': 'default', 'status': Store.Status.ACTIVE, 'entity_prefix': 'SL'})
        store = resolve_store()
        with store_context(store):
            PayrollEventBarrier.objects.get_or_create(pk=1)
            save_grab_window(30, 0, lambda *_: None)
            dispatch = test_dispatch_real_workflow.DispatchRealWorkflowTest(
                methodName='test_quote_then_dispatch_to_lobby_debits_once')
            dispatch.client = APIClient()
            dispatch.setUp()
            boss = APIClient(HTTP_HOST='sl.esports.club')
            boss.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(dispatch.boss_account)[0])
            order_payload = {'service_id': dispatch.service.pk, 'dispatch_route': 'PUBLIC',
                             'client_request_id': str(uuid4())}
        status, data = self._post_during_payroll(store, boss, '/api/orders/create/', order_payload, dispatch.service.pk)
        self.assertEqual((status, data['code']), (200, 0), data)
        order_id = data['data']['id']
        with store_context(store):
            self.assertEqual(Transaction.objects.filter(order_id=order_id, tx_type='PAY').count(), 1)
            self.assertEqual(Wallet.objects.get(user=dispatch.boss).balance, 90000)
            boss.post('/api/orders/create/', order_payload, format='json')
            self.assertEqual(Transaction.objects.filter(order_id=order_id, tx_type='PAY').count(), 1)
            quoted = dispatch.quote()
            admin_payload = dispatch.payload(quoted['amount'])
        status, data = self._post_during_payroll(store, dispatch.client, '/api/admin/orders/dispatch/',
                                                 admin_payload, dispatch.service.pk)
        self.assertEqual((status, data['code']), (200, 0), data)
        admin_order_id = data['data']['id']
        with store_context(store):
            self.assertEqual(Transaction.objects.filter(order_id=admin_order_id, tx_type='PAY').count(), 1)
            self.assertEqual(Wallet.objects.get(user=dispatch.boss).balance, 80000)
            dispatch.client.post('/api/admin/orders/dispatch/', admin_payload, format='json')
            self.assertEqual(Transaction.objects.filter(order_id=admin_order_id, tx_type='PAY').count(), 1)
            reservation = test_reservation_api.ReservationConvertApiTest(methodName='test_convert_creates_paid_order')
            reservation.client = APIClient()
            reservation.setUp()
            payload = {'client_request_id': 'barrier-convert'}
        status, data = self._post_during_payroll(store, reservation.client, reservation.convert_url, payload,
                                                 reservation.service.pk, reservation.reservation.pk)
        self.assertEqual((status, data['code']), (200, 0), data)
        reservation_order_id = data['data']['order']['id']
        with store_context(store):
            self.assertEqual(Transaction.objects.filter(order_id=reservation_order_id, tx_type='PAY').count(), 1)
            self.assertEqual(Wallet.objects.get(user=reservation.boss).balance, 98000)
            reservation.client.post(reservation.convert_url, payload, format='json')
            self.assertEqual(Transaction.objects.filter(order_id=reservation_order_id, tx_type='PAY').count(), 1)
