from datetime import timedelta
import os
import time
from unittest import skipUnless

from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from console import test_payroll_late_activity as salary_fixture
from console.points_redemption_config import save_points_terms
from engagement.models import MemberProfile, PointFundingLot
from engagement.services import config
from orders.models import Order, OrderApplication, OrderParticipationSegment, OrderPointsRedemption, OrderRefund
from orders.tests import test_segment_financial_capture as transfer_fixture
from orders.tests import test_staff_points_dispatch as staff_points_fixture
from orders.tests.test_staff_team_dispatch import screenshot
from wallet.models import Transaction


@override_settings(
    PAYROLL_REFUND_ACCOUNTING_INTERNAL_TESTING=True, ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False,
    POINTS_REDEMPTION_PREVIEW_ENABLED=True, DISCOUNT_OPTIONS_PREVIEW_ENABLED=True,
    DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True,
    SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=True,
    SERVICE_POINTS_CHECKOUT_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
    SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True,
    SERVICE_POINTS_SETTLEMENT_TESTING=True, ORIGINAL_ATTRIBUTION_ENABLED=True,
    SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, SEGMENT_STANDARD_TRANSFER_ENABLED=True,
    SEGMENT_REPORT_ENABLED=True, SEGMENT_SETTLEMENT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    SEGMENT_REFUND_PREVIEW_ENABLED=True, SERVICE_POINTS_TRANSFER_CAPTURE_TESTING=True,
    SERVICE_POINTS_TRANSFER_FUNDING_REVISION_TESTING=True, SERVICE_POINTS_TRANSFER_SETTLEMENT_TESTING=True,
    SERVICE_POINTS_TRANSFER_REFUND_PREVIEW_TESTING=True, NEGOTIATION_PROPOSAL_ENABLED=True,
    SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True, SERVICE_POINTS_NEGOTIATION_TESTING=True,
    SERVICE_POINTS_TRANSFER_REFUND_AGREEMENT_TESTING=True, SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True,
    SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True, SERVICE_POINTS_TRANSFER_REFUND_POSTING_TESTING=True,
    SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True, SERVICE_POINTS_TRANSFER_REFUND_TERMINATION_TESTING=True,
    SERVICE_POINTS_TRANSFER_REFUND_REPORT_TESTING=True, SERVICE_POINTS_FUNDING_RELEASE_TESTING=True,
    SERVICE_POINTS_TRANSFER_REFUND_RELEASE_TESTING=True, SERVICE_POINTS_TRANSFER_REFUND_FUNDING_TESTING=True,
    SERVICE_POINTS_TRANSFER_REFUND_SETTLEMENT_TESTING=True, SERVICE_POINTS_CONSUMPTION_TESTING=True,
    SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True, SERVICE_POINTS_TRANSFER_REFUND_CONSUMPTION_TESTING=True,
    SERVICE_POINTS_TRANSFER_REFUND_ENDPOINT_TESTING=True, SERVICE_POINTS_REFUND_DEPOSIT_TESTING=True,
    SERVICE_POINTS_TRANSFER_FOLLOWUP_PREVIEW_TESTING=True, REFUND_DEPOSIT_DEFICIT_ENABLED=True,
    REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
)
@skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
            '转单退款工资调整依赖正式逐段转单链与客服多人指派夹具（18-盘点转单分段收敛／客服安排抢单），门禁保持关闭；最终联调启用')
class TransferRefundPayrollIntegrationTest(TransactionTestCase):
    @override_settings(GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True, GRAB_TEAM_WINDOW_ENABLED=True,
        GRAB_SINGLE_WINDOW_ENABLED=True, GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True,
        REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED=True)
    def test_staff_points_dispatch_transfer_and_refund_adjust_actual_dispatcher_once(self):
        from console.models import AdminMembership
        from orders.grab_team_flow import resolve

        salary = salary_fixture.LateActivityPayrollTest(
            methodName='test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms')
        salary.setUp()
        flow = staff_points_fixture.StaffPointsDispatchTest(methodName='test_staff_double_points_order_real_team_allocation')
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        flow.prepare(team=True)
        first, player_a = flow.provider(1)
        second, player_b = flow.provider(2)
        replacement, player_c = flow.provider(3)
        order = flow.create()
        time.sleep(.01)
        invite = player_a.post(f'/api/orders/orders/{order.pk}/grab-team/',
            {'partner_id': second.pk, 'client_request_id': 'payroll-team-invite'}, format='json')
        self.assertEqual(invite.status_code, 200, invite.data)
        joined = player_b.post(f'/api/orders/grab-team/invitations/{invite.data["data"]["team_id"]}/confirm/',
            {'client_request_id': 'payroll-team-confirm'}, format='json')
        self.assertEqual(joined.status_code, 200, joined.data)
        time.sleep(1.05)
        self.assertEqual(resolve(order.pk), 'ASSIGNED')
        for player in (player_a, player_b):
            started = player.post(f'/api/orders/orders/{order.pk}/start/',
                                  {'entry_image': screenshot()}, format='multipart')
            self.assertEqual(started.data['code'], 0, started.data)
        membership = AdminMembership.objects.get(account=flow.staff)
        role = membership.roles.first()
        role.permissions = list(set(role.permissions + ['support:edit', 'order:refund', 'escort:deposit_topup']))
        role.save(update_fields=['permissions'])
        changed = flow.admin.post(f'/api/admin/orders/{order.pk}/transfer/', {
            'reason': '双方同意更换', 'transfers': [{'old_provider_id': first.pk, 'new_provider_id': replacement.pk,
                'original_share': {'retained': 5000, 'transferred': 5000, 'agreement': '双方确认'}}]}, format='json')
        self.assertEqual(changed.data['code'], 0, changed.data)
        segment = OrderParticipationSegment.objects.get(attribution__order=order, provider_account=replacement)
        entered = player_c.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id': segment.pk, 'entry_image': screenshot()}, format='multipart')
        self.assertEqual(entered.data['code'], 0, entered.data)
        opened = flow.admin.post('/api/admin/aftersales/open-negotiation/', {'order_id': order.pk,
            'reason': '协商终止接替者服务', 'affected_provider_account_ids': [replacement.pk],
            'client_request_id': 'staff-points-transfer-case'}, format='json')
        self.assertEqual(opened.data['code'], 0, opened.data)
        case = opened.data['data']['id']
        preview = flow.admin.post(f'/api/admin/orders/{order.pk}/segment-refund-preview/',
                                  {'refund_amount': 1000}, format='json')
        self.assertEqual(preview.data['code'], 0, preview.data)
        term = preview.data['data']['provider_terms'][0]
        proposal = flow.admin.post(f'/api/admin/aftersales/{case}/negotiation-proposal/', {
            'request_id': 'staff-points-transfer-proposal', 'expected_version': 0, 'refund_units': 1000,
            'provider_terms': [{'account_id': replacement.pk, 'retained_units': term['retained_units']}],
            'explanation': '双方确认退款'}, format='json')
        self.assertEqual(proposal.data['code'], 0, proposal.data)
        proposal_id = proposal.data['data']['id']
        for actor in (flow.client, player_c):
            result = actor.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
                                {'proposal_id': proposal_id, 'accepted': True}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
        salary.worker = flow.staff
        salary.plan.base_fen = 10000
        salary.plan.performance_fen = salary.plan.target_fen = 100000
        salary.plan.dispatch_day_bps = salary.plan.dispatch_night_bps = 1000
        salary.plan.save(update_fields=['base_fen', 'performance_fen', 'target_fen',
                                        'dispatch_day_bps', 'dispatch_night_bps'])
        salary.assign(salary.current_text)
        paid = -Transaction.objects.get(order=order, tx_type='PAY', status='SUCCESS').amount
        attribution = salary.client.post('/api/admin/salary-activities/', {'kind': 'DISPATCH',
            'account': flow.staff.pk, 'source_id': order.pk, 'reason': '核实客服代单'}, format='json')
        self.assertEqual(attribution.status_code, 201, attribution.data)
        confirmed = salary.confirm(salary.current_text)
        self.assertEqual((confirmed['totals']['dispatch_commission_fen'], confirmed['lines'][0]['performance_basis_fen']),
                         ((paid * 1000 + 5000) // 10000, paid))
        executed = flow.admin.post(f'/api/admin/aftersales/{case}/negotiation-execution/',
            {'proposal_id': proposal_id, 'client_request_id': 'staff-points-transfer-refund'}, format='json')
        self.assertEqual(executed.data['code'], 0, executed.data)
        refund = OrderRefund.objects.get(order=order)
        self.assertLess(refund.amount, 1000)
        next_month = (salary.current.replace(day=28) + timedelta(days=4)).replace(day=1).strftime('%Y-%m')
        salary.assign(next_month)
        following = salary.confirm(next_month)
        commission_before = (paid * 1000 + 5000) // 10000
        commission_after = ((paid - refund.amount) * 1000 + 5000) // 10000
        self.assertEqual(following['lines'][0]['refund_deduction_plan']['deducted_fen'],
                         refund.amount + commission_before - commission_after)
        self.assertEqual(salary.preview(salary.current_text)['totals'], confirmed['totals'])

    def test_transferred_points_refund_adjusts_confirmed_store_performance_once(self):
        salary = salary_fixture.LateActivityPayrollTest(methodName='test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms')
        salary.setUp()
        flow = transfer_fixture.SegmentStandardTeamTransferTest(methodName='test_fixed_team_transfer_preserves_other_seat_and_full_order_denominator')
        flow.setUp()
        flow.client = APIClient()
        self.addCleanup(flow.doCleanups)
        salary.worker = flow.accounts[flow.staff.pk]
        salary.plan.base_fen = 10000
        salary.plan.performance_fen = salary.plan.target_fen = 100000
        salary.plan.target_scope = 'STORE'
        salary.plan.save(update_fields=['base_fen', 'performance_fen', 'target_fen',
                                        'target_scope'])
        salary.assign(salary.current_text)
        account = flow.accounts[flow.boss.pk]
        config()
        MemberProfile.objects.create(account=account, points_balance=10000)
        PointFundingLot.objects.create(account=account, source_key='transfer-payroll-principal',
                                       kind='PREPAID', remaining=100000)
        save_points_terms(group_point_hundredths=10000, group_coin_units=4000,
                          max_ratio_basis_points=5000, version=0, audit=lambda *_: None)
        flow.service.price = 10000
        flow.service.commission_policy = 'REGULAR'
        flow.service.save(update_fields=['price', 'commission_policy'])
        flow.auth(flow.boss)
        payload = {'service_id': flow.service.pk,
                   'preview_points_hundredths': 10000, 'client_request_id': 'payroll-transfer-points'}
        options = flow.client.post('/api/orders/discount-options-preview/',
                                   {'service_id': flow.service.pk}, format='json')
        self.assertEqual(options.data['code'], 0, options.data)
        payload['discount_selection_token'] = options.data['data']['options'][0]['selection_token']
        quote = flow.client.post('/api/orders/quote/', payload, format='json')
        self.assertEqual(quote.data['code'], 0, quote.data)
        token = quote.data['data']['points_preview']['checkout_test_token']
        created = flow.client.post('/api/orders/create/', {**{k: v for k, v in payload.items()
            if k != 'preview_points_hundredths'}, 'points_checkout_test_token': token}, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order = Order.objects.get(pk=created.data['data']['id'])
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=1))
        self.assertGreater(OrderPointsRedemption.objects.get(order=order).discount_units, 0)
        flow.auth(flow.providers[0])
        grabbed = flow.client.post(f'/api/orders/orders/{order.pk}/grab/', {'team_size': 2,
            'partner_id': flow.accounts[flow.providers[1].pk].pk}, format='json')
        self.assertEqual(grabbed.data['code'], 0, grabbed.data)
        application = OrderApplication.objects.get(order=order, applicant=flow.accounts[flow.providers[0].pk])
        flow.auth(flow.providers[1])
        team = flow.client.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/action/',
                                {'action': 'CONFIRM_TEAM'}, format='json')
        self.assertEqual(team.data['code'], 0, team.data)
        flow.auth(flow.boss)
        selected = flow.client.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/select/', {}, format='json')
        self.assertEqual(selected.data['code'], 0, selected.data)
        for provider in flow.providers[:2]:
            flow.auth(provider)
            started = flow.client.post(f'/api/orders/orders/{order.pk}/start/',
                                       {'entry_image': screenshot()}, format='multipart')
            self.assertEqual(started.data['code'], 0, started.data)
        role = flow.staff.admin_membership.roles.first()
        role.permissions = list(set(role.permissions + ['support:edit', 'order:refund', 'escort:deposit_topup']))
        role.save(update_fields=['permissions'])
        flow.auth(flow.staff)
        old, replacement = (flow.accounts[flow.providers[i].pk] for i in (0, 2))
        transferred = flow.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason': '双方同意',
            'transfers': [{'old_provider_id': old.pk, 'new_provider_id': replacement.pk,
                           'original_share': {'retained': 5000, 'transferred': 5000, 'agreement': '双方确认'}}]}, format='json')
        self.assertEqual(transferred.data['code'], 0, transferred.data)
        segment = OrderParticipationSegment.objects.get(attribution__order=order, provider_account=replacement)
        flow.auth(flow.providers[2])
        entered = flow.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
                                  {'segment_id': segment.pk, 'entry_image': screenshot()}, format='multipart')
        self.assertEqual(entered.data['code'], 0, entered.data)
        flow.auth(flow.staff)
        opened = flow.client.post('/api/admin/aftersales/open-negotiation/', {'order_id': order.pk,
            'reason': '协商终止接替者服务', 'affected_provider_account_ids': [replacement.pk],
            'client_request_id': 'payroll-transfer-case'}, format='json')
        self.assertEqual(opened.data['code'], 0, opened.data)
        case = opened.data['data']['id']
        preview = flow.client.post(f'/api/admin/orders/{order.pk}/segment-refund-preview/',
                                   {'refund_amount': 1000}, format='json')
        self.assertEqual(preview.data['code'], 0, preview.data)
        terms = preview.data['data']['provider_terms'][0]
        proposal = flow.client.post(f'/api/admin/aftersales/{case}/negotiation-proposal/', {
            'request_id': 'payroll-transfer-proposal', 'expected_version': 0, 'refund_units': 1000,
            'provider_terms': [{'account_id': replacement.pk, 'retained_units': terms['retained_units']}],
            'explanation': '双方确认退款'}, format='json')
        self.assertEqual(proposal.data['code'], 0, proposal.data)
        proposal_id = proposal.data['data']['id']
        for actor in (flow.boss, flow.providers[2]):
            flow.auth(actor)
            accepted = flow.client.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
                {'proposal_id': proposal_id, 'accepted': True}, format='json')
            self.assertEqual(accepted.data['code'], 0, accepted.data)
        payment = Transaction.objects.get(order=order, tx_type='PAY', status='SUCCESS')
        actual_paid = -payment.amount
        confirmed = salary.confirm(salary.current_text)
        self.assertEqual(confirmed['lines'][0]['performance_basis_fen'], actual_paid)
        flow.auth(flow.staff)
        executed = flow.client.post(f'/api/admin/aftersales/{case}/negotiation-execution/',
            {'proposal_id': proposal_id, 'client_request_id': 'payroll-transfer-refund'}, format='json')
        self.assertEqual(executed.data['code'], 0, executed.data)
        refund = OrderRefund.objects.get(order=order)
        self.assertLess(refund.amount, 1000)
        next_month = (salary.current.replace(day=28) + timedelta(days=4)).replace(day=1).strftime('%Y-%m')
        salary.assign(next_month)
        following = salary.confirm(next_month)
        self.assertEqual(following['lines'][0]['refund_deduction_plan']['deducted_fen'], refund.amount)
        self.assertEqual(salary.preview(salary.current_text)['totals'], confirmed['totals'])
