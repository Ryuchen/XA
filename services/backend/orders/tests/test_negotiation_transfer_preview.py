from django.test import TestCase, override_settings
from datetime import timedelta
from unittest.mock import patch
from django.utils import timezone
from rest_framework.test import APIClient

from orders.models import OrderParticipationSegment
from orders.negotiation_transfer_preview import preview_locked
from orders.tests import test_segment_financial_capture as fixture
from orders.tests.test_staff_team_dispatch import screenshot
from stores.db import transaction
from wallet.services import get_wallet


@override_settings(ORDINARY_TRANSFER_REFUND_PREVIEW_INTERNAL_TESTING=True,
    ORDINARY_TRANSFER_REFUND_AGREEMENT_INTERNAL_TESTING=True, NEGOTIATION_PROPOSAL_ENABLED=True,
    PAYROLL_REFUND_ACCOUNTING_INTERNAL_TESTING=True,
    NEGOTIATION_REFUND_EXECUTION_ENABLED=True, REFUND_DEPOSIT_DEFICIT_ENABLED=True,
    REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True,
    SEGMENT_REPORT_ENABLED=True, SEGMENT_SETTLEMENT_ENABLED=True,
    ORDINARY_TRANSFER_REFUND_REPORT_INTERNAL_TESTING=True, ORDINARY_TRANSFER_REFUND_SETTLEMENT_INTERNAL_TESTING=True,
    ORIGINAL_ATTRIBUTION_ENABLED=True,
    SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, SEGMENT_STANDARD_TRANSFER_ENABLED=True,
    PRESTART_SCOPE_SNAPSHOT_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=True,
    LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    REQUIRE_ORDER_EVIDENCE_IMAGES=True, ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class OrdinaryTransferNegotiationPreviewTest(TestCase):
    def test_real_transfer_uses_segment_income_for_scoped_refund(self):
        self.exercise_refund(1000)

    def test_real_transfer_refund_recovers_wallet_and_deposit_before_settlement(self):
        self.exercise_refund(8000)

    def exercise_refund(self, refund_units):
        from console import test_payroll_late_activity as salary_fixture
        salary = salary_fixture.LateActivityPayrollTest(
            methodName='test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms')
        salary.setUp()
        flow = fixture.SegmentStandardTeamTransferTest(
            methodName='test_fixed_team_transfer_preserves_other_seat_and_full_order_denominator')
        flow.setUp()
        flow.client = APIClient()
        self.addCleanup(flow.doCleanups)
        role = flow.staff.admin_membership.roles.first()
        role.permissions = list(set(role.permissions + ['support:edit', 'order:refund', 'escort:deposit_topup']))
        role.save(update_fields=['permissions'])
        if refund_units == 8000:
            flow.auth(flow.staff)
            for index in (0, 2):
                profile = flow.providers[index].escort_profile
                profile.deposit_paid = 0
                profile.save(update_fields=['deposit_paid'])
                topup = flow.client.post(f'/api/admin/escorts/{profile.pk}/deposit-topups/', {
                    'amount': 40000, 'reason': '线下核实押金', 'reference': f'TEST-{index}',
                    'client_request_id': f'ordinary-topup-{index}'}, format='multipart')
                self.assertEqual(topup.data['code'], 0, topup.data)
        flow.service.price = 5000
        flow.service.save(update_fields=['price'])
        order = flow.create()
        self.assertEqual(flow.assign(order).data['code'], 0)
        salary.worker = flow.accounts[flow.staff.pk]
        salary.plan.base_fen = 10000
        salary.plan.performance_fen = salary.plan.target_fen = 100000
        salary.plan.dispatch_day_bps = salary.plan.dispatch_night_bps = 1000
        salary.plan.save(update_fields=['base_fen', 'performance_fen', 'target_fen',
                                        'dispatch_day_bps', 'dispatch_night_bps'])
        salary.assign(salary.current_text)
        activity = salary.client.post('/api/admin/salary-activities/', {'kind': 'DISPATCH',
            'account': salary.worker.pk, 'source_id': order.pk, 'reason': '核实原派单客服'}, format='json')
        self.assertEqual(activity.status_code, 201, activity.data)
        salary_original = salary.confirm(salary.current_text)
        self.assertEqual((salary_original['totals']['dispatch_commission_fen'],
                          salary_original['lines'][0]['performance_basis_fen']), (1000, 10000))
        for player in flow.providers[:2]:
            flow.auth(player)
            response = flow.client.post(f'/api/orders/orders/{order.pk}/start/',
                                        {'entry_image': screenshot()}, format='multipart')
            self.assertEqual(response.data['code'], 0, response.data)
        old, replacement = (flow.accounts[flow.providers[i].pk] for i in (0, 2))
        flow.auth(flow.staff)
        response = flow.client.post(f'/api/admin/orders/{order.pk}/transfer/', {
            'reason': '协商更换', 'transfers': [{'old_provider_id': old.pk, 'new_provider_id': replacement.pk,
                'original_share': {'retained': 2000, 'transferred': 3000, 'agreement': '双方确认'}}]}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        segment = OrderParticipationSegment.objects.get(attribution__order=order, provider_account=replacement)
        flow.auth(flow.providers[2])
        response = flow.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id': segment.pk, 'entry_image': screenshot()}, format='multipart')
        self.assertEqual(response.data['code'], 0, response.data)
        if refund_units == 8000:
            for player, amount in ((flow.providers[0], 500), (flow.providers[2], 1000)):
                wallet = get_wallet(user=player, account=flow.accounts[player.pk])
                wallet.balance = amount
                wallet.save(update_fields=['balance'])
        flow.auth(flow.staff)
        response = flow.client.post('/api/admin/aftersales/open-negotiation/', {
            'order_id': order.pk, 'reason': '两位陪玩协商终止', 'affected_provider_account_ids': [old.pk, replacement.pk],
            'client_request_id': 'ordinary-transfer-preview-case'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        with transaction.atomic():
            result = preview_locked(response.data['data']['id'], refund_units)
        expected = [(old.pk, 1800, 400, 1400, 0), (replacement.pk, 2699, 600, 2099, 0)] if refund_units == 1000 else [
            (old.pk, 1800, 3200, 0, 1400), (replacement.pk, 2699, 4800, 0, 2101)]
        self.assertEqual([(row['account_id'], row['original_income_units'], row['burden_units'],
                           row['retained_units'], row['additional_units']) for row in result['provider_terms']], expected)
        self.assertEqual(result['return_sources'], {'PREPAID': refund_units, 'GIFT': 0, 'COMPENSATION': 0})
        funding = result['funding_if_recovered']
        self.assertEqual(funding['net_principal_units'] + funding['planned_provider_external_units'] + funding['required_units'],
                         funding['provider_income_units'] + funding['inviter_income_units'] + funding['store_income_units'])
        case_id = response.data['data']['id']
        proposal = flow.client.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/', {
            'request_id': 'ordinary-transfer-proposal', 'expected_version': 0, 'refund_units': refund_units,
            'provider_terms': [{'account_id': row['account_id'], 'retained_units': row['retained_units']}
                               for row in result['provider_terms']], 'explanation': '双方同意退款'}, format='json')
        self.assertEqual(proposal.data['code'], 0, proposal.data)
        for actor in (flow.boss, flow.providers[0], flow.providers[2]):
            flow.auth(actor)
            accepted = flow.client.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
                {'proposal_id': proposal.data['data']['id'], 'accepted': True}, format='json')
            self.assertEqual(accepted.data['code'], 0, accepted.data)
        self.assertEqual(accepted.data['data']['status'], 'ACCEPTED')
        buyer = get_wallet(user=flow.boss, account=flow.accounts[flow.boss.pk])
        balance_before = buyer.balance
        flow.auth(flow.staff)
        executed = flow.client.post(f'/api/admin/aftersales/{case_id}/negotiation-execution/', {
            'proposal_id': proposal.data['data']['id'], 'client_request_id': 'ordinary-transfer-execution'}, format='json')
        self.assertEqual(executed.data['code'], 0, executed.data)
        buyer.refresh_from_db()
        self.assertEqual(buyer.balance, balance_before + refund_units)
        self.assertEqual(order.refund_records.count(), 1)
        next_month = (salary.current.replace(day=28) + timedelta(days=4)).replace(day=1).strftime('%Y-%m')
        salary.assign(next_month)
        next_salary = salary.confirm(next_month)
        self.assertEqual(next_salary['lines'][0]['refund_deduction_plan']['deducted_fen'],
                         refund_units + refund_units // 10)
        self.assertEqual(salary.preview(salary.current_text)['totals'], salary_original['totals'])
        if refund_units == 8000:
            from wallet.models import RefundDepositDeficit
            self.assertEqual(dict(RefundDepositDeficit.objects.filter(refund__order=order)
                                  .values_list('account_id', 'amount')), {old.pk: 900, replacement.pk: 1101})
            self.assertEqual(get_wallet(user=flow.providers[0], account=old).balance, 0)
            self.assertEqual(get_wallet(user=flow.providers[2], account=replacement).balance, 0)
        # The unaffected teammate finishes the order; each actual segment
        # submits its own evidence and receives an independent review.
        flow.auth(flow.providers[1])
        completed = flow.client.post(f'/api/orders/orders/{order.pk}/complete/',
            {'completion_image': screenshot()}, format='multipart')
        self.assertEqual(completed.data['code'], 0, completed.data)
        for player in flow.providers:
            account = flow.accounts[player.pk]
            part = OrderParticipationSegment.objects.get(attribution__order=order, provider_account=account)
            flow.auth(player)
            endpoint = f'/api/orders/orders/{order.pk}/segment-report/'
            opened = flow.client.post(endpoint, {'segment_id': part.pk, 'action': 'open',
                'client_request_id': f'ordinary-open-{part.pk}'}, format='json')
            self.assertEqual(opened.data['code'], 0, opened.data)
            if part.released_at:
                image = flow.client.post(f'/api/orders/orders/{order.pk}/participation-evidence/',
                    {'segment_id': part.pk, 'completion_image': screenshot()}, format='multipart')
                self.assertEqual(image.data['code'], 0, image.data)
            uploaded = flow.client.post(endpoint, {'segment_id': part.pk, 'action': 'upload',
                'client_request_id': f'ordinary-upload-{part.pk}', 'image': screenshot()}, format='multipart')
            self.assertEqual(uploaded.data['code'], 0, uploaded.data)
            submitted = flow.client.post(endpoint, {'segment_id': part.pk, 'action': 'submit',
                'client_request_id': f'ordinary-submit-{part.pk}', 'version': 0}, format='json')
            self.assertEqual(submitted.data['code'], 0, submitted.data)
            flow.auth(flow.staff)
            reviewed = flow.client.post(f'/api/admin/orders/{order.pk}/segments/{part.pk}/report-review/',
                {'action': 'approve', 'version': 1}, format='json')
            self.assertEqual(reviewed.data['code'], 0, reviewed.data)
        from orders.models import Order, OrderSegmentPersonSettlement
        from orders.services import settle_order
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
            with transaction.atomic():
                locked = Order.objects.select_for_update().get(pk=order.pk)
                settle_order(locked)
            from engagement.confirmation import process_order
            confirmed = process_order(order.pk)
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        paid_people = dict(OrderSegmentPersonSettlement.objects.filter(manifest__order=order)
                           .values_list('account_id', 'amount'))
        self.assertEqual(paid_people, {old.pk: 1400 if refund_units == 1000 else 0,
            flow.accounts[flow.providers[1].pk].pk: 4500, replacement.pk: 2099 if refund_units == 1000 else 0})
        from engagement.models import ConsumptionConfirmation
        self.assertTrue(confirmed)
        consumption = ConsumptionConfirmation.objects.get(order=order)
        self.assertEqual(consumption.amount - consumption.refunded_amount, 10000 - refund_units)
