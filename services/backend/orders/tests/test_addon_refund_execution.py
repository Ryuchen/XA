import os
from unittest import skipUnless
from django.test import TestCase, override_settings
from unittest.mock import patch
from rest_framework.exceptions import ValidationError

from orders.models import OrderAddonRefund
from orders.tests import test_addon_refund_flow
from orders.tests.test_addon_configuration import staff_client
from wallet.models import RefundDepositDeficit, Transaction, get_platform_wallet
from wallet.services import get_wallet


@override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class AddonRefundExecutionTests(TestCase):
    def setUp(self):
        self.fixture = test_addon_refund_flow.AddonRefundFlowTests(methodName='test_staff_boss_and_player_confirm_one_paid_addon_without_premature_refund')
        self.fixture.setUp()
        profile = self.fixture.fixture.provider_user.escort_profile
        profile.account = self.fixture.fixture.provider
        profile.save(update_fields=['account'])

    def approve(self, request_id, amount):
        data = {**self.fixture.body, 'request_id': request_id, 'amount_raw': amount}
        proposal = self.fixture.staff.post(self.fixture.staff_url, data, format='json')
        self.assertEqual(proposal.status_code, 200, proposal.data)
        proposal_id = proposal.data['data']['id']
        user_url = self.fixture.user_url + f'{proposal_id}/'
        self.assertEqual(self.fixture.fixture.client.post(user_url, {'accepted': True}, format='json').status_code, 200)
        self.assertEqual(self.fixture.fixture.provider_client.post(user_url, {'accepted': True}, format='json').status_code, 200)
        return self.fixture.staff_url + f'{proposal_id}/execute/'

    def test_confirmed_refunds_restore_original_sources_and_deduct_pending_earnings_once(self):
        first_url = self.approve('refund-one', 500)
        first = self.fixture.staff.post(first_url, {}, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        self.assertTrue(first.data['data']['refund_executed'])
        self.assertEqual(self.fixture.staff.post(first_url, {}, format='json').status_code, 200)
        refund = OrderAddonRefund.objects.get()
        self.assertEqual((refund.amount, refund.addon_pending_deduction, refund.main_pending_deduction), (500, 500, 0))
        self.assertEqual(list(refund.earning_deductions.values_list('payment_id', 'amount')), [(self.fixture.payment.pk, 500)])
        self.fixture.wallet.refresh_from_db()
        self.assertEqual(self.fixture.wallet.balance, 2000)
        self.assertEqual(Transaction.objects.filter(wallet=self.fixture.wallet, tx_type='REFUND').count(), 1)
        history = self.fixture.fixture.client.get('/api/wallet/transactions/')
        self.assertEqual(history.status_code, 200, history.data)
        own_refund = next(item for item in history.data['data']['transactions'] if item['id'] == refund.buyer_transaction_id)
        self.assertEqual((own_refund['order_id'], own_refund['order_no'], own_refund['addon']['refund_id']),
                         (self.fixture.fixture.order.pk, self.fixture.fixture.order.order_no, refund.pk))
        finance = staff_client(['transaction:view'])
        records = finance.get('/api/admin/transactions/', {'order_no_exact': self.fixture.fixture.order.order_no})
        self.assertEqual(records.status_code, 200, records.data)
        matched = {item['id']: item for item in records.data['data']['list']}
        self.assertIn(self.fixture.payment.buyer_transaction_id, matched)
        self.assertEqual((matched[refund.buyer_transaction_id]['related_order_id'],
                          matched[refund.buyer_transaction_id]['addon']['refund_id']),
                         (self.fixture.fixture.order.pk, refund.pk))

        second_url = self.approve('refund-two', 1000)
        second = self.fixture.staff.post(second_url, {}, format='json')
        self.assertEqual(second.status_code, 200, second.data)
        refund = OrderAddonRefund.objects.order_by('pk').last()
        self.assertEqual((refund.addon_pending_deduction, refund.main_pending_deduction), (700, 300))
        self.fixture.wallet.refresh_from_db()
        self.assertEqual(self.fixture.wallet.balance, 3000)
        self.assertEqual(dict(self.fixture.payment.allocations.values_list('kind', 'refunded_amount')),
                         {'PREPAID': 1200, 'GIFT': 200, 'COMPENSATION': 100})
        self.assertEqual(OrderAddonRefund.objects.count(), 2)

    def test_unconfirmed_proposal_and_unprivileged_operator_do_not_refund(self):
        proposal = self.fixture.staff.post(self.fixture.staff_url, self.fixture.body, format='json')
        url = self.fixture.staff_url + f"{proposal.data['data']['id']}/execute/"
        self.assertEqual(self.fixture.fixture.denied.post(url, {}, format='json').status_code, 403)
        self.assertEqual(self.fixture.staff.post(url, {}, format='json').status_code, 400)
        self.fixture.wallet.refresh_from_db()
        self.assertEqual(self.fixture.wallet.balance, 1500)
        self.assertFalse(OrderAddonRefund.objects.exists())

    def test_funding_rebalance_failure_rolls_back_wallet_and_refund_sources(self):
        url = self.approve('failed-rebalance', 500)
        with patch('orders.addon_funding_release.record_addon_funding_release',
                   side_effect=ValidationError('补资核算失败')):
            response = self.fixture.staff.post(url, {}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.fixture.wallet.refresh_from_db()
        self.assertEqual(self.fixture.wallet.balance, 1500)
        self.assertFalse(OrderAddonRefund.objects.exists())
        self.assertEqual(sum(self.fixture.payment.allocations.values_list('refunded_amount', flat=True)), 0)
        self.assertEqual(Transaction.objects.filter(wallet=self.fixture.wallet, tx_type='REFUND').count(), 0)

    def test_refund_uses_other_addon_pending_earnings_before_main_order(self):
        client = self.fixture.fixture.client
        self.fixture.case.status = 'RESOLVED'
        self.fixture.case.save(update_fields=['status'])
        payload = {**self.fixture.fixture.payload, 'request_id': 'second-addon', 'quantity': 1,
                   'expected_amount': 500, 'gift_balance_amount': 100, 'compensation_balance_amount': 0}
        response = client.post(self.fixture.fixture.url, payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        second_intent = response.data['data']['id']
        response = client.post(self.fixture.fixture.url + f'{second_intent}/pay/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        second_payment = response.data['data']['payment_id']
        self.fixture.case.status = 'OPEN'
        self.fixture.case.save(update_fields=['status'])
        url = self.approve('refund-whole-first', 1500)
        response = self.fixture.staff.post(url, {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        refund = OrderAddonRefund.objects.get()
        self.assertEqual((refund.addon_pending_deduction, refund.main_pending_deduction), (1500, 0))
        self.assertEqual(dict(refund.earning_deductions.values_list('payment_id', 'amount')),
                         {self.fixture.payment.pk: 1200, second_payment: 300})

    @override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '加购退款结算 provider/gift_compensation/押金缺口分摊属正式资金口径深水区（18-盘点 加购退款结算待联调，口径②家族待用户裁定）；最终联调启用')
    def test_refund_uses_wallet_then_verified_cash_deposit_and_records_deficit(self):
        profile = self.fixture.fixture.provider_user.escort_profile
        profile.deposit_mode = 'CASH'
        profile.deposit_paid = 0
        profile.save(update_fields=['deposit_mode', 'deposit_paid'])
        assignment = self.fixture.fixture.order.providers.get(provider_account=self.fixture.fixture.provider)
        assignment.provider_income = 100
        assignment.save(update_fields=['provider_income'])
        wallet = get_wallet(account=self.fixture.fixture.provider, user=self.fixture.fixture.provider_user)
        wallet.balance = 100
        wallet.save(update_fields=['balance'])
        registrar = staff_client(['escort:view', 'escort:deposit_topup'])
        topup = registrar.post(f'/api/admin/escorts/{profile.pk}/deposit-topups/', {
            'amount': 100, 'reason': '核验线下补差', 'reference': 'addon-deposit-1',
            'client_request_id': 'addon-cash-1'}, format='multipart')
        self.assertEqual(topup.status_code, 200, topup.data)
        platform_before = get_platform_wallet().balance
        url = self.approve('refund-uses-deposit', 1500)
        response = self.fixture.staff.post(url, {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        refund = OrderAddonRefund.objects.get()
        self.assertEqual((refund.addon_pending_deduction, refund.main_pending_deduction,
                          refund.wallet_deduction, refund.deposit_deduction), (1200, 100, 100, 100))
        wallet.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual((wallet.balance, profile.deposit_paid, get_platform_wallet().balance),
                         (0, 0, platform_before - 100))
        history = self.fixture.fixture.provider_client.get('/api/wallet/transactions/')
        self.assertEqual(history.status_code, 200, history.data)
        own_refund = next(item for item in history.data['data']['transactions'] if item['id'] == refund.provider_transaction_id)
        self.assertEqual((own_refund['order_id'], own_refund['addon']['refund_id']),
                         (self.fixture.fixture.order.pk, refund.pk))
        deficit = RefundDepositDeficit.objects.get(addon_refund=refund)
        self.assertEqual((deficit.amount, deficit.remaining), (100, 100))
        finance = staff_client(['transaction:view'])
        records = finance.get('/api/admin/transactions/', {'order_no_exact': self.fixture.fixture.order.order_no})
        self.assertEqual(records.status_code, 200, records.data)
        matched = {item['id']: item for item in records.data['data']['list']}
        self.assertEqual(matched[deficit.source_store_transaction_id]['addon']['refund_id'], refund.pk)
        self.assertEqual(self.fixture.staff.post(url, {}, format='json').status_code, 200)
        self.assertEqual(RefundDepositDeficit.objects.filter(addon_refund=refund).count(), 1)
        from orders.services import _credit_wallet
        from orders.tests.factories import make_completed_order
        from stores.db import transaction
        from wallet.refund_deposit import apply_released_income
        later = make_completed_order(self.fixture.fixture.boss_user, self.fixture.fixture.provider_user,
            provider_account=self.fixture.fixture.provider, amount=500, payment_status='PAID')
        with transaction.atomic():
            income = _credit_wallet(order=later, amount=200, tx_type='INCOME',
                remark=f'订单收入：{later.order_no}', event='test.order.income', trace_id='addon-deficit-test',
                user_id=profile.user_id, account_id=profile.account_id)
            wallet.refresh_from_db()
            self.assertEqual(apply_released_income(source=income, profile=profile, wallet=wallet), 100)
            self.assertEqual(apply_released_income(source=income, profile=profile, wallet=wallet), 100)
        deficit.refresh_from_db()
        profile.refresh_from_db()
        wallet.refresh_from_db()
        self.assertEqual((deficit.remaining, profile.deposit_paid, wallet.balance), (0, 100, 100))

    @override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '加购退款结算 provider/gift_compensation/押金缺口分摊属正式资金口径深水区（18-盘点 加购退款结算待联调，口径②家族待用户裁定）；最终联调启用')
    def test_insufficient_pending_wallet_and_deposit_leave_everything_unmodified(self):
        profile = self.fixture.fixture.provider_user.escort_profile
        profile.deposit_paid = 0
        profile.save(update_fields=['deposit_paid'])
        self.fixture.fixture.order.providers.filter(provider_account=self.fixture.fixture.provider).update(provider_income=100)
        url = self.approve('insufficient-refund', 1500)
        response = self.fixture.staff.post(url, {}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.fixture.wallet.refresh_from_db()
        self.assertEqual(self.fixture.wallet.balance, 1500)
        self.assertFalse(OrderAddonRefund.objects.exists())
        self.assertEqual(sum(self.fixture.payment.allocations.values_list('refunded_amount', flat=True)), 0)
