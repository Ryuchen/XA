"""Real MySQL payroll/refund serialization against a disposable test schema."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from common.mysql_test_support import mysql_locking_statements, require_disposable_mysql
from console import payroll, test_payroll_late_activity as fixture_module
from console.payroll_models import PayrollRefundApplication
from orders.models import Order, OrderStatusLog
from orders.tests import test_negotiation_execution
from wallet.models import Transaction


@skipUnless(connection.vendor == 'mysql', 'Requires disposable MySQL and InnoDB row locks')
@override_settings(PAYROLL_REFUND_ACCOUNTING_ENABLED=True, NEGOTIATION_PROPOSAL_ENABLED=True,
    REFUND_DEPOSIT_DEFICIT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False, LICENSE_ACCOUNT_ADMISSION_ENABLED=False,
    ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class PayrollRefundMySQLTest(TransactionTestCase):
    def test_refund_waits_for_month_confirmation_and_is_deducted_next_period(self):
        require_disposable_mysql(connection)
        salary = fixture_module.LateActivityPayrollTest(methodName='test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms')
        salary.setUp()
        salary.plan.base_fen = 1000
        salary.plan.dispatch_day_bps = salary.plan.dispatch_night_bps = 100
        salary.plan.save(update_fields=['base_fen', 'dispatch_day_bps', 'dispatch_night_bps'])
        salary.assign(salary.original_text)
        flow = test_negotiation_execution.NegotiationExecutionPostingTests(
            methodName='test_posts_boss_refund_wallet_and_cash_deposit_once_in_one_transaction')
        flow.setUp()
        Order.objects.filter(pk=flow.order.pk).update(created_at=salary.original_time)
        event = OrderStatusLog.objects.create(order=flow.order, action='CREATE', operator_account=salary.worker)
        OrderStatusLog.objects.filter(pk=event.pk).update(created_at=salary.original_time)
        Transaction.objects.filter(order=flow.order, tx_type='PAY').update(created_at=salary.original_time)
        response = salary.client.post('/api/admin/salary-activities/', {'kind': 'DISPATCH',
            'account': salary.worker.pk, 'source_id': flow.order.pk, 'reason': '核实原月派单'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        salary.confirm(salary.original_text)
        salary.assign(salary.current_text)
        case, proposal = flow.accepted(1000)
        preview = salary.preview(salary.current_text)
        entered, release = Event(), Event()
        calculate = payroll.calculate_payroll

        def paused_calculation(month):
            entered.set()
            if not release.wait(15):
                raise RuntimeError('Timed out waiting to release payroll confirmation')
            return calculate(month)

        def confirm():
            close_old_connections()
            try:
                return salary.client.post('/api/admin/payroll/confirm/', {'month': salary.current_text,
                    'fingerprint': preview['fingerprint']}, format='json')
            finally:
                close_old_connections()

        def refund():
            close_old_connections()
            try:
                return flow.execute(case, proposal).refund_id
            finally:
                close_old_connections()

        with patch('console.payroll.calculate_payroll', side_effect=paused_calculation):
            with ThreadPoolExecutor(max_workers=2) as pool:
                confirmation = pool.submit(confirm)
                self.assertTrue(entered.wait(15), 'Payroll did not reach the locked calculation')
                pending = pool.submit(refund)
                try:
                    deadline = monotonic() + 8
                    while monotonic() < deadline and not mysql_locking_statements(connection, 'orders_order'):
                        sleep(0.05)
                    self.assertFalse(pending.done(), 'Refund should wait on the original order lock')
                    self.assertGreater(mysql_locking_statements(connection, 'orders_order'), 0)
                finally:
                    release.set()
                confirmed = confirmation.result(timeout=20)
                refund_id = pending.result(timeout=20)
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        self.assertEqual(confirmed.data['data']['lines'][0]['total_fen'], 1000)
        next_month = (salary.current.replace(day=28) + timedelta(days=4)).replace(day=1)
        salary.assign(next_month.strftime('%Y-%m'))
        following = salary.confirm(next_month.strftime('%Y-%m'))
        self.assertEqual(following['lines'][0]['refund_deduction_plan']['deducted_fen'], 10)
        self.assertEqual(PayrollRefundApplication.objects.get().deduction.refund_id, refund_id)
