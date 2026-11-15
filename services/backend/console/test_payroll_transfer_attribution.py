from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from console import test_payroll_late_activity as salary_fixture
from orders.tests import test_segment_financial_capture as transfer_fixture
from orders.tests.test_staff_team_dispatch import screenshot
from wallet.models import Transaction


@override_settings(PAYROLL_REFUND_ACCOUNTING_INTERNAL_TESTING=True, ORIGINAL_ATTRIBUTION_ENABLED=True,
                   SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, SEGMENT_STANDARD_TRANSFER_ENABLED=True,
                   PRESTART_SCOPE_SNAPSHOT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class TransferPayrollAttributionTest(TestCase):
    def test_two_player_transfer_keeps_one_dispatch_attribution_and_actual_paid_basis(self):
        salary = salary_fixture.LateActivityPayrollTest(
            methodName='test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms')
        salary.setUp()
        flow = transfer_fixture.SegmentStandardTeamTransferTest(
            methodName='test_fixed_team_transfer_preserves_other_seat_and_full_order_denominator')
        flow.setUp()
        flow.client = APIClient()
        self.addCleanup(flow.doCleanups)
        salary.worker = flow.accounts[flow.staff.pk]
        salary.plan.base_fen = 0
        salary.plan.dispatch_day_bps = salary.plan.dispatch_night_bps = 1000
        salary.plan.save(update_fields=['base_fen', 'dispatch_day_bps', 'dispatch_night_bps'])
        salary.assign(salary.current_text)
        flow.service.price = 5000
        flow.service.save(update_fields=['price'])
        order = flow.create()
        self.assertEqual(flow.assign(order).data['code'], 0)
        for provider in flow.providers[:2]:
            flow.auth(provider)
            result = flow.client.post(f'/api/orders/orders/{order.pk}/start/',
                                      {'entry_image': screenshot()}, format='multipart')
            self.assertEqual(result.data['code'], 0, result.data)
        flow.auth(flow.staff)
        result = flow.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason': '协商更换', 'transfers': [{
            'old_provider_id': flow.accounts[flow.providers[0].pk].pk,
            'new_provider_id': flow.accounts[flow.providers[2].pk].pk,
            'original_share': {'retained': 2000, 'transferred': 3000, 'agreement': '双方确认份额'}}]}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        paid = Transaction.objects.get(order=order, tx_type='PAY', status='SUCCESS')
        attribution = salary.client.post('/api/admin/salary-activities/', {'kind': 'DISPATCH',
            'account': salary.worker.pk, 'source_id': order.pk, 'reason': '核实实际派单客服'}, format='json')
        self.assertEqual(attribution.status_code, 201, attribution.data)
        result = salary.confirm(salary.current_text)
        self.assertEqual(result['totals']['dispatch_commission_fen'], (-paid.amount) // 10)
        self.assertEqual(result['lines'][0]['performance_basis_fen'], -paid.amount)
        self.assertEqual(len(result['activity_ids']), 1)
