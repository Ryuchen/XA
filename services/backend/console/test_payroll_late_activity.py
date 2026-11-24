from datetime import timedelta
from unittest import skipUnless

from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user
from console.dictionary_models import BusinessDictionaryItem
from console.models import AdminMembership
from console.payroll_calculation import aftersale_cycles
from console.payroll_models import PayrollRun, SalaryActivity, SalaryPlan
from orders.models import AfterSale
from orders.tests.factories import make_console_user, make_order, make_user
from orders.tests.test_addon_configuration import staff_client
from wallet.models import RechargeRecord


@skipUnless(connection.vendor == 'mysql',
            '工资确认依赖 MySQL 工资事件屏障（18-盘点工资屏障待联调）；本模块同时作为 barrier_mysql 簇夹具')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'],
                   PAYROLL_REFUND_ACCOUNTING_INTERNAL_TESTING=True,
                   PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True)
class LateActivityPayrollTest(TestCase):
    def setUp(self):
        self.client = staff_client(['salary:view', 'salary:edit', 'salary:confirm'])
        worker_user = make_console_user([])
        self.worker = get_or_create_account_for_legacy_user(worker_user)
        AdminMembership.objects.filter(user=worker_user).update(account=self.worker)
        position = BusinessDictionaryItem.objects.create(type='SALARY_POSITION', code='LATE_TEST', name='考官售后')
        self.plan = SalaryPlan.objects.create(position=position, name='原方案', base_fen=100000,
            exam_fee_fen=5000, exam_commission_bps=3000, aftersale_fee_fen=6000)
        self.current = timezone.localdate().replace(day=1)
        self.original = (self.current - timedelta(days=1)).replace(day=1)
        self.original_text = self.original.strftime('%Y-%m')
        self.current_text = self.current.strftime('%Y-%m')
        self.original_time = timezone.make_aware(timezone.datetime.combine(
            self.original.replace(day=10), timezone.datetime.min.time()))

    def assign(self, month):
        response = self.client.post('/api/admin/salary-assignments/',
            {'month': month, 'account': self.worker.pk, 'plan': self.plan.pk}, format='json')
        self.assertEqual(response.status_code, 201, response.data)

    def preview(self, month):
        response = self.client.get('/api/admin/payroll/preview/', {'month': month})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['data']

    def confirm(self, month):
        preview = self.preview(month)
        self.assertTrue(preview['can_confirm'], preview['warnings'])
        response = self.client.post('/api/admin/payroll/confirm/',
            {'month': month, 'fingerprint': preview['fingerprint']}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['data']

    @override_settings(PAYROLL_REFUND_ACCOUNTING_INTERNAL_TESTING=False)
    def test_inactive_employee_late_exam_can_be_closed_without_order_exposure(self):
        self.assign(self.original_text)
        original = self.confirm(self.original_text)
        self.worker.is_active = False
        self.worker.save(update_fields=['is_active'])
        response = self.client.post('/api/admin/salary-activities/', {'kind': 'EXAM',
            'account': self.worker.pk, 'performed_at': self.original_time.isoformat(),
            'reason': '补核原月考试', 'client_request_id': 'separation-late-exam'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assign(self.current_text)
        final = self.confirm(self.current_text)
        self.assertEqual(final['totals']['late_adjustments_fen'], 3500)
        payload = {'run_id': final['run_id'], 'account_id': self.worker.pk,
                   'settled_fen': final['lines'][0]['total_fen'], 'all_wages_settled': True,
                   'note': '线下核实全部工资已结清'}
        viewer = staff_client(['salary:view'])
        forbidden = viewer.post('/api/admin/payroll/separation-settlements/', payload, format='json')
        self.assertEqual(forbidden.status_code, 403, forbidden.data)
        response = self.client.post('/api/admin/payroll/separation-settlements/', payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        again = self.client.post('/api/admin/payroll/separation-settlements/', payload, format='json')
        self.assertEqual(again.data['data']['id'], response.data['data']['id'])
        self.assertEqual(self.preview(self.original_text)['lines'], original['lines'])
        later = (self.current.replace(day=28) + timedelta(days=4)).replace(day=1)
        rejected = self.client.post('/api/admin/salary-assignments/', {'month': later.strftime('%Y-%m'),
            'account': self.worker.pk, 'plan': self.plan.pk}, format='json')
        self.assertEqual(rejected.status_code, 400, rejected.data)

    @override_settings(PAYROLL_REFUND_ACCOUNTING_INTERNAL_TESTING=False)
    def test_closeout_rejects_order_exposure_without_refund_accounting(self):
        self.plan.dispatch_day_bps = 100
        self.plan.save(update_fields=['dispatch_day_bps'])
        self.assign(self.original_text)
        final = self.confirm(self.original_text)
        self.worker.is_active = False
        self.worker.save(update_fields=['is_active'])
        payload = {'run_id': final['run_id'], 'account_id': self.worker.pk,
                   'settled_fen': final['lines'][0]['total_fen'], 'all_wages_settled': True,
                   'note': '核实工资结清'}
        response = self.client.post('/api/admin/payroll/separation-settlements/', payload, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('订单提成或绩效', str(response.data))
        from console.payroll_models import PayrollSeparationSettlement
        self.assertFalse(PayrollSeparationSettlement.objects.filter(account=self.worker).exists())

    def test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms(self):
        self.assign(self.original_text)
        first = self.confirm(self.original_text)
        self.plan.exam_fee_fen = 10000
        self.plan.aftersale_fee_fen = 15000
        self.plan.save(update_fields=['exam_fee_fen', 'aftersale_fee_fen'])
        self.assign(self.current_text)
        exam = {'kind': 'EXAM', 'account': self.worker.pk, 'reason': '核实上月考试',
                'performed_at': self.original_time.isoformat(), 'client_request_id': 'late-exam-1'}
        response = self.client.post('/api/admin/salary-activities/', exam, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        exam_id = response.data['data']['id']
        self.assertEqual(response.data['data']['source_snapshot']['late_compensation']['amount_fen'], 3500)
        self.assertEqual(response.data['data']['recognition_month'][:7], self.current_text)
        self.assertEqual(self.client.post('/api/admin/salary-activities/', exam, format='json').status_code, 200)

        boss = make_user()
        order = make_order(boss)
        case = AfterSale.objects.create(order=order, account=get_or_create_account_for_legacy_user(boss),
            reason='已完成售后', status='RESOLVED', resolved_at=self.original_time)
        AfterSale.objects.filter(pk=case.pk).update(created_at=self.original_time - timedelta(hours=1))
        case.refresh_from_db()
        payload = {'kind': 'AFTERSALE', 'account': self.worker.pk, 'source_id': case.pk,
                   'cycle_key': aftersale_cycles(case)[0]['cycle_key'], 'reason': '核实上月售后'}
        response = self.client.post('/api/admin/salary-activities/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['data']['source_snapshot']['late_compensation']['amount_fen'], 6000)
        result = self.confirm(self.current_text)
        self.assertEqual(result['totals']['late_adjustments_fen'], 9500)
        self.assertEqual(len(result['lines'][0]['late_adjustments']), 2)
        self.assertEqual(PayrollRun.objects.get(month=self.original).snapshot['totals'], first['totals'])
        self.assertEqual(SalaryActivity.objects.filter(recognition_month=self.current).count(), 2)
        self.assertEqual(self.preview(self.current_text)['totals']['late_adjustments_fen'], 9500)
        self.assertEqual(self.client.post('/api/admin/salary-activities/', exam, format='json').data['data']['id'], exam_id)
        self.assertEqual(self.client.delete(f'/api/admin/salary-activities/{exam_id}/').status_code, 409)

    def test_late_activity_skips_a_payroll_period_already_confirmed(self):
        self.assign(self.original_text)
        self.confirm(self.original_text)
        self.assign(self.current_text)
        self.confirm(self.current_text)
        response = self.client.post('/api/admin/salary-activities/', {
            'kind': 'EXAM', 'account': self.worker.pk, 'reason': '补核上月考试',
            'performed_at': self.original_time.isoformat(), 'client_request_id': 'exam-next-open'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        next_month = (self.current.replace(day=28) + timedelta(days=4)).replace(day=1)
        self.assertEqual(response.data['data']['recognition_month'][:7], next_month.strftime('%Y-%m'))
        self.assign(next_month.strftime('%Y-%m'))
        self.assertEqual(self.confirm(next_month.strftime('%Y-%m'))['totals']['late_adjustments_fen'], 3500)
        self.assertEqual(self.preview(self.current_text)['totals']['late_adjustments_fen'], 0)

    @override_settings(REQUIRE_RECHARGE_PROOF=False)
    def test_late_recharge_is_paid_once_at_original_rate_and_keeps_rounding(self):
        self.plan.recharge_bps = 500
        self.plan.save(update_fields=['recharge_bps'])
        self.assign(self.original_text)
        original = self.confirm(self.original_text)
        self.plan.recharge_bps = 1000
        self.plan.save(update_fields=['recharge_bps'])
        self.assign(self.current_text)
        buyer = get_or_create_account_for_legacy_user(make_user())
        finance = staff_client(['wallet:recharge'])
        records = []
        for index, amount in enumerate((20000, 10, 10)):
            response = finance.post('/api/admin/wallets/recharge/', {
                'account_id': buyer.pk, 'amount': amount, 'trade_no': f'late-recharge-{index}',
                'client_request_id': f'late-recharge-{index}'}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            record = RechargeRecord.objects.get(trade_no=f'late-recharge-{index}')
            RechargeRecord.objects.filter(pk=record.pk).update(created_at=self.original_time)
            records.append(record)
        activities = []
        for index, record in enumerate(records):
            payload = {'kind': 'RECHARGE', 'account': self.worker.pk, 'source_id': record.pk,
                       'reason': '核实原月充值业绩', 'client_request_id': f'late-activity-{index}'}
            response = self.client.post('/api/admin/salary-activities/', payload, format='json')
            self.assertEqual(response.status_code, 201, response.data)
            activities.append(response.data['data'])
            self.assertEqual(self.client.post('/api/admin/salary-activities/', payload, format='json').status_code, 200)
        self.assertEqual([row['source_snapshot']['late_compensation']['amount_fen'] for row in activities],
                         [1000, 1, 0])
        self.assertEqual(self.client.delete(f"/api/admin/salary-activities/{activities[0]['id']}/").status_code, 409)
        current = self.confirm(self.current_text)
        self.assertEqual(current['totals']['late_adjustments_fen'], 1001)
        self.assertEqual(PayrollRun.objects.get(month=self.original).snapshot['totals'], original['totals'])
