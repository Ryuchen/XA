"""Real late-dispatch attribution and refund serialize on their order."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from console import test_payroll_late_activity as salary_fixture
from console.payroll_models import PayrollEventBarrier, PayrollLock, SalaryActivity
from orders.models import Order, OrderRefund, OrderStatusLog
from orders.tests import test_negotiation_execution
from stores.context import resolve_store, store_context
from stores.db import transaction
from wallet.models import Transaction


@skipUnless(connection.vendor == 'mysql', 'Requires an independently isolated MySQL server')
@override_settings(PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True, PAYROLL_REFUND_ACCOUNTING_ENABLED=True,
    NEGOTIATION_PROPOSAL_ENABLED=True, REFUND_DEPOSIT_DEFICIT_ENABLED=True,
    LICENSE_ACCOUNT_ADMISSION_ENABLED=False, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'],
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class PayrollLateDispatchRefundBarrierMySQLTest(TransactionTestCase):
    databases = {'default'}

    def setUp(self):
        db = connection.settings_dict
        if (db['USER'] != 'xa_test_payroll' or db['HOST'] != '127.0.0.1'
                or not db['NAME'].startswith('test_xa_test_payroll_')):
            self.fail('仅允许独立临时测试数据库')
        self.store = resolve_store()
        with store_context(self.store):
            PayrollEventBarrier.objects.get_or_create(pk=1)
            self.salary = salary_fixture.LateActivityPayrollTest(
                methodName='test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms')
            self.salary.setUp()
            self.salary.plan.base_fen = 0
            self.salary.plan.dispatch_day_bps = self.salary.plan.dispatch_night_bps = 100
            self.salary.plan.save(update_fields=['base_fen', 'dispatch_day_bps', 'dispatch_night_bps'])
            self.salary.assign(self.salary.original_text)
            self.original = self.salary.confirm(self.salary.original_text)
            self.salary.assign(self.salary.current_text)
            self.flow = test_negotiation_execution.NegotiationExecutionPostingTests(
                methodName='test_posts_boss_refund_wallet_and_cash_deposit_once_in_one_transaction')
            self.flow.setUp()
            self.flow.client.defaults['HTTP_HOST'] = 'admin.esports.club'
            Order.objects.filter(pk=self.flow.order.pk).update(created_at=self.salary.original_time)
            source = OrderStatusLog.objects.create(order=self.flow.order, action='CREATE', operator_account=self.salary.worker)
            OrderStatusLog.objects.filter(pk=source.pk).update(created_at=self.salary.original_time)
            Transaction.objects.filter(order=self.flow.order, tx_type='PAY').update(created_at=self.salary.original_time)
            self.case, self.proposal = self.flow.accepted(1000)

    def _wait_for_order_lock(self, pending):
        deadline = monotonic() + 10
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute('SHOW FULL PROCESSLIST')
                waiting = any(row[3] == connection.settings_dict['NAME'] and row[4] == 'Query'
                    and 'ORDERS_ORDER' in str(row[7] or '').upper() and 'FOR UPDATE' in str(row[7] or '').upper()
                    for row in cursor.fetchall())
            if waiting:
                return
            if pending.done():
                self.fail(f'竞争请求未等待订单锁: {pending.result()}')
            sleep(0.05)
        self.fail('未观察到竞争请求等待订单锁')

    def _post_activity(self):
        close_old_connections()
        try:
            with store_context(self.store):
                response = self.salary.client.post('/api/admin/salary-activities/', {'kind': 'DISPATCH',
                    'account': self.salary.worker.pk, 'source_id': self.flow.order.pk,
                    'client_request_id': 'late-dispatch-race', 'reason': '核实跨期派单'}, format='json')
                return response.status_code, response.data
        finally:
            close_old_connections()

    def _post_refund(self):
        close_old_connections()
        try:
            with store_context(self.store):
                return self.flow.execute(self.case, self.proposal, request_id='late-dispatch-refund').pk
        finally:
            close_old_connections()

    def _verify(self, activity_result, refund_id):
        self.assertEqual(activity_result[0], 201, activity_result)
        with store_context(self.store):
            activity = SalaryActivity.objects.get(pk=activity_result[1]['data']['id'])
            self.assertEqual(activity.order_id, self.flow.order.pk)
            self.assertEqual(OrderRefund.objects.filter(order=self.flow.order).count(), 1)
            from console.payroll_late_dispatch import current_terms
            result = current_terms(activity, cutoff=timezone.now())
            self.assertEqual((result['commission_basis_fen'], result['commission_fen']), (9000, 90))
            preview = self.salary.preview(self.salary.current_text)
            self.assertTrue(preview['can_confirm'], preview['warnings'])
            result = self.salary.confirm(self.salary.current_text)
            line = next(row for row in result['lines'] if row['account'] == self.salary.worker.pk)
            self.assertEqual(line['late_adjustments_fen'], 90)
            self.assertEqual(self.salary.preview(self.salary.original_text)['totals'], self.original['totals'])

    def test_late_activity_holds_order_until_refund_can_post(self):
        from console import payroll_late_dispatch
        original = payroll_late_dispatch.current_terms
        entered, release = Event(), Event()
        def paused(activity, *, cutoff):
            entered.set()
            if not release.wait(15):
                raise TimeoutError('test release not signalled')
            return original(activity, cutoff=cutoff)
        try:
            with patch('console.payroll_late_dispatch.current_terms', side_effect=paused):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    activity = pool.submit(self._post_activity)
                    self.assertTrue(entered.wait(10), 'activity did not reach its locked calculation')
                    refund = pool.submit(self._post_refund)
                    self._wait_for_order_lock(refund)
                    self.assertFalse(refund.done())
                    release.set()
                    self._verify(activity.result(timeout=20), refund.result(timeout=20))
        finally:
            release.set()

    def test_refund_holds_order_before_late_activity_locks_payroll(self):
        from orders import negotiation_execution
        original = negotiation_execution.verify_execution
        entered, release = Event(), Event()
        def paused(execution):
            entered.set()
            if not release.wait(15):
                raise TimeoutError('test release not signalled')
            return original(execution)
        try:
            with patch('orders.negotiation_execution.verify_execution', side_effect=paused):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    refund = pool.submit(self._post_refund)
                    self.assertTrue(entered.wait(10), 'refund did not reach its locked verification')
                    activity = pool.submit(self._post_activity)
                    self._wait_for_order_lock(activity)
                    with store_context(self.store), transaction.atomic():
                        PayrollLock.objects.select_for_update(nowait=True).get(pk=1)
                    self.assertFalse(activity.done())
                    release.set()
                    self._verify(activity.result(timeout=20), refund.result(timeout=20))
        finally:
            release.set()
