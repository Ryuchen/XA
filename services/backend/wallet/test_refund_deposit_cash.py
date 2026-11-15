"""Audited offline cash receipts settle real refund-deficit records."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from orders.models import OrderRefund
from orders.tests.factories import make_order
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.tests_hosts import xinan_store
from wallet.models import (CashDepositTopup, RefundDepositCashRepayment, RefundDepositRecoveryPermit,
                           SystemConfig, Transaction, get_platform_wallet)
from wallet import test_cash_deposit_topup as cash_fixtures
from wallet import test_refund_deposit_direct as direct_fixtures


@override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class CashRefundDeficitTest(TestCase):
    post = cash_fixtures.CashDepositTopupTest.post
    charge = direct_fixtures.DirectRefundDepositTest.charge

    def setUp(self):
        direct_fixtures.DirectRefundDepositTest.setUp(self)
        SystemConfig.objects.update_or_create(key='provider_deposit_targets', defaults={'value': json.dumps(
            {'cash_target': 10000, 'earnings_target': 80000, 'version': 1})})

    def two_deficits(self):
        with transaction.atomic():
            first = self.charge(200)
            second_refund = OrderRefund.objects.create(order=self.order, amount=1000, reason='第二笔退款',
                operator=self.operator, operator_account=self.operator_account)
            from wallet.refund_deposit import charge_refund_deposit
            second = charge_refund_deposit(refund=second_refund, profile=self.profile, amount=300,
                operator=self.operator, operator_account=self.operator_account)
        return first, second

    def permit(self):
        from orders.services import ensure_provider_dispatch_locks, lock_provider_dispatch
        from wallet.refund_recovery_order import reserve
        recovery = make_order(self.order.customer, provider=self.provider, provider_account=self.account,
                              status='GRABBED', payment_status='PAID')
        ensure_provider_dispatch_locks([self.account.pk])
        with transaction.atomic():
            lock_provider_dispatch([self.account.pk])
            return reserve(self.profile, recovery)

    def test_cash_receipts_clear_oldest_gap_and_close_permit_only_when_fully_paid(self):
        first, second = self.two_deficits()
        permit = self.permit()
        before_balance = self.wallet.balance
        partial = self.post(amount=250, reference='GAP-1', client_request_id='gap-one-1')
        self.assertEqual(partial.data['code'], 0, partial.data)
        first.refresh_from_db(); second.refresh_from_db(); permit.refresh_from_db()
        self.assertEqual((first.remaining, second.remaining, permit.closed_at), (0, 250, None))
        self.assertEqual(list(RefundDepositCashRepayment.objects.order_by('pk').values_list('amount', flat=True)), [200, 50])
        completed = self.post(amount=250, reference='GAP-2', client_request_id='gap-two-2')
        self.assertEqual(completed.data['code'], 0, completed.data)
        second.refresh_from_db(); permit.refresh_from_db(); self.profile.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((second.remaining, self.profile.deposit_paid, self.wallet.balance), (0, 10000, before_balance))
        self.assertEqual((permit.close_reason, permit.cash_topup.client_request_id), ('CASH_GAP_PAID', 'gap-two-2'))
        self.assertEqual(self.post(amount=250, reference='GAP-2', client_request_id='gap-two-2').status_code, 409)
        self.assertEqual(RefundDepositCashRepayment.objects.count(), 3)
        with transaction.atomic():
            self.assertEqual(self.charge(200).pk, first.pk)
        with transaction.atomic():
            topup = first.cash_repayments.first().topup
            Transaction.objects.filter(pk=topup.platform_transaction_id).update(amount=249)
            with self.assertRaisesMessage(ValueError, '线下补差与退款押金缺口凭证不一致'):
                self.charge(200)
            transaction.set_rollback(True)

    def test_cash_then_income_reconciles_one_deficit_without_double_credit(self):
        first, second = self.two_deficits()
        self.assertEqual(self.post(amount=250, reference='MIXED-GAP', client_request_id='mixed-gap').data['code'], 0)
        from wallet.refund_deposit import apply_released_income
        with transaction.atomic():
            profile = type(self.profile).objects.select_for_update().get(pk=self.profile.pk)
            wallet = type(self.wallet).objects.select_for_update().get(pk=self.wallet.pk)
            before = wallet.balance
            wallet.balance += 100
            wallet.save(update_fields=['balance'])
            source = Transaction.objects.create(wallet=wallet, order=self.order, tx_type='INCOME', amount=100,
                balance_before=before, balance_after=wallet.balance, remark=f'订单收入：{self.order.order_no}')
            self.assertEqual(apply_released_income(source=source, profile=profile, wallet=wallet), 100)
            self.assertEqual(apply_released_income(source=source, profile=profile, wallet=wallet), 100)
        first.refresh_from_db(); second.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((first.remaining, second.remaining, self.profile.deposit_paid), (0, 150, 9850))
        self.assertEqual(self.post(amount=150, reference='MIXED-GAP-FINAL',
                                   client_request_id='mixed-gap-final').data['code'], 0)
        second.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((second.remaining, self.profile.deposit_paid), (0, 10000))

    def test_write_failure_rolls_back_cash_and_retry_can_succeed(self):
        first, _ = self.two_deficits()
        before = (get_platform_wallet().balance, CashDepositTopup.objects.count(), Transaction.objects.count())
        body = dict(amount=250, reference='FAIL-GAP', client_request_id='fail-gap')
        with patch('wallet.refund_deposit.RefundDepositCashRepayment.objects.create', side_effect=RuntimeError('模拟写入失败')):
            with self.assertRaisesMessage(RuntimeError, '模拟写入失败'):
                self.post(**body)
        first.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((first.remaining, self.profile.deposit_paid), (200, 9500))
        self.assertEqual((get_platform_wallet().balance, CashDepositTopup.objects.count(), Transaction.objects.count()), before)
        self.assertEqual(self.post(**body).data['code'], 0)

    def test_disabled_feature_rejects_cash_when_existing_gap_requires_reconciliation(self):
        first, _ = self.two_deficits()
        before = get_platform_wallet().balance
        with override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=False, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=False):
            result = self.post(amount=250, reference='DISABLED-GAP', client_request_id='disabled-gap')
        self.assertEqual(result.status_code, 409)
        first.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((first.remaining, self.profile.deposit_paid, get_platform_wallet().balance), (200, 9500, before))

    def test_missing_cash_repayment_schema_rejects_and_rolls_back(self):
        first, _ = self.two_deficits()
        before = (get_platform_wallet().balance, CashDepositTopup.objects.count(), Transaction.objects.count())
        tables = [name for name in connection.introspection.table_names()
                  if name != RefundDepositCashRepayment._meta.db_table]
        with patch.object(connection.introspection, 'table_names', return_value=tables):
            result = self.post(amount=250, reference='SCHEMA-GAP', client_request_id='schema-gap')
        self.assertEqual(result.status_code, 409)
        first.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((first.remaining, self.profile.deposit_paid), (200, 9500))
        self.assertEqual((get_platform_wallet().balance, CashDepositTopup.objects.count(), Transaction.objects.count()), before)


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class CashRefundDeficitStoresTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    charge = direct_fixtures.DirectRefundDepositTest.charge
    two_deficits = CashRefundDeficitTest.two_deficits

    def post(self, **kwargs):
        self.client.defaults['HTTP_HOST'] = 'admin.esports.club'
        return cash_fixtures.CashDepositTopupTest.post(self, **kwargs)

    def test_cash_repayment_stays_in_the_authenticated_store(self):
        stores = [resolve_store(), xinan_store()]
        for store in stores:
            with store_context(store):
                CashRefundDeficitTest.setUp(self)
                self.client.defaults['HTTP_HOST'] = 'admin.esports.club'
                self.two_deficits()
                if store.slug == stores[0].slug:
                    result = self.post(amount=250, reference='ISOLATED-GAP', client_request_id='isolated-gap')
                    self.assertEqual(result.data['code'], 0, result.data)
        from wallet.models import RefundDepositDeficit
        with store_context(stores[0]):
            self.assertEqual(list(RefundDepositDeficit.objects.order_by('pk').values_list('remaining', flat=True)), [0, 250])
            self.assertEqual(RefundDepositCashRepayment.objects.count(), 2)
        with store_context(stores[1]):
            self.assertEqual(list(RefundDepositDeficit.objects.order_by('pk').values_list('remaining', flat=True)), [200, 300])
            self.assertFalse(RefundDepositCashRepayment.objects.exists())


@skipUnless(connection.vendor == 'mysql' and os.environ.get('DJANGO_SETTINGS_MODULE') ==
            'config.ordinary_followup_mysql_test_settings', 'requires the disposable MySQL runner')
@override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class CashRefundDeficitMySQLRaceTest(TransactionTestCase):
    post = cash_fixtures.CashDepositTopupTest.post
    charge = direct_fixtures.DirectRefundDepositTest.charge
    setUp = CashRefundDeficitTest.setUp
    two_deficits = CashRefundDeficitTest.two_deficits

    def test_parallel_cash_receipts_cannot_overpay_the_gap(self):
        from club_accounts.tokens import issue_account_tokens
        from wallet.models import RefundDepositDeficit
        first, second = self.two_deficits()
        store = resolve_store()
        token = issue_account_tokens(self.staff_account)[0]
        gate = Barrier(2)

        def attempt(index):
            close_old_connections()
            try:
                with store_context(store):
                    client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
                    gate.wait(timeout=15)
                    result = client.post(self.url, {'amount': 400, 'reason': '并发线下补差',
                        'reference': f'RACE-{index}', 'client_request_id': f'cash-race-{index}'}, format='multipart')
                    return result.data['code']
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(attempt, (1, 2)))
        self.assertEqual(sorted(codes), [0, 400])
        first.refresh_from_db(); second.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((first.remaining, second.remaining, self.profile.deposit_paid), (0, 100, 9900))
        self.assertEqual(RefundDepositCashRepayment.objects.count(), 2)
        self.assertEqual(RefundDepositDeficit.objects.filter(remaining__gt=0).count(), 1)

    def test_cash_receipt_racing_another_refund_keeps_the_new_gap(self):
        from club_accounts.tokens import issue_account_tokens
        from django.db.models import Sum
        from wallet.models import RefundDepositDeficit
        from wallet.refund_deposit import charge_refund_deposit
        self.two_deficits()
        extra = OrderRefund.objects.create(order=self.order, amount=1000, reason='并发退款',
            operator=self.operator, operator_account=self.operator_account)
        store, token, gate = resolve_store(), issue_account_tokens(self.staff_account)[0], Barrier(2)

        def cash():
            close_old_connections()
            try:
                with store_context(store):
                    client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
                    gate.wait(timeout=15)
                    return client.post(self.url, {'amount': 500, 'reason': '线下补差', 'reference': 'RACE-REFUND',
                        'client_request_id': 'cash-race-refund'}, format='multipart').data['code']
            finally:
                close_old_connections()

        def refund():
            close_old_connections()
            try:
                with store_context(store):
                    gate.wait(timeout=15)
                    with transaction.atomic():
                        return charge_refund_deposit(refund=extra, profile=self.profile, amount=100,
                            operator=self.operator, operator_account=self.operator_account).pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            cash_result, refund_result = pool.submit(cash), pool.submit(refund)
            self.assertEqual(cash_result.result(timeout=30), 0)
            self.assertGreater(refund_result.result(timeout=30), 0)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.deposit_paid, 9900)
        self.assertEqual(RefundDepositDeficit.objects.aggregate(value=Sum('remaining'))['value'], 100)
        self.assertEqual(RefundDepositCashRepayment.objects.aggregate(value=Sum('amount'))['value'], 500)
