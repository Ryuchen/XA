from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone

from console import test_payroll_late_activity as salary_fixture
from club_accounts.services import get_or_create_account_for_legacy_user
from console.dictionary_models import BusinessDictionaryItem
from console.models import AdminMembership
from console.payroll_models import PayrollRefundDeduction, PayrollRefundPerformanceAdjustment, SalaryPlan
from engagement.models import PointFundingLot
from orders.models import AfterSale, OrderStatusLog
from orders.tests import test_addon_refund_execution, test_addon_intents, test_negotiation_execution
from orders.tests.test_addon_configuration import staff_client
from orders.tests.factories import make_console_user, make_order, make_user
from wallet.models import Transaction
from wallet.services import get_wallet


@skipUnless(connection.vendor == 'mysql',
            '工资确认依赖 MySQL 工资事件屏障（18-盘点工资屏障待联调）；本模块同时作为 barrier_mysql 簇夹具')
@override_settings(PAYROLL_REFUND_ACCOUNTING_INTERNAL_TESTING=True, ADDON_PAYMENT_INTERNAL_TESTING=True,
                   ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True, PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class AddonPayrollFlowTest(TestCase):
    def setUp(self):
        self.salary = salary_fixture.LateActivityPayrollTest(
            methodName='test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms')
        self.salary.setUp()
        self.salary.plan.base_fen = 1000
        self.salary.plan.dispatch_day_bps = self.salary.plan.dispatch_night_bps = 1000
        self.salary.plan.performance_fen = 1000
        self.salary.plan.target_fen = 10000
        self.salary.plan.save(update_fields=['base_fen', 'dispatch_day_bps', 'dispatch_night_bps',
                                             'performance_fen', 'target_fen'])
        self.flow = test_addon_refund_execution.AddonRefundExecutionTests(
            methodName='test_confirmed_refunds_restore_original_sources_and_deduct_pending_earnings_once')
        self.flow.setUp()
        order = self.flow.fixture.fixture.order
        payment = Transaction.objects.create(wallet=self.flow.fixture.wallet, order=order, tx_type='PAY',
            amount=-order.amount, balance_before=order.amount, balance_after=0)
        Transaction.objects.filter(pk=payment.pk).update(created_at=self.salary.original_time)
        event = OrderStatusLog.objects.create(order=order, action='CREATE', operator_account=self.salary.worker)
        OrderStatusLog.objects.filter(pk=event.pk).update(created_at=self.salary.original_time)
        self.salary.assign(self.salary.original_text)
        result = self.salary.client.post('/api/admin/salary-activities/', {'kind': 'DISPATCH',
            'account': self.salary.worker.pk, 'source_id': order.pk, 'reason': '核实主单派单'}, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        self.salary.confirm(self.salary.original_text)
        self.salary.assign(self.salary.current_text)

    def test_addon_paid_then_refunded_after_payroll_confirmation_is_adjusted_next_period(self):
        current = self.salary.confirm(self.salary.current_text)
        line = current['lines'][0]
        self.assertEqual((line['dispatch_commission_fen'], line['performance_fen']), (150, 150))
        self.assertEqual(line['addon_sources'][0]['payment_id'], self.flow.fixture.payment.pk)
        execute_url = self.flow.approve('payroll-addon-refund', 500)
        response = self.flow.fixture.staff.post(execute_url, {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)

        next_month = (self.salary.current.replace(day=28) + timedelta(days=4)).replace(day=1)
        next_text = next_month.strftime('%Y-%m')
        self.salary.assign(next_text)
        preview = self.salary.preview(next_text)
        deduction = preview['lines'][0]['refund_deduction_plan']
        self.assertEqual((deduction['gross_fen'], deduction['deducted_fen'], deduction['net_fen']),
                         (1000, 100, 900))
        self.assertEqual({item['refund_origin'] for item in deduction['entries']}, {'ADDON'})
        result = self.salary.confirm(next_text)
        self.assertEqual(result['totals']['total_fen'], 900)
        self.assertEqual(PayrollRefundDeduction.objects.filter(addon_refund__isnull=False).count(), 1)
        self.assertEqual(PayrollRefundPerformanceAdjustment.objects.filter(addon_refund__isnull=False).count(), 1)
        self.assertEqual(self.salary.preview(self.salary.current_text)['lines'][0]['performance_fen'], 150)

    def test_refund_before_confirmation_reduces_addon_commission_and_performance_once(self):
        execute_url = self.flow.approve('payroll-before-confirm', 500)
        response = self.flow.fixture.staff.post(execute_url, {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        current = self.salary.confirm(self.salary.current_text)
        line = current['lines'][0]
        self.assertEqual((line['dispatch_commission_fen'], line['performance_fen']), (100, 100))
        self.assertEqual(line['addon_sources'][0]['net_fen'], 1000)
        next_month = (self.salary.current.replace(day=28) + timedelta(days=4)).replace(day=1)
        next_text = next_month.strftime('%Y-%m')
        self.salary.assign(next_text)
        preview = self.salary.preview(next_text)
        self.assertEqual(preview['lines'][0]['refund_deduction_plan']['deducted_fen'], 0)

    def test_prior_month_addon_refund_adjusts_current_closed_month_performance(self):
        payment = self.flow.fixture.payment
        payment.paid_at = self.salary.original_time
        payment.save(update_fields=['paid_at'])
        Transaction.objects.filter(pk=payment.buyer_transaction_id).update(created_at=self.salary.original_time)
        buyer = make_user()
        account = get_or_create_account_for_legacy_user(buyer)
        current_order = make_order(buyer, amount=5000, customer_account=account, payment_status='PAID')
        wallet = get_wallet(user=buyer, account=account)
        Transaction.objects.create(wallet=wallet, order=current_order, tx_type='PAY', amount=-5000,
                                   balance_before=5000, balance_after=0)
        OrderStatusLog.objects.create(order=current_order, action='CREATE', operator_account=self.salary.worker)
        activity = self.salary.client.post('/api/admin/salary-activities/', {'kind': 'DISPATCH',
            'account': self.salary.worker.pk, 'source_id': current_order.pk, 'reason': '核实本月派单'}, format='json')
        self.assertEqual(activity.status_code, 201, activity.data)
        current = self.salary.confirm(self.salary.current_text)
        self.assertEqual(current['totals']['performance_fen'], 500)
        execute_url = self.flow.approve('prior-month-addon-refund', 500)
        refund = self.flow.fixture.staff.post(execute_url, {}, format='json')
        self.assertEqual(refund.status_code, 200, refund.data)
        next_month = (self.salary.current.replace(day=28) + timedelta(days=4)).replace(day=1)
        next_text = next_month.strftime('%Y-%m')
        self.salary.assign(next_text)
        result = self.salary.confirm(next_text)
        plan = result['lines'][0]['refund_deduction_plan']
        self.assertEqual((plan['deducted_fen'], result['totals']['total_fen']), (100, 900))
        self.assertEqual({entry['kind'] for entry in plan['entries']}, {'COMMISSION', 'PERFORMANCE'})

    def test_preconfirmation_addon_refund_is_attributed_when_dispatch_is_verified_late(self):
        game = self.flow.fixture.fixture.service.game_category
        game.name = '已存在服务游戏'
        game.save(update_fields=['name'])
        other = test_addon_intents.AddonIntentTests(
            methodName='test_boss_intent_records_sources_snapshot_and_idempotency_without_payment')
        other.setUp()
        other.config.condition_mode = 'NONE'
        other.config.conditions = ''
        other.config.save(update_fields=['condition_mode', 'conditions'])
        profile = other.provider_user.escort_profile
        profile.account = other.provider
        profile.save(update_fields=['account'])
        wallet = get_wallet(account=other.boss, user=other.boss_user)
        wallet.balance = 3000
        wallet.save(update_fields=['balance'])
        for kind, amount in [('PREPAID', 1600), ('GIFT', 900), ('COMPENSATION', 500)]:
            PointFundingLot.objects.create(account=other.boss, source_key=f'late-owner-{kind}',
                                           kind=kind, remaining=amount)
        main = Transaction.objects.create(wallet=wallet, order=other.order, tx_type='PAY', amount=-other.order.amount,
                                          balance_before=other.order.amount, balance_after=0)
        Transaction.objects.filter(pk=main.pk).update(created_at=self.salary.original_time)
        log = OrderStatusLog.objects.create(order=other.order, action='CREATE', operator_account=self.salary.worker)
        OrderStatusLog.objects.filter(pk=log.pk).update(created_at=self.salary.original_time)
        intent = other.client.post(other.url, other.payload, format='json')
        self.assertEqual(intent.status_code, 200, intent.data)
        paid = other.client.post(other.url + f"{intent.data['data']['id']}/pay/", {}, format='json')
        self.assertEqual(paid.status_code, 200, paid.data)
        from orders.models import OrderAddonPayment
        payment = OrderAddonPayment.objects.get(pk=paid.data['data']['payment_id'])
        payment.paid_at = self.salary.original_time
        payment.save(update_fields=['paid_at'])
        Transaction.objects.filter(pk=payment.buyer_transaction_id).update(created_at=self.salary.original_time)
        AfterSale.objects.create(order=other.order, account=other.boss, reason='加购退款')
        staff = staff_client(['order:refund'])
        admin_url = f'/api/admin/orders/{other.order.pk}/addons/payments/{payment.pk}/refund-proposals/'
        user_url = f'/api/orders/orders/{other.order.pk}/addons/payments/{payment.pk}/refund-proposals/'
        proposal = staff.post(admin_url, {'request_id': 'late-owner-addon', 'amount_raw': 500,
                                           'explanation': '退款未履行的加购'}, format='json')
        self.assertEqual(proposal.status_code, 200, proposal.data)
        proposal_id = proposal.data['data']['id']
        self.assertEqual(other.client.post(user_url + f'{proposal_id}/', {'accepted': True}, format='json').status_code, 200)
        self.assertEqual(other.provider_client.post(user_url + f'{proposal_id}/', {'accepted': True}, format='json').status_code, 200)
        refund = staff.post(admin_url + f'{proposal_id}/execute/', {}, format='json')
        self.assertEqual(refund.status_code, 200, refund.data)

        buyer = make_user()
        account = get_or_create_account_for_legacy_user(buyer)
        current_order = make_order(buyer, amount=5000, customer_account=account, payment_status='PAID')
        current_wallet = get_wallet(user=buyer, account=account)
        Transaction.objects.create(wallet=current_wallet, order=current_order, tx_type='PAY', amount=-5000,
                                   balance_before=5000, balance_after=0)
        OrderStatusLog.objects.create(order=current_order, action='CREATE', operator_account=self.salary.worker)
        current = self.salary.client.post('/api/admin/salary-activities/', {'kind': 'DISPATCH',
            'account': self.salary.worker.pk, 'source_id': current_order.pk, 'reason': '核实当月派单'}, format='json')
        self.assertEqual(current.status_code, 201, current.data)
        confirmed = self.salary.confirm(self.salary.current_text)
        self.assertEqual(confirmed['totals']['performance_fen'], 650)
        late = self.salary.client.post('/api/admin/salary-activities/', {'kind': 'DISPATCH',
            'account': self.salary.worker.pk, 'source_id': other.order.pk, 'reason': '补核原订单派单'}, format='json')
        self.assertEqual(late.status_code, 201, late.data)
        next_month = (self.salary.current.replace(day=28) + timedelta(days=4)).replace(day=1)
        next_text = next_month.strftime('%Y-%m')
        self.salary.assign(next_text)
        result = self.salary.confirm(next_text)
        entries = result['lines'][0]['refund_deduction_plan']['entries']
        self.assertEqual([(item['kind'], item['refund_origin'], item['amount_fen']) for item in entries],
                         [('PERFORMANCE', 'ADDON', 50)])
        self.assertEqual(self.salary.preview(self.salary.current_text)['totals'], confirmed['totals'])


@skipUnless(connection.vendor == 'mysql',
            '工资确认依赖 MySQL 工资事件屏障（18-盘点工资屏障待联调）；本模块同时作为 barrier_mysql 簇夹具')
@override_settings(PAYROLL_REFUND_ACCOUNTING_INTERNAL_TESTING=True, ADDON_PAYMENT_INTERNAL_TESTING=True,
                   ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True, PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class LateAddonPayrollFlowTest(TestCase):
    def setUp(self):
        self.salary = salary_fixture.LateActivityPayrollTest(
            methodName='test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms')
        self.salary.setUp()
        self.salary.plan.base_fen = 1000
        self.salary.plan.dispatch_day_bps = self.salary.plan.dispatch_night_bps = 1000
        self.salary.plan.performance_fen = 1000
        self.salary.plan.target_fen = 10000
        self.salary.plan.save(update_fields=['base_fen', 'dispatch_day_bps', 'dispatch_night_bps',
                                             'performance_fen', 'target_fen'])
        self.addon = test_addon_intents.AddonIntentTests(
            methodName='test_boss_intent_records_sources_snapshot_and_idempotency_without_payment')
        self.addon.setUp()
        self.addon.config.condition_mode = 'NONE'
        self.addon.config.conditions = ''
        self.addon.config.save(update_fields=['condition_mode', 'conditions'])
        profile = self.addon.provider_user.escort_profile
        profile.account = self.addon.provider
        profile.save(update_fields=['account'])
        wallet = get_wallet(account=self.addon.boss, user=self.addon.boss_user)
        wallet.balance = 3000
        wallet.save(update_fields=['balance'])
        for kind, amount in [('PREPAID', 1600), ('GIFT', 900), ('COMPENSATION', 500)]:
            PointFundingLot.objects.create(account=self.addon.boss, source_key=f'late-addon-{kind}',
                                           kind=kind, remaining=amount)
        main = Transaction.objects.create(wallet=wallet, order=self.addon.order, tx_type='PAY',
            amount=-self.addon.order.amount, balance_before=self.addon.order.amount, balance_after=0)
        Transaction.objects.filter(pk=main.pk).update(created_at=self.salary.original_time)
        event = OrderStatusLog.objects.create(order=self.addon.order, action='CREATE', operator_account=self.salary.worker)
        OrderStatusLog.objects.filter(pk=event.pk).update(created_at=self.salary.original_time)
        self.salary.assign(self.salary.original_text)
        response = self.salary.client.post('/api/admin/salary-activities/', {'kind': 'DISPATCH',
            'account': self.salary.worker.pk, 'source_id': self.addon.order.pk, 'reason': '核实主单派单'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.salary.confirm(self.salary.original_text)
        self.salary.assign(self.salary.current_text)
        manager_user = make_console_user([])
        self.manager = get_or_create_account_for_legacy_user(manager_user)
        AdminMembership.objects.filter(user=manager_user).update(account=self.manager)
        position = BusinessDictionaryItem.objects.create(type='SALARY_POSITION', code='ADDON_MANAGER', name='店长')
        manager_plan = SalaryPlan.objects.create(position=position, name='店长方案', base_fen=0,
            performance_fen=1000, target_fen=10000, target_scope='STORE')
        self.manager_plan_id = manager_plan.pk
        manager_assignment = self.salary.client.post('/api/admin/salary-assignments/',
            {'month': self.salary.current_text, 'account': self.manager.pk, 'plan': manager_plan.pk}, format='json')
        self.assertEqual(manager_assignment.status_code, 201, manager_assignment.data)
        self.confirmed = self.salary.confirm(self.salary.current_text)
        intent = self.addon.client.post(self.addon.url, self.addon.payload, format='json')
        self.assertEqual(intent.status_code, 200, intent.data)
        paid = self.addon.client.post(self.addon.url + f"{intent.data['data']['id']}/pay/", {}, format='json')
        self.assertEqual(paid.status_code, 200, paid.data)
        self.payment_id = paid.data['data']['payment_id']
        self.next_month = (self.salary.current.replace(day=28) + timedelta(days=4)).replace(day=1)
        self.next_text = self.next_month.strftime('%Y-%m')
        self.salary.assign(self.next_text)
        response = self.salary.client.post('/api/admin/salary-assignments/',
            {'month': self.next_text, 'account': self.manager.pk, 'plan': self.manager_plan_id}, format='json')
        self.assertEqual(response.status_code, 201, response.data)

    def refund(self, amount):
        AfterSale.objects.create(order=self.addon.order, account=self.addon.boss, reason='加购退款')
        staff = staff_client(['order:refund'])
        admin_url = f'/api/admin/orders/{self.addon.order.pk}/addons/payments/{self.payment_id}/refund-proposals/'
        user_url = f'/api/orders/orders/{self.addon.order.pk}/addons/payments/{self.payment_id}/refund-proposals/'
        proposal = staff.post(admin_url, {'request_id': 'late-addon-refund', 'amount_raw': amount,
                                           'explanation': '退款未履行的加购'}, format='json')
        self.assertEqual(proposal.status_code, 200, proposal.data)
        proposal_id = proposal.data['data']['id']
        self.assertEqual(self.addon.client.post(user_url + f'{proposal_id}/', {'accepted': True}, format='json').status_code, 200)
        self.assertEqual(self.addon.provider_client.post(user_url + f'{proposal_id}/', {'accepted': True}, format='json').status_code, 200)
        result = staff.post(admin_url + f'{proposal_id}/execute/', {}, format='json')
        self.assertEqual(result.status_code, 200, result.data)

    def test_addon_paid_after_confirmed_month_is_supplemented_once(self):
        result = self.salary.confirm(self.next_text)
        owner = next(line for line in result['lines'] if line['account'] == self.salary.worker.pk)
        manager = next(line for line in result['lines'] if line['account'] == self.manager.pk)
        late = owner['late_addon_sources'][0]
        self.assertEqual((late['commission_fen'], late['performance_fen']), (150, 150))
        self.assertEqual(manager['late_addon_sources'][0]['performance_fen'], 150)
        self.assertEqual(result['totals']['total_fen'], 1450)
        self.assertEqual(self.salary.preview(self.salary.current_text)['totals'], self.confirmed['totals'])
        self.assertEqual(self.salary.preview(self.next_text)['totals']['total_fen'], 1450)

    def test_same_original_month_refund_reduces_late_addon_without_double_deduction(self):
        self.refund(500)
        result = self.salary.confirm(self.next_text)
        owner = next(line for line in result['lines'] if line['account'] == self.salary.worker.pk)
        manager = next(line for line in result['lines'] if line['account'] == self.manager.pk)
        late = owner['late_addon_sources'][0]
        self.assertEqual((late['commission_fen'], late['performance_fen']), (100, 100))
        self.assertEqual(manager['late_addon_sources'][0]['performance_fen'], 100)
        self.assertEqual(result['totals']['total_fen'], 1300)
        self.assertEqual(owner['refund_deduction_plan']['deducted_fen'], 0)

    def test_refund_after_late_addon_wage_is_recovered_from_following_period(self):
        next_time = timezone.make_aware(timezone.datetime.combine(
            self.next_month.replace(day=5), timezone.datetime.min.time()))
        with patch('django.utils.timezone.now', return_value=next_time):
            paid = self.salary.confirm(self.next_text)
            self.assertEqual(paid['totals']['total_fen'], 1450)
        with patch('django.utils.timezone.now', return_value=next_time + timedelta(seconds=1)):
            self.refund(500)
            following = (self.next_month.replace(day=28) + timedelta(days=4)).replace(day=1)
            following_text = following.strftime('%Y-%m')
            self.salary.assign(following_text)
            preview = self.salary.preview(following_text)
            plan = preview['lines'][0]['refund_deduction_plan']
            self.assertEqual((plan['deducted_fen'], plan['net_fen']), (50, 950))
            self.assertEqual(plan['entries'][0]['refund_origin'], 'ADDON')
            self.assertEqual(self.salary.confirm(following_text)['totals']['total_fen'], 950)

    def test_store_manager_receives_late_addon_performance_without_a_dispatch_owner(self):
        game = self.addon.service.game_category
        game.name = '既有服务游戏'
        game.save(update_fields=['name'])
        other = test_addon_intents.AddonIntentTests(
            methodName='test_boss_intent_records_sources_snapshot_and_idempotency_without_payment')
        other.setUp()
        other.config.condition_mode = 'NONE'
        other.config.conditions = ''
        other.config.save(update_fields=['condition_mode', 'conditions'])
        wallet = get_wallet(account=other.boss, user=other.boss_user)
        wallet.balance = 3000
        wallet.save(update_fields=['balance'])
        for kind, amount in [('PREPAID', 1600), ('GIFT', 900), ('COMPENSATION', 500)]:
            PointFundingLot.objects.create(account=other.boss, source_key=f'unassigned-addon-{kind}',
                                           kind=kind, remaining=amount)
        main = Transaction.objects.create(wallet=wallet, order=other.order, tx_type='PAY', amount=-other.order.amount,
                                          balance_before=other.order.amount, balance_after=0)
        Transaction.objects.filter(pk=main.pk).update(created_at=self.salary.original_time)
        intent = other.client.post(other.url, other.payload, format='json')
        self.assertEqual(intent.status_code, 200, intent.data)
        paid = other.client.post(other.url + f"{intent.data['data']['id']}/pay/", {}, format='json')
        self.assertEqual(paid.status_code, 200, paid.data)
        result = self.salary.confirm(self.next_text)
        manager = next(line for line in result['lines'] if line['account'] == self.manager.pk)
        sources = manager['late_addon_sources']
        self.assertEqual(len(sources), 2)
        self.assertEqual(sum(source['performance_fen'] for source in sources), 300)
        self.assertTrue(any(source['activity_id'] is None for source in sources))

    def test_late_addon_and_late_main_payment_share_store_performance_cap(self):
        flow = test_negotiation_execution.NegotiationExecutionPostingTests(
            methodName='test_posts_boss_refund_wallet_and_cash_deposit_once_in_one_transaction')
        flow.setUp()
        result = self.salary.confirm(self.next_text)
        manager = next(line for line in result['lines'] if line['account'] == self.manager.pk)
        addon_bonus = manager['late_addon_sources'][0]['performance_fen']
        main = next(source for source in manager['late_store_order_sources'] if source['order_id'] == flow.order.pk)
        self.assertEqual((addon_bonus, main['performance_fen']), (150, 850))
        self.assertEqual(manager['late_adjustments_fen'], 1000)


@skipUnless(connection.vendor == 'mysql',
            '工资确认依赖 MySQL 工资事件屏障（18-盘点工资屏障待联调）；本模块同时作为 barrier_mysql 簇夹具')
@override_settings(PAYROLL_REFUND_ACCOUNTING_INTERNAL_TESTING=True, NEGOTIATION_PROPOSAL_ENABLED=True,
                   PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True,
                   REFUND_DEPOSIT_DEFICIT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class LateStorePaymentPayrollTest(TestCase):
    def test_store_manager_gets_late_payment_performance_without_dispatch_owner(self):
        salary = salary_fixture.LateActivityPayrollTest(
            methodName='test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms')
        salary.setUp()
        salary.plan.base_fen = 1000
        salary.plan.performance_fen = 1000
        salary.plan.target_fen = 10000
        salary.plan.target_scope = 'STORE'
        salary.plan.save(update_fields=['base_fen', 'performance_fen', 'target_fen', 'target_scope'])
        salary.assign(salary.current_text)
        original = salary.confirm(salary.current_text)
        flow = test_negotiation_execution.NegotiationExecutionPostingTests(
            methodName='test_posts_boss_refund_wallet_and_cash_deposit_once_in_one_transaction')
        flow.setUp()
        case, proposal = flow.accepted(1000)
        flow.execute(case, proposal)
        next_month = (salary.current.replace(day=28) + timedelta(days=4)).replace(day=1)
        next_text = next_month.strftime('%Y-%m')
        salary.assign(next_text)
        result = salary.confirm(next_text)
        source = result['lines'][0]['late_store_order_sources'][0]
        self.assertEqual((source['order_id'], source['performance_fen']), (flow.order.pk, 900))
        self.assertEqual(result['totals']['total_fen'], 1900)
        self.assertEqual(salary.preview(salary.current_text)['totals'], original['totals'])
        self.assertEqual(salary.preview(next_text)['totals']['total_fen'], 1900)

    def test_late_payment_performance_first_accounts_for_refunds_of_older_orders(self):
        salary = salary_fixture.LateActivityPayrollTest(
            methodName='test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms')
        salary.setUp()
        salary.plan.base_fen = 1000
        salary.plan.performance_fen = 1000
        salary.plan.target_fen = 10000
        salary.plan.target_scope = 'STORE'
        salary.plan.save(update_fields=['base_fen', 'performance_fen', 'target_fen', 'target_scope'])
        older = test_negotiation_execution.NegotiationExecutionPostingTests(
            methodName='test_posts_boss_refund_wallet_and_cash_deposit_once_in_one_transaction')
        older.setUp()
        Transaction.objects.filter(order=older.order, tx_type='PAY').update(created_at=salary.original_time)

        def paid_order(amount):
            buyer = make_user()
            account = get_or_create_account_for_legacy_user(buyer)
            order = make_order(buyer, amount=amount, customer_account=account, payment_status='PAID')
            wallet = get_wallet(user=buyer, account=account)
            Transaction.objects.create(wallet=wallet, order=order, tx_type='PAY', amount=-amount,
                                       balance_before=amount, balance_after=0)
            return order

        paid_order(9000)
        salary.assign(salary.current_text)
        original = salary.confirm(salary.current_text)
        self.assertEqual(original['totals']['performance_fen'], 900)
        case, proposal = older.accepted(1000)
        older.execute(case, proposal)
        late = paid_order(2000)
        next_month = (salary.current.replace(day=28) + timedelta(days=4)).replace(day=1)
        next_text = next_month.strftime('%Y-%m')
        salary.assign(next_text)
        result = salary.confirm(next_text)
        source = result['lines'][0]['late_store_order_sources'][0]
        self.assertEqual((source['order_id'], source['performance_fen']), (late.pk, 200))
        self.assertEqual(result['lines'][0]['refund_deduction_plan']['deducted_fen'], 100)
        self.assertEqual(result['totals']['total_fen'], 1100)
