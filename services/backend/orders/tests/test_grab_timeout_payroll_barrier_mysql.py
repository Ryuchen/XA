"""Grab and timeout refunds serialize with payroll in disposable MySQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from time import monotonic, sleep
from unittest import skipUnless
from uuid import uuid4

from django.core.management import call_command
from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from common.mysql_test_support import require_disposable_mysql
from console.grab_config import save_grab_window
from console.payroll_event_barrier import lock_payroll_events
from console.payroll_models import PayrollEventBarrier
from orders.models import Order, OrderGrabPoolSnapshot, OrderGrabRound, OrderGrabReceiptProbe, OrderRefund
from orders.tasks import auto_cancel_pending_order
from orders.tests import test_grab_single_flow
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.models import Store


@skipUnless(connection.vendor == 'mysql', 'Requires disposable MySQL row locks')
@override_settings(PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True, LICENSE_ACCOUNT_ADMISSION_ENABLED=False,
    GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True, GRAB_SINGLE_WINDOW_ENABLED=True, GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True,
    PRESTART_SCOPE_SNAPSHOT_ENABLED=True, REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED=True,
    GAME_COMMISSION_PAYMENT_ENABLED=False, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'],
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class GrabTimeoutPayrollBarrierMySQLTest(TransactionTestCase):
    def _wait_for_gate(self, store, order_id, operation, *, cancellation_receipt=False):
        def run():
            close_old_connections()
            try:
                with store_context(store):
                    return operation()
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with store_context(store), transaction.atomic():
                lock_payroll_events()
                pending = pool.submit(run)
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
                        self.fail(f'Operation returned before barrier: {pending.result()}')
                    sleep(0.05)
                self.assertTrue(found, 'Operation did not wait for payroll barrier')
                Order.objects.select_for_update(nowait=True).get(pk=order_id)
                if cancellation_receipt:
                    self.assertTrue(OrderGrabReceiptProbe.objects.filter(round__pool__order_id=order_id,
                                                                          kind='BOSS_CANCEL').exists())
                self.assertFalse(OrderRefund.objects.filter(order_id=order_id).exists())
            return pending.result(timeout=20)

    def test_cancel_priority_management_candidate_clock_and_ordinary_timeout(self):
        require_disposable_mysql(connection)
        Store.objects.get_or_create(slug='shenglang', defaults={'name':'测试门店', 'entity_prefix':'SL',
            'database_alias':'default', 'status':Store.Status.ACTIVE})
        store = resolve_store()
        with store_context(store):
            PayrollEventBarrier.objects.get_or_create(pk=1)
            flow = test_grab_single_flow.GrabSingleFlowTest(
                methodName='test_boss_cancel_before_deadline_refunds_and_cannot_allocate')
            flow.client = APIClient(HTTP_HOST='sl.esports.club')
            self.addCleanup(flow.doCleanups)
            flow.setUp()
            flow.prepare()
            save_grab_window(30, 1, lambda *_: None)
            flow.auth(flow.boss_account)
            created = flow.create(dispatch_mode='AUTO_GRAB', client_request_id=str(uuid4()))
            self.assertEqual(created.data['code'], 0, created.data)
            cancelled = Order.objects.get(pk=created.data['data']['id'])
            flow.auth(flow.accounts[1])
            grab = flow.client.post(f'/api/orders/orders/{cancelled.pk}/grab/',
                                    {'client_request_id':'candidate-before-cancel'}, format='json')
            self.assertEqual(grab.data['code'], 0, grab.data)
            flow.auth(flow.boss_account)
        response = self._wait_for_gate(store, cancelled.pk, lambda: flow.client.post(
            f'/api/orders/orders/{cancelled.pk}/cancel/', {'reason':'计划变更'}, format='json'),
            cancellation_receipt=True)
        self.assertEqual(response.data['code'], 0, response.data)
        with store_context(store):
            cancelled.refresh_from_db()
            self.assertEqual((cancelled.status, cancelled.provider_account_id), ('CANCELLED', None))
            self.assertEqual(OrderRefund.objects.filter(order=cancelled).count(), 1)
            save_grab_window(1, 2, lambda *_: None)
            flow.auth(flow.boss_account)
            created = flow.create(dispatch_mode='AUTO_GRAB', client_request_id=str(uuid4()))
            self.assertEqual(created.data['code'], 0, created.data)
            candidate = Order.objects.get(pk=created.data['data']['id'])
            flow.auth(flow.accounts[1])
            grabbed = flow.client.post(f'/api/orders/orders/{candidate.pk}/grab/',
                                       {'client_request_id':'candidate-before-command'}, format='json')
            self.assertEqual(grabbed.data['code'], 0, grabbed.data)
            pool = OrderGrabPoolSnapshot.objects.get(order=candidate)
            OrderGrabPoolSnapshot.objects.filter(pk=pool.pk).update(original_total_deadline=pool.first_deadline,
                                                                     total_deadline=pool.first_deadline)
            OrderGrabRound.objects.filter(pool=pool, number=1).update(total_deadline=pool.first_deadline)
            Order.objects.filter(pk=candidate.pk).update(auto_cancel_at=pool.first_deadline)
        sleep(1.1)
        self._wait_for_gate(store, candidate.pk, lambda: call_command('check_order_timeout', verbosity=0))
        with store_context(store):
            candidate.refresh_from_db()
            self.assertEqual((candidate.status, candidate.provider_account_id), ('GRABBED', flow.accounts[1].pk))
            self.assertFalse(OrderRefund.objects.filter(order=candidate).exists())
            flow.auth(flow.boss_account)
            created = flow.create(dispatch_mode='AUTO_GRAB', client_request_id=str(uuid4()))
            self.assertEqual(created.data['code'], 0, created.data)
            ungated_candidate = Order.objects.get(pk=created.data['data']['id'])
            flow.auth(flow.accounts[0])
            result = flow.client.post(f'/api/orders/orders/{ungated_candidate.pk}/grab/',
                                      {'client_request_id':'ungated-candidate'}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            pool = OrderGrabPoolSnapshot.objects.get(order=ungated_candidate)
            OrderGrabPoolSnapshot.objects.filter(pk=pool.pk).update(original_total_deadline=pool.first_deadline,
                                                                     total_deadline=pool.first_deadline)
            OrderGrabRound.objects.filter(pool=pool, number=1).update(total_deadline=pool.first_deadline)
            Order.objects.filter(pk=ungated_candidate.pk).update(auto_cancel_at=pool.first_deadline)
            sleep(1.1)
            with override_settings(PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=False):
                call_command('check_order_timeout', verbosity=0)
            ungated_candidate.refresh_from_db()
            self.assertEqual((ungated_candidate.status, ungated_candidate.provider_account_id),
                             ('GRABBED', flow.accounts[0].pk))
            self.assertFalse(OrderRefund.objects.filter(order=ungated_candidate).exists())
            flow.auth(flow.boss_account)
            created = flow.create(dispatch_mode='AUTO_GRAB', client_request_id=str(uuid4()))
            self.assertEqual(created.data['code'], 0, created.data)
            paused = Order.objects.get(pk=created.data['data']['id'])
            Order.objects.filter(pk=paused.pk).update(auto_cancel_at=timezone.now()-timedelta(seconds=1))
            with override_settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False):
                call_command('check_order_timeout', verbosity=0)
            paused.refresh_from_db()
            self.assertEqual((paused.status, paused.payment_status), ('PENDING', 'PAID'))
            self.assertFalse(OrderRefund.objects.filter(order=paused).exists())
            ordinary = flow.create(client_request_id=str(uuid4()))
            self.assertEqual(ordinary.data['code'], 0, ordinary.data)
            ordinary = Order.objects.get(pk=ordinary.data['data']['id'])
            Order.objects.filter(pk=ordinary.pk).update(auto_cancel_at=timezone.now()-timedelta(seconds=1))
        result = self._wait_for_gate(store, ordinary.pk, lambda: auto_cancel_pending_order.run(ordinary.pk))
        self.assertEqual(result, 'cancelled')
        with store_context(store):
            ordinary.refresh_from_db()
            self.assertEqual((ordinary.status, ordinary.payment_status), ('CANCELLED', 'REFUNDED'))
            self.assertEqual(OrderRefund.objects.filter(order=ordinary).count(), 1)
            flow.auth(flow.boss_account)
            plain = flow.create(client_request_id=str(uuid4()))
            self.assertEqual(plain.data['code'], 0, plain.data)
            plain = Order.objects.get(pk=plain.data['data']['id'])
            Order.objects.filter(pk=plain.pk).update(auto_cancel_at=timezone.now()-timedelta(seconds=1))
            with override_settings(PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=False,
                                   GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False):
                call_command('check_order_timeout', verbosity=0)
            plain.refresh_from_db(); paused.refresh_from_db()
            self.assertEqual((plain.status, plain.payment_status), ('CANCELLED', 'REFUNDED'))
            self.assertEqual(OrderRefund.objects.filter(order=plain).count(), 1)
            self.assertEqual((paused.status, paused.payment_status), ('PENDING', 'PAID'))
            flow.service.billing_provider_count = 2
            flow.service.save(update_fields=['billing_provider_count'])
            save_grab_window(30, 3, lambda *_: None)
            with override_settings(GRAB_TEAM_WINDOW_ENABLED=True):
                created = flow.create(dispatch_mode='AUTO_GRAB', client_request_id=str(uuid4()))
                self.assertEqual(created.data['code'], 0, created.data)
                team_order = Order.objects.get(pk=created.data['data']['id'])
                response = self._wait_for_gate(store, team_order.pk, lambda: flow.client.post(
                    f'/api/orders/orders/{team_order.pk}/cancel/', {'reason':'双陪取消'}, format='json'),
                    cancellation_receipt=True)
                self.assertEqual(response.data['code'], 0, response.data)
                team_order.refresh_from_db()
                self.assertEqual((team_order.status, team_order.payment_status), ('CANCELLED', 'REFUNDED'))
                self.assertEqual(OrderRefund.objects.filter(order=team_order).count(), 1)
