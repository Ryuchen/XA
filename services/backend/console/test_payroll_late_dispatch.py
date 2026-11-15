from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from console import test_payroll_late_activity as salary_fixture
from console.payroll_models import PayrollRun
from orders.models import Order, OrderStatusLog, OrderOriginalAttribution, OrderParticipationSegment
from orders.tests import test_negotiation_execution
from orders.tests.factories import make_order, make_user
from club_accounts.services import get_or_create_account_for_legacy_user
from wallet.models import Transaction
from wallet.services import get_wallet


@override_settings(PAYROLL_REFUND_ACCOUNTING_INTERNAL_TESTING=True, NEGOTIATION_PROPOSAL_ENABLED=True,
                   REFUND_DEPOSIT_DEFICIT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class LateDispatchIntegrationTest(TestCase):
    def setUp(self):
        self.salary = salary_fixture.LateActivityPayrollTest(
            methodName='test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms')
        self.salary.setUp()
        self.salary.plan.base_fen = 0
        self.salary.plan.dispatch_day_bps = self.salary.plan.dispatch_night_bps = 1000
        self.salary.plan.save(update_fields=['base_fen', 'dispatch_day_bps', 'dispatch_night_bps'])
        self.salary.assign(self.salary.original_text)
        self.original = self.salary.confirm(self.salary.original_text)
        self.salary.assign(self.salary.current_text)
        self.flow = test_negotiation_execution.NegotiationExecutionPostingTests(
            methodName='test_posts_boss_refund_wallet_and_cash_deposit_once_in_one_transaction')
        self.flow.setUp()
        Order.objects.filter(pk=self.flow.order.pk).update(created_at=self.salary.original_time)
        event = OrderStatusLog.objects.create(order=self.flow.order, action='CREATE', operator_account=self.salary.worker)
        OrderStatusLog.objects.filter(pk=event.pk).update(created_at=self.salary.original_time)
        Transaction.objects.filter(order=self.flow.order, tx_type='PAY').update(created_at=self.salary.original_time)

    def register(self):
        response = self.salary.client.post('/api/admin/salary-activities/', {
            'kind': 'DISPATCH', 'account': self.salary.worker.pk, 'source_id': self.flow.order.pk,
            'reason': '补核上月派单', 'client_request_id': 'late-dispatch-1'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data['data']

    def refund(self, amount):
        case, proposal = self.flow.accepted(amount)
        return self.flow.execute(case, proposal)

    def test_refund_before_late_pay_reduces_unpaid_commission(self):
        activity = self.register()
        self.assertEqual(activity['source_snapshot']['late_compensation']['amount_fen'], 1000)
        self.refund(1000)
        preview = self.salary.preview(self.salary.current_text)
        late = preview['lines'][0]['late_adjustments'][0]
        self.assertEqual((late['commission_basis_fen'], late['commission_fen'], late['amount_fen']),
                         (9000, 900, 900))
        result = self.salary.confirm(self.salary.current_text)
        self.assertEqual(result['totals']['late_adjustments_fen'], 900)
        self.assertEqual(PayrollRun.objects.get(month=self.salary.original).snapshot['totals'], self.original['totals'])

    def test_refund_after_late_pay_is_deducted_from_next_payroll(self):
        self.register()
        result = self.salary.confirm(self.salary.current_text)
        self.assertEqual(result['totals']['late_adjustments_fen'], 1000)
        receipt = self.refund(1000)
        self.salary.plan.base_fen = 1000
        self.salary.plan.save(update_fields=['base_fen'])
        next_month = (self.salary.current.replace(day=28) + timedelta(days=4)).replace(day=1)
        next_text = next_month.strftime('%Y-%m')
        self.salary.assign(next_text)
        preview = self.salary.preview(next_text)
        plan = preview['lines'][0]['refund_deduction_plan']
        self.assertEqual((plan['gross_fen'], plan['deducted_fen'], plan['net_fen']), (1000, 100, 900))
        self.assertEqual(plan['entries'][0]['refund_id'], receipt.refund_id)
        self.assertEqual(self.salary.confirm(next_text)['totals']['total_fen'], 900)

    def test_dispatch_before_month_end_but_paid_next_month_uses_payment_month(self):
        self.salary.plan.dispatch_day_bps = self.salary.plan.dispatch_night_bps = 2000
        self.salary.plan.save(update_fields=['dispatch_day_bps', 'dispatch_night_bps'])
        from console.payroll_models import SalaryAssignment
        assignment = SalaryAssignment.objects.get(account=self.salary.worker, month=self.salary.current)
        updated = self.salary.client.patch(f'/api/admin/salary-assignments/{assignment.pk}/',
            {'revision': assignment.revision, 'adopt_current_plan': True}, format='json')
        self.assertEqual(updated.status_code, 200, updated.data)
        Transaction.objects.filter(order=self.flow.order, tx_type='PAY').update(created_at=timezone.now())
        activity = self.register()
        self.assertIsNone(activity['recognition_month'])
        listing = self.salary.client.get('/api/admin/salary-activities/', {'month': self.salary.current_text})
        self.assertIn(activity['id'], [item['id'] for item in listing.data['data']['list']])
        previous = self.salary.client.get('/api/admin/salary-activities/', {'month': self.salary.original_text})
        self.assertNotIn(activity['id'], [item['id'] for item in previous.data['data']['list']])
        removed = self.salary.client.delete(f"/api/admin/salary-activities/{activity['id']}/")
        self.assertEqual(removed.status_code, 200, removed.data)
        recorded = self.salary.client.post('/api/admin/salary-activities/', {
            'kind': 'DISPATCH', 'account': self.salary.worker.pk, 'source_id': self.flow.order.pk,
            'reason': '重新核实本月派单', 'client_request_id': 'retry-after-void'}, format='json')
        self.assertEqual(recorded.status_code, 201, recorded.data)
        current = self.salary.confirm(self.salary.current_text)
        self.assertEqual(current['totals']['dispatch_commission_fen'], 2000)
        self.assertEqual(current['totals']['late_adjustments_fen'], 0)
        self.salary.worker.is_active = False
        self.salary.worker.save(update_fields=['is_active'])
        closed = self.salary.client.post('/api/admin/payroll/separation-settlements/', {
            'run_id': current['run_id'], 'account_id': self.salary.worker.pk,
            'settled_fen': current['totals']['total_fen'], 'all_wages_settled': True}, format='json')
        self.assertEqual(closed.status_code, 200, closed.data)

    def test_prior_month_dispatch_paid_this_month_requires_ownership_before_confirmation(self):
        Transaction.objects.filter(order=self.flow.order, tx_type='PAY').update(created_at=timezone.now())
        preview = self.salary.preview(self.salary.current_text)
        self.assertEqual(preview['unattributed']['dispatch']['count'], 1)
        self.assertFalse(preview['can_confirm'])
        self.register()
        preview = self.salary.preview(self.salary.current_text)
        self.assertEqual(preview['unattributed']['dispatch']['count'], 0)
        self.assertTrue(preview['can_confirm'], preview['warnings'])
        self.assertEqual(self.salary.confirm(self.salary.current_text)['totals']['dispatch_commission_fen'], 1000)

    def test_fully_refunded_paid_order_remains_available_for_late_attribution(self):
        wallet = get_wallet(user=self.flow.a, account=self.flow.a_account)
        wallet.balance = 6000
        wallet.save(update_fields=['balance'])
        self.refund(10000)
        sources = self.salary.client.get('/api/admin/payroll/sources/',
            {'kind': 'DISPATCH', 'source_id': self.flow.order.pk})
        self.assertEqual(sources.status_code, 200, sources.data)
        self.assertEqual(sources.data['data']['list'][0]['source_amount_fen'], 0)
        self.register()
        preview = self.salary.preview(self.salary.current_text)
        self.assertEqual(preview['totals']['late_adjustments_fen'], 0)
        self.assertEqual(self.salary.confirm(self.salary.current_text)['totals']['total_fen'], 0)

    def test_refund_in_next_month_before_payroll_confirmation_stays_in_next_month(self):
        self.register()
        next_month = (self.salary.current.replace(day=28) + timedelta(days=4)).replace(day=1)
        next_time = timezone.make_aware(timezone.datetime.combine(next_month.replace(day=5), timezone.datetime.min.time()))
        with patch('django.utils.timezone.now', return_value=next_time):
            receipt = self.refund(1000)
            type(receipt.refund).objects.filter(pk=receipt.refund_id).update(created_at=next_time)
            self.assertEqual(self.salary.confirm(self.salary.current_text)['totals']['late_adjustments_fen'], 1000)
            self.salary.plan.base_fen = 1000
            self.salary.plan.save(update_fields=['base_fen'])
            next_text = next_month.strftime('%Y-%m')
            self.salary.assign(next_text)
            plan = self.salary.preview(next_text)['lines'][0]['refund_deduction_plan']
            self.assertEqual((plan['deducted_fen'], plan['net_fen']), (100, 900))
            self.assertEqual(plan['entries'][0]['refund_id'], receipt.refund_id)

    def test_refund_before_confirm_is_attributed_after_late_dispatch_verification(self):
        from console.payroll_models import SalaryAssignment
        self.salary.plan.base_fen = 1000
        self.salary.plan.performance_fen = self.salary.plan.target_fen = 10000
        self.salary.plan.save(update_fields=['base_fen', 'performance_fen', 'target_fen'])
        assignment = SalaryAssignment.objects.get(account=self.salary.worker, month=self.salary.current)
        adopted = self.salary.client.patch(f'/api/admin/salary-assignments/{assignment.pk}/',
            {'revision': assignment.revision, 'adopt_current_plan': True}, format='json')
        self.assertEqual(adopted.status_code, 200, adopted.data)

        buyer = make_user()
        account = get_or_create_account_for_legacy_user(buyer)
        order = make_order(buyer, amount=5000, customer_account=account, payment_status='PAID')
        wallet = get_wallet(user=buyer, account=account)
        Transaction.objects.create(wallet=wallet, order=order, tx_type='PAY', amount=-5000,
                                   balance_before=5000, balance_after=0)
        OrderStatusLog.objects.create(order=order, action='CREATE', operator_account=self.salary.worker)
        recorded = self.salary.client.post('/api/admin/salary-activities/', {'kind': 'DISPATCH',
            'account': self.salary.worker.pk, 'source_id': order.pk, 'reason': '核实本月派单'}, format='json')
        self.assertEqual(recorded.status_code, 201, recorded.data)
        self.refund(1000)  # owner of the older order is not verified yet
        confirmed = self.salary.confirm(self.salary.current_text)
        self.assertEqual(confirmed['totals']['performance_fen'], 5000)

        self.register()  # attribute the older order after this payroll was confirmed
        next_month = (self.salary.current.replace(day=28) + timedelta(days=4)).replace(day=1)
        next_text = next_month.strftime('%Y-%m')
        self.salary.assign(next_text)
        preview = self.salary.preview(next_text)
        line = preview['lines'][0]
        self.assertEqual(line['late_adjustments_fen'], 900)
        self.assertEqual(line['refund_deduction_plan']['deducted_fen'], 1000)
        self.assertEqual(self.salary.confirm(next_text)['totals']['total_fen'], 900)


@override_settings(PAYROLL_REFUND_ACCOUNTING_INTERNAL_TESTING=True, NEGOTIATION_PROPOSAL_ENABLED=True,
                   REFUND_DEPOSIT_DEFICIT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class LateDispatchOriginalMonthPerformanceTest(TestCase):
    def small_flow(self, salary):
        flow = test_negotiation_execution.NegotiationExecutionPostingTests(
            methodName='test_posts_boss_refund_wallet_and_cash_deposit_once_in_one_transaction')
        flow.setUp()
        Order.objects.filter(pk=flow.order.pk).update(amount=10, original_amount=10, provider_income=8, shop_income=2)
        flow.order.refresh_from_db()
        flow.order.providers.update(settlement_base=5, provider_income=4)
        attribution = OrderOriginalAttribution.objects.get(order=flow.order)
        attribution.original_amount = 10
        attribution.save(update_fields=['original_amount'])
        OrderParticipationSegment.objects.filter(attribution=attribution).update(original_share=5)
        Transaction.objects.filter(order=flow.order, tx_type='PAY').update(
            amount=-10, balance_before=9010, balance_after=9000, created_at=salary.original_time)
        event = OrderStatusLog.objects.create(order=flow.order, action='CREATE', operator_account=salary.worker)
        OrderStatusLog.objects.filter(pk=event.pk).update(created_at=salary.original_time)
        response = salary.client.post('/api/admin/salary-activities/', {'kind': 'DISPATCH',
            'account': salary.worker.pk, 'source_id': flow.order.pk, 'reason': '补核原月派单'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return flow

    def refund_small(self, flow):
        from orders.negotiation_proposals import create, decide
        wallet = get_wallet(user=flow.a, account=flow.a_account)
        wallet.balance = 6
        wallet.save(update_fields=['balance'])
        case = flow.case()
        proposal = create(case.pk, actor=flow.staff, request_id='small-refund', expected_version=0,
                          refund_units=10, provider_terms=[{'account_id': flow.a_account.pk, 'retained_units': 0}],
                          explanation='全额退款')
        decide(case.pk, actor=flow.boss_account, proposal_id=proposal.pk, accepted=True)
        decide(case.pk, actor=flow.a_account, proposal_id=proposal.pk, accepted=True)
        flow.execute(case, proposal)

    def test_original_month_refund_does_not_double_deduct_late_performance(self):
        salary = salary_fixture.LateActivityPayrollTest(
            methodName='test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms')
        salary.setUp()
        salary.plan.base_fen = 0
        salary.plan.performance_fen = salary.plan.target_fen = 10000
        salary.plan.dispatch_day_bps = salary.plan.dispatch_night_bps = 1000
        salary.plan.save(update_fields=['base_fen', 'performance_fen', 'target_fen',
                                        'dispatch_day_bps', 'dispatch_night_bps'])
        salary.assign(salary.original_text)
        boss = make_user()
        account = get_or_create_account_for_legacy_user(boss)
        order = make_order(boss, amount=5000, customer_account=account, payment_status=Order.PaymentStatus.PAID)
        wallet = get_wallet(user=boss, account=account)
        paid = Transaction.objects.create(wallet=wallet, order=order, tx_type='PAY', amount=-5000,
                                           balance_before=5000, balance_after=0)
        Transaction.objects.filter(pk=paid.pk).update(created_at=salary.original_time)
        event = OrderStatusLog.objects.create(order=order, action='CREATE', operator_account=salary.worker)
        OrderStatusLog.objects.filter(pk=event.pk).update(created_at=salary.original_time)
        result = salary.client.post('/api/admin/salary-activities/', {'kind': 'DISPATCH',
            'account': salary.worker.pk, 'source_id': order.pk, 'reason': '核实本月派单'}, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        first = salary.confirm(salary.original_text)
        self.assertEqual(first['totals']['performance_fen'], 5000)
        PayrollRun.objects.filter(month=salary.original).update(confirmed_at=salary.original_time + timedelta(days=1))
        salary.assign(salary.current_text)

        flow = test_negotiation_execution.NegotiationExecutionPostingTests(
            methodName='test_posts_boss_refund_wallet_and_cash_deposit_once_in_one_transaction')
        flow.setUp()
        Transaction.objects.filter(order=flow.order, tx_type='PAY').update(created_at=salary.original_time)
        event = OrderStatusLog.objects.create(order=flow.order, action='CREATE', operator_account=salary.worker)
        OrderStatusLog.objects.filter(pk=event.pk).update(created_at=salary.original_time)
        response = salary.client.post('/api/admin/salary-activities/', {'kind': 'DISPATCH',
            'account': salary.worker.pk, 'source_id': flow.order.pk, 'reason': '补核原月派单'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        case, proposal = flow.accepted(1000)
        receipt = flow.execute(case, proposal)
        type(receipt.refund).objects.filter(pk=receipt.refund_id).update(created_at=salary.original_time + timedelta(days=2))
        preview = salary.preview(salary.current_text)
        late = preview['lines'][0]['late_adjustments'][0]
        self.assertEqual((late['commission_fen'], late['performance_fen']), (900, 5000))
        self.assertEqual(preview['lines'][0]['refund_deduction_plan']['deducted_fen'], 0)
        self.assertEqual(salary.confirm(salary.current_text)['totals']['total_fen'], 5900)

    def test_original_month_other_refund_is_not_deducted_twice_from_late_bonus(self):
        salary = salary_fixture.LateActivityPayrollTest(
            methodName='test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms')
        salary.setUp()
        salary.plan.base_fen = 0
        salary.plan.performance_fen = salary.plan.target_fen = 20000
        salary.plan.dispatch_day_bps = salary.plan.dispatch_night_bps = 1000
        salary.plan.save(update_fields=['base_fen', 'performance_fen', 'target_fen',
                                        'dispatch_day_bps', 'dispatch_night_bps'])
        salary.assign(salary.original_text)

        def make_flow():
            flow = test_negotiation_execution.NegotiationExecutionPostingTests(
                methodName='test_posts_boss_refund_wallet_and_cash_deposit_once_in_one_transaction')
            flow.setUp()
            Transaction.objects.filter(order=flow.order, tx_type='PAY').update(created_at=salary.original_time)
            event = OrderStatusLog.objects.create(order=flow.order, action='CREATE', operator_account=salary.worker)
            OrderStatusLog.objects.filter(pk=event.pk).update(created_at=salary.original_time)
            return flow

        known = make_flow()
        response = salary.client.post('/api/admin/salary-activities/', {'kind': 'DISPATCH',
            'account': salary.worker.pk, 'source_id': known.order.pk, 'reason': '原月已核派单'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(salary.confirm(salary.original_text)['totals']['performance_fen'], 10000)
        PayrollRun.objects.filter(month=salary.original).update(confirmed_at=salary.original_time + timedelta(days=1))
        salary.assign(salary.current_text)
        case, proposal = known.accepted(1000)
        receipt = known.execute(case, proposal)
        type(receipt.refund).objects.filter(pk=receipt.refund_id).update(created_at=salary.original_time + timedelta(days=2))
        late = make_flow()
        response = salary.client.post('/api/admin/salary-activities/', {'kind': 'DISPATCH',
            'account': salary.worker.pk, 'source_id': late.order.pk, 'reason': '补核原月派单'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        preview = salary.preview(salary.current_text)
        self.assertEqual(preview['lines'][0]['late_adjustments'][0]['performance_fen'], 10000)
        self.assertEqual(preview['lines'][0]['refund_deduction_plan']['deducted_fen'], 1100)
        # Late commission 1,000 + late performance 10,000; known order refund
        # adjusts already-paid original performance by 1,000 and commission by 100.
        self.assertEqual(salary.confirm(salary.current_text)['totals']['total_fen'], 9900)

    def test_two_small_late_commissions_refund_by_cumulative_rounded_group(self):
        salary = salary_fixture.LateActivityPayrollTest(
            methodName='test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms')
        salary.setUp()
        salary.plan.base_fen = 0
        salary.plan.dispatch_day_bps = salary.plan.dispatch_night_bps = 500
        salary.plan.save(update_fields=['base_fen', 'dispatch_day_bps', 'dispatch_night_bps'])
        salary.assign(salary.original_text)
        salary.confirm(salary.original_text)
        salary.assign(salary.current_text)
        flows = [self.small_flow(salary), self.small_flow(salary)]
        confirmed = salary.confirm(salary.current_text)
        self.assertEqual([row['commission_fen'] for row in confirmed['lines'][0]['late_adjustments']], [1, 0])

        salary.plan.base_fen = 100
        salary.plan.save(update_fields=['base_fen'])
        next_month = (salary.current.replace(day=28) + timedelta(days=4)).replace(day=1)
        next_text = next_month.strftime('%Y-%m')
        salary.assign(next_text)
        self.refund_small(flows[0])
        self.assertEqual(salary.preview(next_text)['lines'][0]['refund_deduction_plan']['deducted_fen'], 0)
        self.refund_small(flows[1])
        plan = salary.preview(next_text)['lines'][0]['refund_deduction_plan']
        self.assertEqual((plan['deducted_fen'], plan['net_fen']), (1, 99))
        self.assertEqual(salary.confirm(next_text)['totals']['total_fen'], 99)

    def test_refund_before_confirmation_recalculates_later_late_commission_rounding(self):
        salary = salary_fixture.LateActivityPayrollTest(
            methodName='test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms')
        salary.setUp()
        salary.plan.base_fen = 0
        salary.plan.dispatch_day_bps = salary.plan.dispatch_night_bps = 500
        salary.plan.save(update_fields=['base_fen', 'dispatch_day_bps', 'dispatch_night_bps'])
        salary.assign(salary.original_text)
        salary.confirm(salary.original_text)
        salary.assign(salary.current_text)
        first, _ = self.small_flow(salary), self.small_flow(salary)
        self.refund_small(first)
        current = salary.confirm(salary.current_text)
        self.assertEqual([row['commission_fen'] for row in current['lines'][0]['late_adjustments']], [0, 1])
        self.assertEqual(current['totals']['late_adjustments_fen'], 1)
