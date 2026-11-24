from unittest.mock import patch

from django.test import override_settings
from rest_framework.test import APITransactionTestCase
from stores.db import transaction

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import OrderRefund, RefundRecoveryLine
from orders.tests.factories import make_completed_order, make_provider, make_user
from wallet.income import hold_provider_income
from wallet.models import (RefundDepositDeficit, RefundDepositIncomeApplication, RefundDepositRepayment,
                           Transaction, get_platform_wallet)
from wallet.refund_deposit import apply_released_income, register_refund_deficit
from wallet.services import get_wallet


@override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class RefundDepositDeficitTest(APITransactionTestCase):
    def setUp(self):
        self.boss = make_user()
        self.provider = make_provider(deposit_mode='EARNINGS', deposit_paid=10000)
        self.account = get_or_create_account_for_legacy_user(self.provider)
        self.profile = self.provider.escort_profile
        self.profile.account = self.account; self.profile.save(update_fields=['account'])
        self.wallet = get_wallet(account=self.account, user=self.provider)
        self.platform = get_platform_wallet()
        self.platform.balance = 10000; self.platform.save(update_fields=['balance'])
        self.order = make_completed_order(self.boss, self.provider, amount=10000,
            provider_account=self.account, payment_status='PAID')

    def deficit(self, amount):
        """Isolated synthetic refund with matching real recovery ledger rows."""
        with transaction.atomic():
            profile = type(self.profile).objects.select_for_update().get(pk=self.profile.pk)
            wallet = type(self.wallet).objects.select_for_update().get(pk=self.wallet.pk)
            platform = get_platform_wallet(for_update=True)
            cycle = profile.account.business_cycle
            refund = OrderRefund.objects.create(order=self.order, amount=amount, reason='隔离测试退款')
            original_provider = Transaction.objects.create(wallet=wallet, order=self.order, tx_type='DEPOSIT',
                amount=-amount, balance_before=amount, balance_after=0,
                deposit_owner=profile.account, deposit_business_cycle=cycle)
            original_store = Transaction.objects.create(wallet=platform, order=self.order, tx_type='DEPOSIT_INCOME',
                amount=amount, balance_before=platform.balance-amount, balance_after=platform.balance,
                deposit_owner=profile.account, deposit_business_cycle=cycle, deposit_pair=original_provider)
            before = wallet.balance; wallet.balance += amount; wallet.save(update_fields=['balance'])
            provider = Transaction.objects.create(wallet=wallet, order=self.order, tx_type='DEPOSIT', amount=amount,
                balance_before=before, balance_after=wallet.balance, reversal_of=original_provider,
                deposit_owner=profile.account, deposit_business_cycle=cycle)
            before = platform.balance; platform.balance -= amount; platform.save(update_fields=['balance'])
            store = Transaction.objects.create(wallet=platform, order=self.order, tx_type='DEPOSIT_INCOME', amount=-amount,
                balance_before=before, balance_after=platform.balance, reversal_of=original_store,
                deposit_owner=profile.account, deposit_business_cycle=cycle, deposit_pair=provider)
            RefundRecoveryLine.objects.create(refund=refund, original_transaction=original_provider,
                reversal_transaction=provider, wallet=wallet, source='DEPOSIT', amount=amount)
            RefundRecoveryLine.objects.create(refund=refund, original_transaction=original_store,
                reversal_transaction=store, wallet=platform, source='DEPOSIT', amount=-amount)
            profile.deposit_paid -= amount
            if not profile.deposit_business_cycle:
                profile.deposit_business_cycle = cycle
            profile.save(update_fields=['deposit_paid', 'deposit_business_cycle'])
            deficit = register_refund_deficit(refund=refund, profile=profile,
                                              provider_transaction=provider, store_transaction=store)
            self.assertEqual(register_refund_deficit(refund=refund, profile=profile,
                provider_transaction=provider, store_transaction=store).pk, deficit.pk)
            before = wallet.balance; wallet.balance -= amount; wallet.save(update_fields=['balance'])
            Transaction.objects.create(wallet=wallet, order=self.order, tx_type='REFUND', amount=-amount,
                balance_before=before, balance_after=wallet.balance, remark='隔离退款扣款')
        return deficit

    def income(self, amount, *, regular=False):
        with transaction.atomic():
            profile = type(self.profile).objects.select_for_update().get(pk=self.profile.pk)
            wallet = type(self.wallet).objects.select_for_update().get(pk=self.wallet.pk)
            before = wallet.balance; wallet.balance += amount; wallet.save(update_fields=['balance'])
            source = Transaction.objects.create(wallet=wallet, order=self.order, tx_type='INCOME', amount=amount,
                balance_before=before, balance_after=wallet.balance, remark=f'订单收入：{self.order.order_no}')
            if regular:
                hold_provider_income(source, profile=profile, hold_hours=0, deposit_percent=60, apply_without_hold=True)
            else:
                apply_released_income(source=source, profile=profile, wallet=wallet)
        return source

    def test_shortfall_is_repaid_before_ordinary_earnings_deposit(self):
        deficit = self.deficit(200)
        source = self.income(1000, regular=True)
        deficit.refresh_from_db(); self.profile.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((deficit.remaining, self.profile.deposit_paid, self.wallet.balance), (0, 10480, 320))
        application = RefundDepositIncomeApplication.objects.get(source_transaction=source)
        self.assertEqual(application.applied_amount, 200)
        self.assertEqual(list(application.repayments.values_list('amount', flat=True)), [200])
        self.assertEqual(list(Transaction.objects.filter(order=self.order, tx_type='DEPOSIT', amount__lt=0)
                              .values_list('amount', flat=True).order_by('id')), [-200, -200, -480])
        with transaction.atomic():
            profile = type(self.profile).objects.select_for_update().get(pk=self.profile.pk)
            wallet = type(self.wallet).objects.select_for_update().get(pk=self.wallet.pk)
            self.assertEqual(apply_released_income(source=source, profile=profile, wallet=wallet), 200)
        self.assertEqual(application.repayments.count(), 1)

    def test_oldest_deficit_first_and_partial_repayment(self):
        first, second = self.deficit(200), self.deficit(300)
        self.income(250)
        first.refresh_from_db(); second.refresh_from_db()
        self.assertEqual((first.remaining, second.remaining), (0, 250))
        self.assertIsNotNone(first.closed_at); self.assertIsNone(second.closed_at)
        self.assertEqual(list(RefundDepositRepayment.objects.order_by('pk').values_list('amount', flat=True)), [200, 50])

    def test_old_income_with_no_deficit_cannot_be_used_after_new_refund(self):
        source = self.income(100)
        self.assertEqual(RefundDepositIncomeApplication.objects.get(source_transaction=source).applied_amount, 0)
        deficit = self.deficit(50)
        with transaction.atomic():
            profile = type(self.profile).objects.select_for_update().get(pk=self.profile.pk)
            wallet = type(self.wallet).objects.select_for_update().get(pk=self.wallet.pk)
            self.assertEqual(apply_released_income(source=source, profile=profile, wallet=wallet), 0)
        deficit.refresh_from_db(); self.assertEqual(deficit.remaining, 50)

    def test_repayment_failure_rolls_back_income_and_deposit(self):
        deficit = self.deficit(200)
        before = (type(self.wallet).objects.get(pk=self.wallet.pk).balance,
                  type(self.profile).objects.get(pk=self.profile.pk).deposit_paid, Transaction.objects.count())
        with patch('wallet.refund_deposit.RefundDepositRepayment.objects.create', side_effect=RuntimeError('写入失败')):
            with self.assertRaisesMessage(RuntimeError, '写入失败'):
                self.income(1000)
        deficit.refresh_from_db()
        self.assertEqual(deficit.remaining, 200)
        self.assertEqual((type(self.wallet).objects.get(pk=self.wallet.pk).balance,
                          type(self.profile).objects.get(pk=self.profile.pk).deposit_paid, Transaction.objects.count()), before)

    def test_unpaired_refund_evidence_is_rejected(self):
        refund = OrderRefund.objects.create(order=self.order, amount=100, reason='隔离测试退款')
        provider = Transaction.objects.create(wallet=self.wallet, order=self.order, tx_type='DEPOSIT',
            amount=100, balance_before=0, balance_after=100)
        store = Transaction.objects.create(wallet=self.platform, order=self.order, tx_type='DEPOSIT_INCOME',
            amount=-100, balance_before=100, balance_after=0)
        with transaction.atomic(), self.assertRaisesMessage(ValueError, '退款押金两侧流水不匹配'):
            register_refund_deficit(refund=refund, profile=self.profile,
                                    provider_transaction=provider, store_transaction=store)
        self.assertFalse(RefundDepositDeficit.objects.exists())

    def test_admission_remains_closed_until_single_order_allowance_is_implemented(self):
        self.deficit(200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.order_acceptance_error(), '退款押金缺口待补，补差接单功能尚未开放')
