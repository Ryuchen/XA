"""Real payroll APIs wait for payment events on the same MySQL store."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep
from unittest import skipUnless
from uuid import uuid4

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.tokens import issue_account_tokens
from common.mysql_test_support import mysql_locking_statements, require_disposable_mysql
from console import test_payroll_late_activity as salary_fixture
from console.payroll_event_barrier import lock_payment_refund_events
from console.payroll_models import PayrollEventBarrier, PayrollLock, PayrollSeparationSettlement
from orders.models import Order
from orders.tests import test_dispatch_real_workflow
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.models import Store
from wallet.models import Transaction


@skipUnless(connection.vendor == 'mysql', 'Requires disposable MySQL row locks')
@override_settings(PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True, PAYROLL_REFUND_ACCOUNTING_ENABLED=True,
    LICENSE_ACCOUNT_ADMISSION_ENABLED=False, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'],
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class PayrollEventIntegrationMySQLTest(TransactionTestCase):
    def _wait_on_barrier(self, future):
        deadline = monotonic() + 10
        while monotonic() < deadline:
            if mysql_locking_statements(connection, 'console_payrolleventbarrier'):
                return
            if future.done():
                self.fail(f'Payroll returned before acquiring barrier: {future.result()}')
            sleep(0.05)
        self.fail('Payroll did not wait on the event barrier')

    def _post(self, store, client, url, body):
        close_old_connections()
        try:
            with store_context(store):
                response = client.post(url, body, format='json')
                return response.status_code, response.data
        finally:
            close_old_connections()

    def test_payment_commit_forces_payroll_repreview_and_closeout_waits(self):
        require_disposable_mysql(connection)
        Store.objects.get_or_create(slug='shenglang', defaults={'name':'测试门店', 'entity_prefix':'SL',
            'database_alias':'default', 'status':Store.Status.ACTIVE})
        store = resolve_store()
        with store_context(store):
            PayrollEventBarrier.objects.get_or_create(pk=1)
            salary = salary_fixture.LateActivityPayrollTest(
                methodName='test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms')
            salary.setUp()
            salary.plan.base_fen = 1000
            salary.plan.performance_fen = 1000
            salary.plan.target_fen = 10000
            salary.plan.target_scope = 'STORE'
            salary.plan.save(update_fields=['base_fen', 'performance_fen', 'target_fen', 'target_scope'])
            salary.assign(salary.current_text)
            preview = salary.preview(salary.current_text)
            self.assertTrue(preview['can_confirm'], preview['warnings'])
            flow = test_dispatch_real_workflow.DispatchRealWorkflowTest(methodName='test_quote_then_dispatch_to_lobby_debits_once')
            flow.client = APIClient()
            flow.setUp()
            boss = APIClient(HTTP_HOST='sl.esports.club')
            boss.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(flow.boss_account)[0])
            payload = {'service_id': flow.service.pk, 'dispatch_route':'PUBLIC', 'client_request_id':str(uuid4())}

        wrote, release = Event(), Event()
        def pay_with_open_transaction():
            close_old_connections()
            try:
                with store_context(store), transaction.atomic():
                    lock_payment_refund_events()
                    response = boss.post('/api/orders/create/', payload, format='json')
                    result = (response.status_code, response.data)
                    wrote.set()
                    if not release.wait(15):
                        raise TimeoutError('Payment transaction was not released')
                    return result
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            payment = pool.submit(pay_with_open_transaction)
            try:
                self.assertTrue(wrote.wait(10), 'Payment API did not finish writing')
                self.assertFalse(payment.done(), 'Payment must remain uncommitted')
                confirm = pool.submit(self._post, store, salary.client, '/api/admin/payroll/confirm/',
                    {'month':salary.current_text, 'fingerprint':preview['fingerprint']})
                self._wait_on_barrier(confirm)
                with store_context(store), transaction.atomic():
                    PayrollLock.objects.select_for_update(nowait=True).get(pk=1)
            finally:
                release.set()
            status, payment_data = payment.result(timeout=20)
            self.assertEqual((status, payment_data['code']), (200, 0), payment_data)
            status, stale = confirm.result(timeout=20)
        self.assertEqual(status, 409, stale)
        with store_context(store):
            self.assertEqual(Transaction.objects.filter(order_id=payment_data['data']['id'], tx_type='PAY').count(), 1)
            updated = salary.preview(salary.current_text)
            self.assertNotEqual(updated['fingerprint'], preview['fingerprint'])
            self.assertGreater(updated['lines'][0]['performance_basis_fen'], 0)
            final = salary.confirm(salary.current_text)
            self.assertEqual(final['lines'][0]['performance_basis_fen'], updated['lines'][0]['performance_basis_fen'])
            salary.worker.is_active = False
            salary.worker.save(update_fields=['is_active'])
            closeout = {'run_id':final['run_id'], 'account_id':salary.worker.pk,
                        'settled_fen':final['lines'][0]['total_fen'], 'all_wages_settled':True,
                        'note':'核实此前工资已全部结清'}
            with ThreadPoolExecutor(max_workers=1) as pool:
                with transaction.atomic():
                    lock_payment_refund_events()
                    pending = pool.submit(self._post, store, salary.client, '/api/admin/payroll/separation-settlements/', closeout)
                    self._wait_on_barrier(pending)
                    PayrollLock.objects.select_for_update(nowait=True).get(pk=1)
                status, data = pending.result(timeout=20)
            self.assertEqual((status, data['code']), (200, 0), data)
            self.assertEqual(PayrollSeparationSettlement.objects.filter(account=salary.worker).count(), 1)
