"""Refund eligibility follows real store orders, cases and income holds."""
import os
from datetime import timedelta
from unittest import skipUnless

from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import AfterSale, Order, OrderProvider, Reservation
from orders.tests.factories import make_order, make_provider, make_superuser, make_user, make_wallet
from wallet.models import IncomeHold, ProviderReport, SystemConfig, Transaction
from wallet.test_deposit_refund import bind_account, fund_platform_deposit_pool, seed_closed_order


class DepositRefundEligibilityTest(APITestCase):
    def setUp(self):
        self.admin = make_superuser()
        self.provider = make_provider(deposit_paid=3000)
        self.account = bind_account(self.provider)
        self.wallet = make_wallet(self.provider, balance=1000)
        self.wallet.account = self.account; self.wallet.save(update_fields=['account'])
        fund_platform_deposit_pool(3000)
        self.boss = make_user()
        self.boss_account = get_or_create_account_for_legacy_user(self.boss)
        self.client.force_authenticate(self.admin)
        self.url = f'/api/admin/escorts/{self.provider.escort_profile.pk}/deposit-refund/'

    def assert_blocked(self, key):
        before = (self.wallet.balance, self.provider.escort_profile.deposit_paid, Transaction.objects.count())
        preview = self.client.get(self.url).data['data']
        self.assertFalse(preview['can_refund'])
        self.assertGreater(preview[key], 0)
        result = self.client.post(self.url, {'amount':1000, 'reason':'隔离测试', 'client_request_id':'refund-after-block'}, format='json')
        self.assertEqual(result.data['code'], 409, result.data)
        self.assertGreater(result.data['data'][key], 0)
        self.wallet.refresh_from_db(); self.provider.escort_profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.provider.escort_profile.deposit_paid, Transaction.objects.count()), before)

    def settle_share(self, share):
        before = self.wallet.balance
        self.wallet.balance += share.provider_income
        self.wallet.save(update_fields=['balance'])
        Transaction.objects.create(wallet=self.wallet, order=share.order, tx_type='INCOME',
            amount=share.provider_income, balance_before=before, balance_after=self.wallet.balance)
        share.settled_at = timezone.now()
        share.save(update_fields=['settled_at'])

    def test_open_case_on_transferred_provider_blocks_even_when_order_is_settled(self):
        other = make_provider()
        order = make_order(self.boss, provider=other, status=Order.Status.COMPLETED,
                           completed_at=timezone.now(), settled_at=timezone.now())
        order.completed_at = timezone.now(); order.save(update_fields=['completed_at'])
        share = OrderProvider.objects.create(order=order, provider=self.provider, provider_account=self.account,
            settlement_base=5000, provider_income=4000, released_at=timezone.now())
        self.settle_share(share)
        case = AfterSale.objects.create(order=order, account=self.boss_account, reason='等待核实')
        self.assert_blocked('open_aftersale_count')
        case.status = 'RESOLVED'; case.resolved_at = timezone.now(); case.save(update_fields=['status', 'resolved_at'])
        preview = self.client.get(self.url).data['data']
        self.assertEqual(preview['open_aftersale_count'], 0)
        self.assertEqual(preview['wait_status'], 'WAITING')
        self.assertEqual(self.client.post(self.url, {'amount':1000, 'reason':'隔离测试',
            'client_request_id':'refund-after-block'}, format='json').data['code'], 409)

    def test_unsettled_completed_share_blocks_even_if_transferred_out(self):
        other = make_provider()
        order = make_order(self.boss, provider=other, status=Order.Status.COMPLETED, completed_at=timezone.now())
        order.completed_at = timezone.now(); order.save(update_fields=['completed_at'])
        share = OrderProvider.objects.create(order=order, provider=self.provider, provider_account=self.account,
            settlement_base=5000, provider_income=4000, released_at=timezone.now())
        self.assert_blocked('pending_income_count')
        self.settle_share(share)
        self.assertEqual(self.client.get(self.url).data['data']['wait_status'], 'WAITING')

    def test_transferred_out_provider_waits_until_entire_order_ends(self):
        other = make_provider()
        order = make_order(self.boss, provider=other, status=Order.Status.IN_SERVICE)
        OrderProvider.objects.create(order=order, provider=self.provider, provider_account=self.account,
            settlement_base=0, provider_income=0, released_at=timezone.now())
        self.assert_blocked('active_order_count')

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '释放收入后退款等待态须清算证据可核对（needs_review=False 依赖正式结算凭证，18-盘点订单售后资金组合）；最终联调启用')
    def test_unreleased_income_blocks_but_nonincome_withdraw_freeze_does_not(self):
        self.wallet.frozen_amount = 700; self.wallet.save(update_fields=['frozen_amount'])
        tx = Transaction.objects.create(wallet=self.wallet, tx_type='INCOME', amount=200,
            balance_before=800, balance_after=1000)
        hold = IncomeHold.objects.create(wallet=self.wallet, income_transaction=tx, amount=200,
            release_at=timezone.now()+timedelta(hours=1))
        self.assert_blocked('unreleased_income_count')
        hold.release_at = timezone.now(); hold.released_at = timezone.now()
        hold.save(update_fields=['release_at', 'released_at'])
        self.assertEqual(self.client.get(self.url).data['data']['wait_status'], 'WAITING')

    def test_pending_legacy_orderless_report_blocks(self):
        report = ProviderReport.objects.create(provider=self.provider, provider_account=self.account,
            order=None, game_name='隔离测试', amount=1000, status='PENDING')
        self.assert_blocked('pending_income_count')
        report.status = 'REJECTED'; report.audited_at = timezone.now(); report.save(update_fields=['status', 'audited_at'])
        self.assertEqual(self.client.get(self.url).data['data']['wait_status'], 'WAITING')

    def test_verified_zero_income_report_does_not_remain_pending_forever(self):
        audited = timezone.now()-timedelta(days=8)
        report = ProviderReport.objects.create(provider=self.provider, provider_account=self.account,
            order=None, game_name='零收益报单', amount=1000, payout_amount=0,
            status='APPROVED', audited_at=audited)
        ProviderReport.objects.filter(pk=report.pk).update(created_at=audited-timedelta(hours=1))
        preview = self.client.get(self.url).data['data']
        self.assertEqual((preview['pending_income_count'], preview['wait_status']), (0, 'READY'))

    def test_another_providers_open_case_does_not_block(self):
        seed_closed_order(self.provider, self.account)
        other = make_provider()
        order = make_order(self.boss, provider=other, status=Order.Status.COMPLETED, settled_at=timezone.now())
        AfterSale.objects.create(order=order, account=self.boss_account, reason='其他陪玩')
        self.assertTrue(self.client.get(self.url).data['data']['can_refund'])

    def test_same_order_other_providers_scoped_case_does_not_block(self):
        other = make_provider()
        other_account = bind_account(other)
        order = make_order(self.boss, provider=other, provider_account=other_account,
                           status=Order.Status.COMPLETED, completed_at=timezone.now(), settled_at=timezone.now())
        order.completed_at = timezone.now(); order.save(update_fields=['completed_at'])
        share = OrderProvider.objects.create(order=order, provider=self.provider, provider_account=self.account,
            settlement_base=5000, provider_income=4000)
        self.settle_share(share)
        OrderProvider.objects.create(order=order, provider=other, provider_account=other_account,
            settlement_base=5000, provider_income=4000, settled_at=timezone.now())
        case = AfterSale.objects.create(order=order, account=self.boss_account, reason='只涉及另一位陪玩',
                                       affected_provider_account_ids=[other_account.pk])
        preview = self.client.get(self.url).data['data']
        self.assertEqual(preview['open_aftersale_count'], 0)
        self.assertEqual(preview['wait_status'], 'WAITING')
        case.affected_provider_account_ids = None
        case.save(update_fields=['affected_provider_account_ids'])
        self.assert_blocked('open_aftersale_count')

    def test_preview_does_not_guarantee_future_refund(self):
        seed_closed_order(self.provider, self.account)
        self.assertTrue(self.client.get(self.url).data['data']['can_refund'])
        make_order(self.boss, provider=self.provider, provider_account=self.account, status=Order.Status.IN_SERVICE)
        result = self.client.post(self.url, {'amount':1000, 'reason':'隔离测试', 'client_request_id':'stale-preview'}, format='json')
        self.assertEqual(result.data['code'], 409)
        self.assertEqual(result.data['data']['active_order_count'], 1)

    def test_unstarted_paid_appointment_still_blocks_deposit_refund(self):
        order = make_order(self.boss, provider=self.provider, provider_account=self.account,
                           customer_account=self.boss_account, status=Order.Status.GRABBED,
                           payment_status=Order.PaymentStatus.PAID)
        start = timezone.now() + timedelta(days=1)
        Reservation.objects.create(customer=self.boss, customer_account=self.boss_account,
            provider=self.provider, provider_account=self.account, service=order.service, order=order,
            status=Reservation.Status.CONVERTED, start_time=start, end_time=start+timedelta(hours=1),
            duration_minutes=60)
        from orders.services import count_capacity_orders
        self.assertEqual(count_capacity_orders(self.account), 0)
        self.assert_blocked('active_order_count')

    def test_invalid_capacity_rule_does_not_block_otherwise_valid_deposit_refund(self):
        seed_closed_order(self.provider, self.account)
        from orders.capacity import CONFIG_KEY
        SystemConfig.objects.create(key=CONFIG_KEY, value='invalid')
        result = self.client.post(self.url, {'amount':1000, 'reason':'隔离测试',
            'client_request_id':'capacity-invalid-deposit'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
