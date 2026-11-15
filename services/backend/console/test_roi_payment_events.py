"""Operating revenue follows real customer payment and refund event dates."""
from datetime import datetime, time, timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from console.test_operating_workflow import seed_payroll_fixture
from orders.models import Order
from orders.tests import test_negotiation_termination as negotiation_flow
from orders.tests import test_release_flow as release_flow
from orders.tests.test_provider_aftersale_status import image
from wallet.models import Transaction


@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
                   BOSS_FUNDING_SELECTION_ENABLED=True, ORDINARY_SERVICE_FUNDING_ENABLED=True,
                   ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class RoiPaymentEventsTest(APITransactionTestCase):
    def setUp(self):
        negotiation_flow.NegotiationTerminationActualOrderTest.setUp(self)
        from engagement.models import RechargePackage
        from wallet.models import Wallet
        Wallet.objects.filter(user=self.boss).update(balance=0)
        RechargePackage.objects.create(amount=50000, gift_amount=0, active=True)
        role = self.staff.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'wallet:recharge']
        role.save(update_fields=['permissions'])
        self.login(self.staff)
        self.ok(self.client.post('/api/admin/wallets/recharge/', {
            'account_id': self.accounts[self.boss.pk].pk, 'amount': 50000, 'gift_amount': 0,
            'trade_no': 'roi-events-principal', 'client_request_id': 'roi-events-principal',
            'proof_image': image()}, format='multipart'))

    login = negotiation_flow.NegotiationTerminationActualOrderTest.login
    ok = negotiation_flow.NegotiationTerminationActualOrderTest.ok

    @override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True)
    def test_refund_burden_includes_verified_cash_deposit_deduction(self):
        seed_payroll_fixture()
        profile = self.provider.escort_profile
        profile.deposit_mode = 'CASH'
        profile.deposit_paid = 0
        profile.save(update_fields=['deposit_mode', 'deposit_paid'])
        role = self.staff.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'support:edit', 'order:refund', 'roi:view', 'escort:deposit_topup']
        role.save(update_fields=['permissions'])
        self.login(self.staff)
        self.ok(self.client.post(f'/api/admin/escorts/{profile.pk}/deposit-topups/', {
            'amount': 40000, 'reason': '隔离测试线下押金', 'reference': 'ROI-DEP',
            'client_request_id': 'roi-deposit-topup'}, format='multipart'))
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            'provider_id': self.accounts[self.provider.pk].pk, 'client_request_id': 'roi-deposit-order'}, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': image()}, format='multipart'))
        self.login(self.staff)
        case = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {'order_id': order_id,
            'affected_provider_account_ids': [self.accounts[self.provider.pk].pk], 'reason': '押金承担退款',
            'client_request_id': 'roi-deposit-case'}, format='json'))
        proposal = self.ok(self.client.post(f"/api/admin/aftersales/{case['id']}/negotiation-proposal/", {
            'request_id': 'roi-deposit-plan', 'expected_version': 0, 'refund_units': 9000,
            'provider_terms': [{'account_id': self.accounts[self.provider.pk].pk, 'retained_units': 0}],
            'explanation': '收益和押金承担退款'}, format='json'))
        for user in (self.boss, self.provider):
            self.login(user)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
                {'proposal_id': proposal['id'], 'accepted': True}, format='json'))
        self.login(self.staff)
        self.ok(self.client.post(f"/api/admin/aftersales/{case['id']}/negotiation-execution/", {
            'proposal_id': proposal['id'], 'client_request_id': 'roi-deposit-refund'}, format='json'))
        profile.refresh_from_db()
        self.assertEqual(profile.deposit_paid, 39000)
        today = timezone.localdate()
        previous_day = today.replace(day=1) - timedelta(days=1)
        earlier = timezone.make_aware(datetime.combine(previous_day, time(hour=10)))
        Transaction.objects.filter(order_id=order_id, tx_type='PAY', amount__lt=0).update(created_at=earlier)
        Order.objects.filter(pk=order_id).update(created_at=earlier)
        prior = self.ok(self.client.get('/api/admin/operating-roi/', {
            'start_date': previous_day.isoformat(), 'end_date': previous_day.isoformat()}))['operating']
        later = self.ok(self.client.get('/api/admin/operating-roi/', {
            'start_date': today.isoformat(), 'end_date': today.isoformat()}))['operating']
        self.assertEqual((prior['net_consumption_fen'], prior['provider_cost_fen']), (10000, 8000))
        self.assertEqual((later['net_consumption_fen'], later['provider_cost_fen']), (-9000, -9000))

    def test_payment_and_negotiated_refund_are_reported_in_their_own_months(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from orders.tests.factories import make_user
        seed_payroll_fixture()
        boss_account = self.accounts[self.boss.pk]
        boss_account.inviter = get_or_create_account_for_legacy_user(make_user())
        boss_account.inviter_commission_rate = 10
        boss_account.save(update_fields=['inviter', 'inviter_commission_rate'])
        role = self.staff.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'support:edit', 'order:refund', 'roi:view']
        role.save(update_fields=['permissions'])
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            'provider_id': self.accounts[self.provider.pk].pk, 'client_request_id': 'roi-event-order'}, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': image()}, format='multipart'))
        self.login(self.staff)
        case = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {'order_id': order_id,
            'affected_provider_account_ids': [self.accounts[self.provider.pk].pk], 'reason': '核对退款月份',
            'client_request_id': 'roi-event-case'}, format='json'))
        proposal = self.ok(self.client.post(f"/api/admin/aftersales/{case['id']}/negotiation-proposal/", {
            'request_id': 'roi-event-proposal', 'expected_version': 0, 'refund_units': 2500,
            'provider_terms': [{'account_id': self.accounts[self.provider.pk].pk, 'retained_units': 5500}],
            'explanation': '退款由陪玩承担'}, format='json'))
        for user in (self.boss, self.provider):
            self.login(user)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
                {'proposal_id': proposal['id'], 'accepted': True}, format='json'))
        self.login(self.staff)
        self.ok(self.client.post(f"/api/admin/aftersales/{case['id']}/negotiation-execution/", {
            'proposal_id': proposal['id'], 'client_request_id': 'roi-event-refund'}, format='json'))

        today = timezone.localdate()
        previous_day = today.replace(day=1) - timedelta(days=1)
        earlier = timezone.make_aware(datetime.combine(previous_day, time(hour=10)))
        Transaction.objects.filter(order_id=order_id, tx_type='PAY', amount__lt=0).update(created_at=earlier)
        Order.objects.filter(pk=order_id).update(created_at=earlier)
        previous = self.ok(self.client.get('/api/admin/operating-roi/', {
            'start_date': previous_day.isoformat(), 'end_date': previous_day.isoformat()}))
        current = self.ok(self.client.get('/api/admin/operating-roi/', {
            'start_date': today.isoformat(), 'end_date': today.isoformat()}))
        self.assertEqual(previous['roas']['net_consumption_fen'], 10000)
        self.assertEqual(current['roas']['net_consumption_fen'], -2500)
        self.assertEqual(previous['operating']['net_consumption_fen'], 10000)
        self.assertEqual(current['operating']['net_consumption_fen'], -2500)
        self.assertEqual((previous['operating']['paid_consumption_fen'], previous['operating']['refunded_consumption_fen']), (10000, 0))
        self.assertEqual((current['operating']['paid_consumption_fen'], current['operating']['refunded_consumption_fen']), (0, 2500))
        self.assertEqual(previous['operating']['provider_cost_fen'], 8000, previous)
        self.assertEqual(current['operating']['provider_cost_fen'], -2500, current)
        self.assertEqual(previous['operating']['referral_cost_fen'], 1000, previous)
        self.assertEqual(current['operating']['referral_cost_fen'], -250, current)
        self.assertFalse(previous['operating']['complete'])
        self.assertFalse(current['operating']['complete'])
        self.assertIsNone(previous['operating']['profit_fen'])
        self.assertIsNone(current['operating']['profit_fen'])
        from orders.models import OrderRefund
        refund = OrderRefund.objects.get(order_id=order_id)
        original_effects = refund.benefit_effects
        refund.benefit_effects = {**original_effects, 'negotiation_execution_version': 99}
        refund.save(update_fields=['benefit_effects'])
        disputed = self.ok(self.client.get('/api/admin/operating-roi/', {
            'start_date': today.isoformat(), 'end_date': today.isoformat()}))['operating']
        self.assertEqual(disputed['net_consumption_fen'], -2500)
        self.assertIsNone(disputed['provider_cost_fen'])
        self.assertIn(order_id, disputed['unknown_cost_order_ids'])
        refund.benefit_effects = original_effects
        refund.save(update_fields=['benefit_effects'])
        from console.models import BusinessDictionaryItem, SalaryPlan
        role.permissions = [*role.permissions, 'salary:edit', 'cost:edit']
        role.save(update_fields=['permissions'])
        plan = SalaryPlan.objects.create(position=BusinessDictionaryItem.objects.get(type='SALARY_POSITION', code='FINANCE'),
                                         name='跨月核算零薪资')
        category = BusinessDictionaryItem.objects.get(type='COST_CATEGORY', code='AD_SPEND')
        channel = BusinessDictionaryItem.objects.get(type='AD_CHANNEL', code='UNKNOWN')
        for day in (previous_day, today):
            assigned = self.client.post('/api/admin/salary-assignments/', {
                'month': day.strftime('%Y-%m'), 'account': self.accounts[self.staff.pk].pk, 'plan': plan.pk}, format='json')
            self.assertEqual((assigned.status_code, assigned.data['code']), (201, 0), assigned.data)
            ad = self.client.post('/api/admin/operating-costs/', {
                'day': day.isoformat(), 'category': category.pk, 'channel': channel.pk, 'amount_fen': 0,
                'note': '隔离测试：未投流', 'client_request_id': f'roi-zero-ad-{day}'}, format='json')
            self.assertIn(ad.status_code, (200, 201), ad.data)
            self.assertEqual(ad.data['code'], 0, ad.data)
        prior_estimate = self.ok(self.client.get('/api/admin/operating-roi/', {
            'start_date': previous_day.isoformat(), 'end_date': previous_day.isoformat()}))['provisional']
        refund_estimate = self.ok(self.client.get('/api/admin/operating-roi/', {
            'start_date': today.isoformat(), 'end_date': today.isoformat()}))['provisional']
        self.assertTrue(prior_estimate['complete'], prior_estimate)
        self.assertTrue(refund_estimate['complete'], refund_estimate)
        self.assertEqual((prior_estimate['total_cost_fen'], prior_estimate['profit_fen']), (9000, 1000))
        self.assertEqual((refund_estimate['total_cost_fen'], refund_estimate['profit_fen']), (-2750, 250))
        # Once the original service is actually settled, the same historical
        # event periods and the unrecovered net referral liability stay stable.
        from unittest.mock import patch
        from engagement.confirmation import process_order
        from wallet.models import ProviderReport
        report = ProviderReport.objects.get(order_id=order_id, provider=self.provider)
        self.login(self.provider)
        for kind in ('COMPLETION', 'RESULT'):
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                {'kind': kind, 'image': image()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.staff)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        report.refresh_from_db()
        from stores.context import resolve_store, store_context
        with store_context(resolve_store()), patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
            self.assertTrue(process_order(order_id))
        prior_after_settlement = self.ok(self.client.get('/api/admin/operating-roi/', {
            'start_date': previous_day.isoformat(), 'end_date': previous_day.isoformat()}))
        refund_after_settlement = self.ok(self.client.get('/api/admin/operating-roi/', {
            'start_date': today.isoformat(), 'end_date': today.isoformat()}))
        self.assertEqual((prior_after_settlement['operating']['provider_cost_fen'],
                          prior_after_settlement['operating']['referral_cost_fen']), (8000, 1000))
        self.assertEqual((refund_after_settlement['operating']['provider_cost_fen'],
                          refund_after_settlement['operating']['referral_cost_fen']), (-2500, -250))


@override_settings(BOSS_FUNDING_SELECTION_ENABLED=True, ORDINARY_SERVICE_FUNDING_ENABLED=True,
                   ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
                   NEGOTIATION_PROPOSAL_ENABLED=True,
                   NEGOTIATION_REFUND_EXECUTION_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class RoiFundingSourcesTest(APITransactionTestCase):
    setUp = release_flow.ReleaseFlowTest.setUp
    login = release_flow.ReleaseFlowTest.login
    ok = release_flow.ReleaseFlowTest.ok
    recharge = release_flow.ReleaseFlowTest.recharge
    finish = release_flow.ReleaseFlowTest.finish

    def test_used_gift_balance_is_a_separate_operating_cost(self):
        from console.models import BusinessDictionaryItem, SalaryPlan
        seed_payroll_fixture()
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'roi:view', 'salary:edit', 'cost:edit']
        role.save(update_fields=['permissions'])
        self.login(self.admin)
        today = timezone.localdate()
        position = BusinessDictionaryItem.objects.get(type='SALARY_POSITION', code='FINANCE')
        plan = SalaryPlan.objects.create(position=position, name='隔离验证零薪资')
        assigned = self.client.post('/api/admin/salary-assignments/', {
            'month': today.strftime('%Y-%m'), 'account': self.accounts[self.admin.pk].pk, 'plan': plan.pk}, format='json')
        self.assertEqual((assigned.status_code, assigned.data['code']), (201, 0), assigned.data)
        cost = self.client.post('/api/admin/operating-costs/', {
            'day': today.isoformat(), 'category': BusinessDictionaryItem.objects.get(type='COST_CATEGORY', code='AD_SPEND').pk,
            'channel': BusinessDictionaryItem.objects.get(type='AD_CHANNEL', code='UNKNOWN').pk,
            'amount_fen': 0, 'note': '隔离测试：当日未投流', 'client_request_id': 'roi-gift-ad-zero'}, format='json')
        self.assertIn(cost.status_code, (200, 201), cost.data)
        self.assertEqual(cost.data['code'], 0, cost.data)
        self.ok(self.recharge())
        self.login(self.boss)
        data = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'support_contact_id': self.card.pk, 'client_request_id': 'roi-gift-source',
            'gift_balance_amount': 1000, 'compensation_balance_amount': 0}, format='json'))
        self.login(self.admin)
        day = timezone.localdate().isoformat()
        result = self.ok(self.client.get('/api/admin/operating-roi/', {'start_date': day, 'end_date': day}))
        operating = result['operating']
        self.assertEqual((operating['net_consumption_fen'], operating['provider_cost_fen'],
                          operating['gift_compensation_cost_fen']), (10000, 8000, 1000), result)
        self.assertTrue(result['provisional']['complete'], result)
        self.assertEqual((result['provisional']['total_cost_fen'], result['provisional']['profit_fen']), (9000, 1000))
        report = self.finish(Order.objects.get(pk=data['id']))
        self.login(self.admin)
        result = self.ok(self.client.get('/api/admin/operating-roi/', {'start_date': day, 'end_date': day}))
        self.assertEqual(result['operating']['provider_cost_fen'], 8000, result)
        self.login(self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                                 {'kind': 'RESULT', 'image': image()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        report.refresh_from_db()
        from unittest.mock import patch
        from engagement.confirmation import process_order
        from stores.context import resolve_store, store_context
        with store_context(resolve_store()), patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
            self.assertTrue(process_order(data['id']))
        result = self.ok(self.client.get('/api/admin/operating-roi/', {'start_date': day, 'end_date': day}))
        self.assertEqual((result['operating']['provider_cost_fen'], result['operating']['gift_compensation_cost_fen']),
                         (8000, 1000), result)
        self.assertEqual(result['provisional']['profit_fen'], 1000, result)

    def test_cross_month_refund_reverses_gift_cost_only_when_gift_is_returned(self):
        from engagement.models import RechargePackage
        seed_payroll_fixture()
        RechargePackage.objects.filter(amount=20000).update(gift_amount=4000)
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'roi:view', 'support:edit', 'order:refund']
        role.save(update_fields=['permissions'])
        self.login(self.admin)
        self.ok(self.client.post('/api/admin/wallets/recharge/', {
            'account_id': self.accounts[self.boss.pk].pk, 'amount': 20000, 'gift_amount': 4000,
            'trade_no': 'roi-gift-receipt', 'client_request_id': 'roi-gift-receipt',
            'proof_image': release_flow.screenshot()}, format='multipart'))
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'support_contact_id': self.card.pk, 'client_request_id': 'roi-gift-refund-order',
            'gift_balance_amount': 4000, 'compensation_balance_amount': 0}, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': image()}, format='multipart'))
        self.login(self.admin)
        case = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {'order_id': order_id,
            'affected_provider_account_ids': [self.accounts[self.provider.pk].pk], 'reason': '核对赠送余额退款',
            'client_request_id': 'roi-gift-refund-case'}, format='json'))
        proposal = self.ok(self.client.post(f"/api/admin/aftersales/{case['id']}/negotiation-proposal/", {
            'request_id': 'roi-gift-refund-plan', 'expected_version': 0, 'refund_units': 7000,
            'provider_terms': [{'account_id': self.accounts[self.provider.pk].pk, 'retained_units': 1000}],
            'explanation': '退回本金和部分赠送余额'}, format='json'))
        for user in (self.boss, self.provider):
            self.login(user)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
                {'proposal_id': proposal['id'], 'accepted': True}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f"/api/admin/aftersales/{case['id']}/negotiation-execution/", {
            'proposal_id': proposal['id'], 'client_request_id': 'roi-gift-refund-post'}, format='json'))
        today = timezone.localdate()
        previous_day = today.replace(day=1) - timedelta(days=1)
        earlier = timezone.make_aware(datetime.combine(previous_day, time(hour=10)))
        Transaction.objects.filter(order_id=order_id, tx_type='PAY', amount__lt=0).update(created_at=earlier)
        Order.objects.filter(pk=order_id).update(created_at=earlier)
        prior = self.ok(self.client.get('/api/admin/operating-roi/', {
            'start_date': previous_day.isoformat(), 'end_date': previous_day.isoformat()}))['operating']
        later = self.ok(self.client.get('/api/admin/operating-roi/', {
            'start_date': today.isoformat(), 'end_date': today.isoformat()}))['operating']
        self.assertEqual((prior['net_consumption_fen'], prior['provider_cost_fen'], prior['gift_compensation_cost_fen']),
                         (10000, 8000, 4000))
        self.assertEqual((later['net_consumption_fen'], later['provider_cost_fen'], later['gift_compensation_cost_fen']),
                         (-7000, -7000, -1000))
