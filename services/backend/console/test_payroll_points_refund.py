from datetime import timedelta
from unittest import skipUnless

from django.db import connection
from django.test import TestCase, override_settings

from console import test_payroll_late_activity as salary_fixture
from orders.models import OrderStatusLog
from orders.tests import test_points_service_refund


@skipUnless(connection.vendor == 'mysql',
            '工资确认依赖 MySQL 工资事件屏障（18-盘点工资屏障待联调）；本模块同时作为 barrier_mysql 簇夹具')
@override_settings(PAYROLL_REFUND_ACCOUNTING_INTERNAL_TESTING=True, SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True,
                   POINTS_USAGE_SWITCHES_ENABLED=True, PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class PointsRefundPayrollFlowTest(TestCase):
    def test_partial_points_refund_only_reduces_paid_coins_in_payroll(self):
        salary = salary_fixture.LateActivityPayrollTest(
            methodName='test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms')
        salary.setUp()
        salary.plan.base_fen = 1000
        salary.plan.performance_fen = salary.plan.target_fen = 1000
        salary.plan.dispatch_day_bps = salary.plan.dispatch_night_bps = 1000
        salary.plan.save(update_fields=['base_fen', 'performance_fen', 'target_fen',
                                        'dispatch_day_bps', 'dispatch_night_bps'])
        flow = test_points_service_refund.ServicePointsRefundTests(
            methodName='test_partial_then_final_return_original_assets_and_sources')
        flow.setUp()
        OrderStatusLog.objects.create(order=flow.order, action='CREATE', operator_account=salary.worker)
        salary.assign(salary.current_text)
        activity = salary.client.post('/api/admin/salary-activities/', {'kind': 'DISPATCH',
            'account': salary.worker.pk, 'source_id': flow.order.pk, 'reason': '核实派单'}, format='json')
        self.assertEqual(activity.status_code, 201, activity.data)
        confirmed = salary.confirm(salary.current_text)
        self.assertEqual((confirmed['totals']['dispatch_commission_fen'], confirmed['totals']['performance_fen']),
                         (97, 970))
        refund = flow.refund(500, 480)
        self.assertEqual((refund.equivalent_units, refund.coin_units), (500, 480))
        next_month = (salary.current.replace(day=28) + timedelta(days=4)).replace(day=1)
        next_text = next_month.strftime('%Y-%m')
        salary.assign(next_text)
        result = salary.confirm(next_text)
        adjustments = result['lines'][0]['refund_deduction_plan']
        self.assertEqual((adjustments['deducted_fen'], result['totals']['total_fen']), (528, 472))
        self.assertEqual(salary.preview(salary.current_text)['totals'], confirmed['totals'])
