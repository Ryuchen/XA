from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured

from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from club_accounts.services import get_or_create_account_for_legacy_user
from console import test_payroll_late_activity as fixture_module
from console.models import AdminMembership
from console.payroll_calculation import refund_accounting_enabled
from console.payroll_models import (PayrollRefundApplication, PayrollRefundDeduction, PayrollRefundWriteOff,
                                    PayrollRefundPerformanceAdjustment, PayrollRefundPerformanceApplication, PayrollRun)
from console.payroll_refund_ledger import (attach_refund_deductions, capture_confirmed_refunds,
                                           preview_refund_deductions, record_offline_separation_settlement)
from console.payroll_refund_performance import (capture_closed_period_refund_performance,
    closed_period_refund_performance, monthly_payment_refund_events, monthly_performance,
    monthly_performance_bases)
from orders.models import Order, OrderRefund, OrderStatusLog
from orders.tests.factories import make_console_user, make_order, make_user
from orders.tests import test_negotiation_execution
from stores.db import transaction
from wallet.models import Transaction
from wallet.services import get_wallet


class PayrollRefundConfigurationTest(SimpleTestCase):
    @override_settings(PAYROLL_REFUND_ACCOUNTING_ENABLED=None, PAYROLL_REFUND_ACCOUNTING_INTERNAL_TESTING=False)
    def test_deployable_flag_and_invalid_value(self):
        with patch.dict('os.environ', {'PAYROLL_REFUND_ACCOUNTING_ENABLED': 'true'}):
            self.assertTrue(refund_accounting_enabled())
        with patch.dict('os.environ', {'PAYROLL_REFUND_ACCOUNTING_ENABLED': 'false'}):
            self.assertFalse(refund_accounting_enabled())
        with patch.dict('os.environ', {'PAYROLL_REFUND_ACCOUNTING_ENABLED': 'invalid'}):
            with self.assertRaises(ImproperlyConfigured):
                refund_accounting_enabled()


@skipUnless(connection.vendor == 'mysql',
            '工资确认依赖 MySQL 工资事件屏障（18-盘点工资屏障待联调）；本模块同时作为 barrier_mysql 簇夹具')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], LICENSE_ACCOUNT_ADMISSION_ENABLED=False,
                   PAYROLL_REFUND_ACCOUNTING_INTERNAL_TESTING=True, PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True)
class PayrollRefundLedgerTest(TestCase):
    def setUp(self):
        self.f = fixture_module.LateActivityPayrollTest(methodName='test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms')
        self.f.setUp()
        self.f.plan.dispatch_day_bps = self.f.plan.dispatch_night_bps = 100
        self.f.plan.save(update_fields=['dispatch_day_bps', 'dispatch_night_bps'])
        self.f.assign(self.f.original_text)
        boss = make_user()
        self.order = make_order(boss, amount=15000, customer_account=get_or_create_account_for_legacy_user(boss),
                                payment_status=Order.PaymentStatus.PAID)
        Order.objects.filter(pk=self.order.pk).update(created_at=self.f.original_time)
        event = OrderStatusLog.objects.create(order=self.order, action='CREATE', operator_account=self.f.worker)
        OrderStatusLog.objects.filter(pk=event.pk).update(created_at=self.f.original_time)
        response = self.f.client.post('/api/admin/salary-activities/', {'kind': 'DISPATCH',
            'account': self.f.worker.pk, 'source_id': self.order.pk, 'reason': '核实派单'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.f.confirm(self.f.original_text)
        self.original_run = PayrollRun.objects.get(month=self.f.original)

    def _internal_confirm(self, month, gross):
        with transaction.atomic():
            plan = preview_refund_deductions(self.f.worker.pk, month, gross)
            run = PayrollRun.objects.create(month=month, confirmed_by=self.f.worker, fingerprint='a' * 64,
                snapshot={'lines': [{'account': self.f.worker.pk, 'total_fen': plan['net_fen'],
                                     'refund_deduction_plan': plan}]})
            self.assertEqual(attach_refund_deductions(run, self.f.worker.pk), plan)
            self.assertEqual(attach_refund_deductions(run, self.f.worker.pk), plan)
        return plan

    def test_each_refund_is_traced_and_deduction_carries_until_paid(self):
        first = OrderRefund.objects.create(order=self.order, amount=5000, reason='退款一')
        second = OrderRefund.objects.create(order=self.order, amount=10000, reason='退款二')
        rows = capture_confirmed_refunds(self.original_run.pk, self.f.worker.pk)
        self.assertEqual([(row.refund_id, row.amount_fen) for row in rows], [(first.pk, 50), (second.pk, 100)])
        capture_confirmed_refunds(self.original_run.pk, self.f.worker.pk)
        self.assertEqual(PayrollRefundDeduction.objects.count(), 2)

        original = self.original_run.snapshot
        plan = self._internal_confirm(self.f.current, 100)
        self.assertEqual((plan['gross_fen'], plan['deducted_fen'], plan['net_fen'], plan['carry_fen']),
                         (100, 100, 0, 50))
        next_month = (self.f.current.replace(day=28) + timedelta(days=4)).replace(day=1)
        next_plan = self._internal_confirm(next_month, 100)
        self.assertEqual((next_plan['deducted_fen'], next_plan['net_fen'], next_plan['carry_fen']), (50, 50, 0))
        self.assertEqual(sum(PayrollRefundApplication.objects.values_list('amount_fen', flat=True)), 150)
        self.original_run.refresh_from_db()
        self.assertEqual(self.original_run.snapshot, original)

    def test_post_confirmation_refunds_are_rounded_cumulatively(self):
        # Original confirmed day commission is rounded on the whole 15000-fen bucket.
        first = OrderRefund.objects.create(order=self.order, amount=49, reason='小额退款一')
        second = OrderRefund.objects.create(order=self.order, amount=51, reason='小额退款二')
        rows = capture_confirmed_refunds(self.original_run.pk, self.f.worker.pk)
        self.assertEqual([(row.refund_id, row.amount_fen) for row in rows], [(first.pk, 0), (second.pk, 1)])

    def test_offline_separation_closeout_forgives_remaining_and_later_old_refund_commission(self):
        OrderRefund.objects.create(order=self.order, amount=10000, reason='退款一')
        capture_confirmed_refunds(self.original_run.pk, self.f.worker.pk)
        plan = self._internal_confirm(self.f.current, 50)
        self.assertEqual((plan['net_fen'], plan['carry_fen']), (0, 50))
        final_run = PayrollRun.objects.get(month=self.f.current)
        self.f.worker.is_active = False
        self.f.worker.save(update_fields=['is_active'])
        operator_user = make_console_user(['salary:confirm'])
        operator = get_or_create_account_for_legacy_user(operator_user)
        AdminMembership.objects.filter(user=operator_user).update(account=operator)
        kwargs = {'run_id': final_run.pk, 'account_id': self.f.worker.pk, 'actor': operator,
                  'settled_fen': 0, 'reference': '', 'note': '核实此前工资已全部结清', 'all_wages_settled': True}
        settlement = record_offline_separation_settlement(**kwargs)
        self.assertEqual(record_offline_separation_settlement(**kwargs).pk, settlement.pk)
        self.assertEqual(list(PayrollRefundWriteOff.objects.values_list('amount_fen', flat=True)), [50])
        OrderRefund.objects.create(order=self.order, amount=5000, reason='离职后核实的旧单退款')
        capture_confirmed_refunds(self.original_run.pk, self.f.worker.pk)
        self.assertEqual(sum(PayrollRefundWriteOff.objects.values_list('amount_fen', flat=True)), 100)
        next_month = (self.f.current.replace(day=28) + timedelta(days=4)).replace(day=1)
        self.assertEqual(preview_refund_deductions(self.f.worker.pk, next_month, 100)['deducted_fen'], 0)

    def test_positive_offline_settlement_does_not_require_reference(self):
        self._internal_confirm(self.f.current, 200)
        self.f.worker.is_active = False
        self.f.worker.save(update_fields=['is_active'])
        operator_user = make_console_user(['salary:confirm'])
        operator = get_or_create_account_for_legacy_user(operator_user)
        AdminMembership.objects.filter(user=operator_user).update(account=operator)
        settlement = record_offline_separation_settlement(run_id=PayrollRun.objects.get(month=self.f.current).pk,
            account_id=self.f.worker.pk, actor=operator, settled_fen=200, reference='',
            note='已核实工资结清', all_wages_settled=True)
        self.assertEqual((settlement.settled_fen, settlement.reference), (200, ''))

    def test_separation_cannot_skip_later_assignment_or_confirmed_wages(self):
        self._internal_confirm(self.f.current, 200)
        next_month = (self.f.current.replace(day=28) + timedelta(days=4)).replace(day=1)
        self.f.assign(next_month.strftime('%Y-%m'))
        self.f.worker.is_active = False
        self.f.worker.save(update_fields=['is_active'])
        operator_user = make_console_user(['salary:confirm'])
        operator = get_or_create_account_for_legacy_user(operator_user)
        AdminMembership.objects.filter(user=operator_user).update(account=operator)
        kwargs = {'run_id': PayrollRun.objects.get(month=self.f.current).pk,
                  'account_id': self.f.worker.pk, 'actor': operator, 'settled_fen': 200,
                  'reference': '', 'note': '核实工资结清', 'all_wages_settled': True}
        with self.assertRaisesRegex(ValidationError, '之后仍有薪资安排'):
            record_offline_separation_settlement(**kwargs)
        from console.payroll_models import SalaryAssignment, PayrollSeparationSettlement
        SalaryAssignment.objects.filter(account=self.f.worker, month=next_month).delete()
        PayrollRun.objects.create(month=next_month, confirmed_by=operator, fingerprint='b' * 64,
            snapshot={'lines': [{'account': self.f.worker.pk, 'total_fen': 100}]})
        with self.assertRaisesRegex(ValidationError, '之后仍有已确认工资'):
            record_offline_separation_settlement(**kwargs)
        self.assertFalse(PayrollSeparationSettlement.objects.filter(account=self.f.worker).exists())
        self.assertFalse(PayrollRefundWriteOff.objects.exists())

    def test_refund_after_final_confirmation_must_be_accounted_before_closeout(self):
        self._internal_confirm(self.f.current, 200)
        OrderRefund.objects.create(order=self.order, amount=10000, reason='最终工资确认后退款')
        self.f.worker.is_active = False
        self.f.worker.save(update_fields=['is_active'])
        operator_user = make_console_user(['salary:confirm'])
        operator = get_or_create_account_for_legacy_user(operator_user)
        AdminMembership.objects.filter(user=operator_user).update(account=operator)
        with self.assertRaisesRegex(ValidationError, '最终工资确认后出现退款提成调整'):
            record_offline_separation_settlement(
                run_id=PayrollRun.objects.get(month=self.f.current).pk, account_id=self.f.worker.pk,
                actor=operator, settled_fen=200, reference='', note='核实工资结清', all_wages_settled=True)
        self.assertFalse(PayrollRefundDeduction.objects.exists())
        self.assertFalse(PayrollRefundWriteOff.objects.exists())


@skipUnless(connection.vendor == 'mysql',
            '工资确认依赖 MySQL 工资事件屏障（18-盘点工资屏障待联调）；本模块同时作为 barrier_mysql 簇夹具')
@override_settings(LICENSE_ACCOUNT_ADMISSION_ENABLED=False, NEGOTIATION_PROPOSAL_ENABLED=True, REFUND_DEPOSIT_DEFICIT_ENABLED=True,
                   PAYROLL_REFUND_ACCOUNTING_INTERNAL_TESTING=True, PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class PayrollActualRefundIntegrationTest(TestCase):
    def test_real_consensual_refund_produces_source_for_internal_ledger(self):
        salary = fixture_module.LateActivityPayrollTest(methodName='test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms')
        salary.setUp()
        salary.plan.dispatch_day_bps = salary.plan.dispatch_night_bps = 100
        salary.plan.save(update_fields=['dispatch_day_bps', 'dispatch_night_bps'])
        salary.assign(salary.original_text)
        flow = test_negotiation_execution.NegotiationExecutionPostingTests(
            methodName='test_posts_boss_refund_wallet_and_cash_deposit_once_in_one_transaction')
        flow.setUp()
        Order.objects.filter(pk=flow.order.pk).update(created_at=salary.original_time)
        event = OrderStatusLog.objects.create(order=flow.order, action='CREATE', operator_account=salary.worker)
        OrderStatusLog.objects.filter(pk=event.pk).update(created_at=salary.original_time)
        response = salary.client.post('/api/admin/salary-activities/', {'kind': 'DISPATCH',
            'account': salary.worker.pk, 'source_id': flow.order.pk, 'reason': '核实真实派单'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        Transaction.objects.filter(order=flow.order, tx_type='PAY').update(created_at=salary.original_time)
        salary.confirm(salary.original_text)
        run = PayrollRun.objects.get(month=salary.original)
        salary.plan.performance_fen, salary.plan.target_fen = 10000, 10000
        salary.plan.save(update_fields=['performance_fen', 'target_fen'])
        salary.assign(salary.current_text)
        current_order = make_order(flow.boss, amount=500, customer_account=flow.boss_account,
                                   payment_status=Order.PaymentStatus.PAID)
        wallet = get_wallet(user=flow.boss, account=flow.boss_account)
        wallet.balance = 10000
        wallet.save(update_fields=['balance'])
        Transaction.objects.create(wallet=wallet, order=current_order, tx_type='PAY', amount=-500,
                                   balance_before=10500, balance_after=10000)
        OrderStatusLog.objects.create(order=current_order, action='CREATE', operator_account=salary.worker)
        response = salary.client.post('/api/admin/salary-activities/', {'kind': 'DISPATCH',
            'account': salary.worker.pk, 'source_id': current_order.pk, 'reason': '核实本月派单'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        salary.confirm(salary.current_text)
        current_run = PayrollRun.objects.get(month=salary.current)
        case, proposal = flow.accepted(1000)
        receipt = flow.execute(case, proposal)
        self.assertEqual(receipt.customer_transaction.amount, 1000)
        rows = capture_confirmed_refunds(run.pk, salary.worker.pk)
        self.assertEqual([(row.refund_id, row.amount_fen) for row in rows], [(receipt.refund_id, 10)])
        next_month = (salary.current.replace(day=28) + timedelta(days=4)).replace(day=1)
        plan = preview_refund_deductions(salary.worker.pk, next_month, 100)
        self.assertEqual((plan['deducted_fen'], plan['net_fen'], plan['carry_fen']), (100, 0, 410))
        self.assertEqual(PayrollRefundPerformanceAdjustment.objects.count(), 0)
        original_events = monthly_payment_refund_events(salary.original)
        current_events = monthly_payment_refund_events(salary.current)
        self.assertEqual([(item['kind'], item['amount_fen']) for item in original_events], [('PAYMENT', 10000)])
        self.assertEqual([(item['kind'], item['amount_fen']) for item in current_events],
                         [('PAYMENT', 500), ('REFUND', -1000)])
        monthly = monthly_performance_bases(salary.current)
        self.assertEqual((monthly['store_fen'], monthly['personal_fen'][salary.worker.pk]), (-500, -500))
        personal_plan = current_run.snapshot['lines'][0]['plan_snapshot']
        self.assertEqual(monthly_performance(salary.current, salary.worker.pk, personal_plan),
                         {'basis_fen': -500, 'performance_fen': 0})
        self.assertEqual(monthly_performance(next_month, salary.worker.pk, personal_plan),
                         {'basis_fen': 0, 'performance_fen': 0})
        deltas = closed_period_refund_performance(current_run.pk)
        self.assertEqual([(item['refund_id'], item['adjustment_fen']) for item in deltas], [(receipt.refund_id, -500)])
        first = capture_closed_period_refund_performance(current_run.pk)
        second = capture_closed_period_refund_performance(current_run.pk)
        self.assertEqual([row.pk for row in first], [row.pk for row in second])
        self.assertEqual(PayrollRefundPerformanceAdjustment.objects.count(), 1)
        self.assertEqual(PayrollRun.objects.get(pk=current_run.pk).snapshot['lines'][0]['performance_fen'], 500)
        with transaction.atomic():
            combined = preview_refund_deductions(salary.worker.pk, next_month, 100)
            self.assertEqual((combined['deducted_fen'], combined['net_fen'], combined['carry_fen']), (100, 0, 410))
            self.assertEqual([item['kind'] for item in combined['entries']], ['COMMISSION', 'PERFORMANCE'])
            next_run = PayrollRun.objects.create(month=next_month, confirmed_by=salary.worker, fingerprint='b' * 64,
                snapshot={'lines': [{'account': salary.worker.pk, 'total_fen': combined['net_fen'],
                                     'refund_deduction_plan': combined}]})
            attach_refund_deductions(next_run, salary.worker.pk)
            attach_refund_deductions(next_run, salary.worker.pk)
        self.assertEqual(PayrollRefundPerformanceApplication.objects.get().amount_fen, 90)

    @override_settings(PAYROLL_REFUND_ACCOUNTING_ENABLED=True)
    def test_internal_payroll_preview_and_confirm_apply_real_refund_and_carry(self):
        salary = fixture_module.LateActivityPayrollTest(methodName='test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms')
        salary.setUp()
        salary.plan.base_fen = 0
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
        original = salary.confirm(salary.original_text)

        salary.plan.performance_fen, salary.plan.target_fen = 10000, 10000
        salary.plan.save(update_fields=['performance_fen', 'target_fen'])
        salary.assign(salary.current_text)
        current_order = make_order(flow.boss, amount=500, customer_account=flow.boss_account,
                                   payment_status=Order.PaymentStatus.PAID)
        wallet = get_wallet(user=flow.boss, account=flow.boss_account)
        wallet.balance = 10000; wallet.save(update_fields=['balance'])
        Transaction.objects.create(wallet=wallet, order=current_order, tx_type='PAY', amount=-500,
                                   balance_before=10500, balance_after=10000)
        OrderStatusLog.objects.create(order=current_order, action='CREATE', operator_account=salary.worker)
        response = salary.client.post('/api/admin/salary-activities/', {'kind': 'DISPATCH',
            'account': salary.worker.pk, 'source_id': current_order.pk, 'reason': '核实本月派单'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        case, proposal = flow.accepted(1000)
        receipt = flow.execute(case, proposal)

        preview = salary.preview(salary.current_text)
        line = preview['lines'][0]
        self.assertEqual((line['performance_basis_fen'], line['performance_fen']), (-500, 0))
        self.assertEqual((line['refund_deduction_plan']['gross_fen'], line['total_fen'],
                          line['refund_deduction_plan']['carry_fen']), (5, 0, 5))
        self.assertEqual(PayrollRefundDeduction.objects.count(), 0)
        confirmed = salary.confirm(salary.current_text)
        self.assertEqual(confirmed['lines'][0]['total_fen'], 0)
        self.assertEqual(PayrollRefundApplication.objects.get().amount_fen, 5)
        self.assertEqual(PayrollRefundDeduction.objects.get().refund_id, receipt.refund_id)
        self.assertEqual(salary.preview(salary.original_text)['totals'], original['totals'])

        next_month = (salary.current.replace(day=28) + timedelta(days=4)).replace(day=1)
        salary.plan.base_fen = 100
        salary.plan.save(update_fields=['base_fen'])
        salary.assign(next_month.strftime('%Y-%m'))
        next_result = salary.confirm(next_month.strftime('%Y-%m'))
        self.assertEqual((next_result['lines'][0]['total_fen'],
                          next_result['lines'][0]['refund_deduction_plan']['carry_fen']), (95, 0))
        self.assertEqual(sum(PayrollRefundApplication.objects.values_list('amount_fen', flat=True)), 10)

    @override_settings(PAYROLL_REFUND_ACCOUNTING_ENABLED=True)
    def test_refund_after_closed_month_adjusts_next_payroll_without_changing_old_run(self):
        salary = fixture_module.LateActivityPayrollTest(methodName='test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms')
        salary.setUp()
        salary.plan.base_fen = 0
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
        salary.plan.performance_fen, salary.plan.target_fen = 10000, 10000
        salary.plan.save(update_fields=['performance_fen', 'target_fen'])
        salary.assign(salary.current_text)
        current_order = make_order(flow.boss, amount=500, customer_account=flow.boss_account,
                                   payment_status=Order.PaymentStatus.PAID)
        wallet = get_wallet(user=flow.boss, account=flow.boss_account)
        wallet.balance = 10000; wallet.save(update_fields=['balance'])
        Transaction.objects.create(wallet=wallet, order=current_order, tx_type='PAY', amount=-500,
                                   balance_before=10500, balance_after=10000)
        OrderStatusLog.objects.create(order=current_order, action='CREATE', operator_account=salary.worker)
        response = salary.client.post('/api/admin/salary-activities/', {'kind': 'DISPATCH',
            'account': salary.worker.pk, 'source_id': current_order.pk, 'reason': '核实本月派单'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        closed = salary.confirm(salary.current_text)
        self.assertEqual(closed['lines'][0]['performance_fen'], 500)
        case, proposal = flow.accepted(1000)
        receipt = flow.execute(case, proposal)

        next_month = (salary.current.replace(day=28) + timedelta(days=4)).replace(day=1)
        salary.plan.base_fen = 600; salary.plan.save(update_fields=['base_fen'])
        salary.assign(next_month.strftime('%Y-%m'))
        preview = salary.preview(next_month.strftime('%Y-%m'))
        self.assertEqual((preview['lines'][0]['total_fen'], preview['lines'][0]['refund_deduction_plan']['deducted_fen']), (90, 510))
        self.assertEqual((PayrollRefundDeduction.objects.count(), PayrollRefundPerformanceAdjustment.objects.count()), (0, 0))
        next_result = salary.confirm(next_month.strftime('%Y-%m'))
        self.assertEqual(next_result['lines'][0]['total_fen'], 90)
        self.assertEqual(PayrollRefundApplication.objects.get().deduction.refund_id, receipt.refund_id)
        self.assertEqual(PayrollRefundPerformanceApplication.objects.get().adjustment.refund_id, receipt.refund_id)
        replay = salary.client.post('/api/admin/payroll/confirm/', {'month': next_month.strftime('%Y-%m'),
                                    'fingerprint': preview['fingerprint']}, format='json')
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual((PayrollRefundApplication.objects.count(), PayrollRefundPerformanceApplication.objects.count()), (1, 1))
        self.assertEqual(salary.preview(salary.current_text)['totals'], closed['totals'])

    @override_settings(PAYROLL_REFUND_ACCOUNTING_ENABLED=True)
    def test_disabled_employee_final_payroll_and_offline_closeout_through_api(self):
        salary = fixture_module.LateActivityPayrollTest(methodName='test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms')
        salary.setUp()
        salary.plan.base_fen = 0
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
        salary.worker.is_active = False
        salary.worker.save(update_fields=['is_active'])
        salary.plan.base_fen = 20; salary.plan.save(update_fields=['base_fen'])
        salary.assign(salary.current_text)
        case, proposal = flow.accepted(4000)
        receipt = flow.execute(case, proposal)
        final = salary.confirm(salary.current_text)
        self.assertEqual((final['lines'][0]['total_fen'], final['lines'][0]['refund_deduction_plan']['carry_fen']), (0, 20))
        body = {'run_id': final['run_id'], 'account_id': salary.worker.pk, 'settled_fen': 0,
                'all_wages_settled': True}
        url = '/api/admin/payroll/separation-settlements/'
        closed = salary.client.post(url, body, format='json')
        self.assertEqual(closed.status_code, 200, closed.data)
        self.assertEqual(closed.data['data']['waived_fen'], 20)
        self.assertEqual(salary.client.post(url, body, format='json').data['data']['id'], closed.data['data']['id'])
        self.assertEqual(PayrollRefundWriteOff.objects.get().deduction.refund_id, receipt.refund_id)
