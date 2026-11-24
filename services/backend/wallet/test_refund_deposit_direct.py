"""Direct deposit liability used by a refund; all business data are isolated."""
import os
from unittest.mock import patch
from unittest import skipUnless

from django.conf import settings
from django.test import override_settings
from django.test import TestCase
from rest_framework.test import APITestCase
from stores.db import transaction

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import OrderRefund
from orders.tests.factories import make_completed_order, make_console_user, make_user
from wallet.models import RefundDepositDeficit, Transaction, get_platform_wallet
from wallet.refund_deposit import apply_released_income, charge_refund_deposit
from wallet import test_cash_deposit_topup


@override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False)
class DirectRefundDepositTest(APITestCase):
    post = test_cash_deposit_topup.CashDepositTopupTest.post

    def setUp(self):
        test_cash_deposit_topup.CashDepositTopupTest.setUp(self)
        self.profile.deposit_paid = 0
        self.profile.save(update_fields=['deposit_paid'])
        result = self.post(amount=10000)
        self.assertEqual(result.data['code'], 0, result.data)
        self.profile.refresh_from_db()
        self.operator = make_console_user(['order:refund'])
        self.operator_account = get_or_create_account_for_legacy_user(self.operator)
        self.operator.admin_membership.account = self.operator_account
        self.operator.admin_membership.save(update_fields=['account'])
        self.order = make_completed_order(make_user(), self.provider, amount=10000, payment_status='PAID')
        # The actual order refund coordinator is not wired yet; this receipt
        # isolates the debit primitive and does not represent an end-to-end refund.
        self.refund = OrderRefund.objects.create(order=self.order, amount=1000, reason='协商退款',
                                                  operator=self.operator, operator_account=self.operator_account)

    def charge(self, amount=200):
        return charge_refund_deposit(refund=self.refund, profile=self.profile, amount=amount,
                                     operator=self.operator, operator_account=self.operator_account)

    def test_real_cash_topup_debits_store_and_creates_idempotent_gap(self):
        before_wallet = self.wallet.balance
        before_store = get_platform_wallet().balance
        with transaction.atomic():
            deficit = self.charge()
            self.assertEqual(self.charge().pk, deficit.pk)
        self.profile.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((self.profile.deposit_paid, self.wallet.balance, get_platform_wallet().balance),
                         (9800, before_wallet, before_store - 200))
        self.assertEqual((deficit.source_kind, deficit.amount, deficit.remaining), ('DIRECT', 200, 200))
        self.assertIsNone(deficit.source_provider_transaction_id)
        self.assertEqual((deficit.source_store_transaction.amount, deficit.source_store_transaction.order_id),
                         (-200, self.order.pk))
        with transaction.atomic(), self.assertRaisesMessage(ValueError, '不一致'):
            self.charge(300)
        self.assertEqual(RefundDepositDeficit.objects.count(), 1)

    def test_income_repayment_replenishes_direct_gap_before_release(self):
        with transaction.atomic():
            deficit = self.charge()
        with transaction.atomic():
            wallet = type(self.wallet).objects.select_for_update().get(pk=self.wallet.pk)
            profile = type(self.profile).objects.select_for_update().get(pk=self.profile.pk)
            before = wallet.balance; wallet.balance += 300; wallet.save(update_fields=['balance'])
            income = Transaction.objects.create(wallet=wallet, order=self.order, tx_type='INCOME', amount=300,
                balance_before=before, balance_after=wallet.balance, remark=f'订单收入：{self.order.order_no}')
            self.assertEqual(apply_released_income(source=income, profile=profile, wallet=wallet), 200)
            self.assertEqual(apply_released_income(source=income, profile=profile, wallet=wallet), 200)
        self.profile.refresh_from_db(); self.wallet.refresh_from_db(); deficit.refresh_from_db()
        self.assertEqual((self.profile.deposit_paid, deficit.remaining, self.wallet.balance), (10000, 0, 1334))
        self.assertEqual(deficit.repayments.count(), 1)
        second = OrderRefund.objects.create(order=self.order, amount=1000, reason='后续退款',
                                             operator=self.operator, operator_account=self.operator_account)
        with transaction.atomic():
            another = charge_refund_deposit(refund=second, profile=self.profile, amount=100,
                                             operator=self.operator, operator_account=self.operator_account)
        self.profile.refresh_from_db()
        self.assertEqual((another.remaining, self.profile.deposit_paid), (100, 9900))

    def test_unverified_historical_deposit_and_store_shortage_are_rejected(self):
        self.profile.deposit_paid = 10001; self.profile.save(update_fields=['deposit_paid'])
        with transaction.atomic(), self.assertRaisesMessage(ValueError, '历史凭证不一致'):
            self.charge()
        self.profile.deposit_paid = 10000; self.profile.save(update_fields=['deposit_paid'])
        platform = get_platform_wallet(); platform.balance -= 1; platform.save(update_fields=['balance'])
        with transaction.atomic(), self.assertRaisesMessage(ValueError, '资金不足'):
            self.charge()
        self.assertFalse(RefundDepositDeficit.objects.exists())

    def test_failure_rolls_back_store_debit_and_profile(self):
        before = (self.profile.deposit_paid, get_platform_wallet().balance, Transaction.objects.count())
        with transaction.atomic():
            with self.assertRaisesMessage(RuntimeError, '写入失败'):
                with patch('wallet.refund_deposit.RefundDepositDeficit.objects.create', side_effect=RuntimeError('写入失败')):
                    self.charge()
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.deposit_paid, get_platform_wallet().balance, Transaction.objects.count()), before)
        self.assertFalse(RefundDepositDeficit.objects.exists())

    def test_outer_refund_failure_rolls_back_both_sides_and_deficit(self):
        from wallet.services import get_wallet
        buyer = get_wallet(user=self.order.customer)
        before = (buyer.balance, self.profile.deposit_paid, get_platform_wallet().balance,
                  Transaction.objects.count(), OrderRefund.objects.count())
        with self.assertRaisesMessage(RuntimeError, '退款后失败'):
            with transaction.atomic():
                refund = OrderRefund.objects.create(order=self.order, amount=200, reason='模拟外层事务',
                                                     operator=self.operator, operator_account=self.operator_account)
                charge_refund_deposit(refund=refund, profile=self.profile, amount=200,
                                      operator=self.operator, operator_account=self.operator_account)
                locked_buyer = type(buyer).objects.select_for_update().get(pk=buyer.pk)
                prior = locked_buyer.balance; locked_buyer.balance += 200; locked_buyer.save(update_fields=['balance'])
                Transaction.objects.create(wallet=locked_buyer, order=self.order, tx_type='REFUND', amount=200,
                                           balance_before=prior, balance_after=locked_buyer.balance)
                raise RuntimeError('退款后失败')
        buyer.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((buyer.balance, self.profile.deposit_paid, get_platform_wallet().balance,
                          Transaction.objects.count(), OrderRefund.objects.count()), before)
        self.assertFalse(RefundDepositDeficit.objects.exists())

    def test_changed_cash_evidence_is_rejected(self):
        topup = self.profile.cash_deposit_topups.get()
        topup.platform_transaction.amount = 9990
        topup.platform_transaction.save(update_fields=['amount'])
        with transaction.atomic(), self.assertRaisesMessage(ValueError, '现金押金凭证异常'):
            self.charge()
        self.assertFalse(RefundDepositDeficit.objects.exists())

    def test_tampered_refund_debit_cannot_be_repaid_by_income(self):
        with transaction.atomic():
            deficit = self.charge()
        tx = deficit.source_store_transaction
        tx.amount = -190; tx.save(update_fields=['amount'])
        before = self.wallet.balance
        with self.assertRaisesMessage(ValueError, '直接扣款凭证异常'):
            with transaction.atomic():
                wallet = type(self.wallet).objects.select_for_update().get(pk=self.wallet.pk)
                profile = type(self.profile).objects.select_for_update().get(pk=self.profile.pk)
                wallet.balance += 300; wallet.save(update_fields=['balance'])
                income = Transaction.objects.create(wallet=wallet, order=self.order, tx_type='INCOME', amount=300,
                    balance_before=before, balance_after=wallet.balance, remark=f'订单收入：{self.order.order_no}')
                apply_released_income(source=income, profile=profile, wallet=wallet)
        self.wallet.refresh_from_db(); deficit.refresh_from_db()
        self.assertEqual((self.wallet.balance, deficit.remaining), (before, 200))
        self.assertFalse(deficit.repayments.exists())

    def test_existing_wallet_deposit_is_included_in_source_reconciliation(self):
        with transaction.atomic():
            platform = get_platform_wallet(for_update=True)
            before = self.wallet.balance; self.wallet.balance -= 300; self.wallet.save(update_fields=['balance'])
            provider_tx = Transaction.objects.create(wallet=self.wallet, order=self.order, tx_type='DEPOSIT', amount=-300,
                                       balance_before=before, balance_after=self.wallet.balance,
                                       deposit_owner_id=self.profile.account_id,
                                       deposit_business_cycle=self.profile.deposit_business_cycle)
            before_store = platform.balance; platform.balance += 300; platform.save(update_fields=['balance'])
            Transaction.objects.create(wallet=platform, order=self.order, tx_type='DEPOSIT_INCOME', amount=300,
                                       balance_before=before_store, balance_after=platform.balance,
                                       deposit_owner_id=self.profile.account_id,
                                       deposit_business_cycle=self.profile.deposit_business_cycle,
                                       deposit_pair=provider_tx)
            self.profile.deposit_paid += 300; self.profile.save(update_fields=['deposit_paid'])
            deficit = self.charge()
        self.profile.refresh_from_db()
        self.assertEqual((deficit.amount, self.profile.deposit_paid), (200, 10100))

    def test_refund_operator_and_amount_must_match(self):
        other = make_console_user(['order:refund'])
        other_account = get_or_create_account_for_legacy_user(other)
        other.admin_membership.account = other_account; other.admin_membership.save(update_fields=['account'])
        with transaction.atomic(), self.assertRaisesMessage(ValueError, '经办人不一致'):
            charge_refund_deposit(refund=self.refund, profile=self.profile, amount=200,
                                  operator=other, operator_account=other_account)
        with transaction.atomic(), self.assertRaisesMessage(ValueError, '金额或经办人不一致'):
            self.charge(1010)
        self.assertFalse(RefundDepositDeficit.objects.exists())

    def test_unpaired_wallet_deposit_cannot_use_another_providers_pool(self):
        with transaction.atomic():
            before = self.wallet.balance; self.wallet.balance -= 300; self.wallet.save(update_fields=['balance'])
            Transaction.objects.create(wallet=self.wallet, order=self.order, tx_type='DEPOSIT', amount=-300,
                                       balance_before=before, balance_after=self.wallet.balance)
            self.profile.deposit_paid += 300; self.profile.save(update_fields=['deposit_paid'])
        with transaction.atomic(), self.assertRaisesMessage(ValueError, '配对凭证'):
            self.charge()
        self.assertFalse(RefundDepositDeficit.objects.exists())

    def test_ambiguous_historical_earnings_pairs_are_not_guessed(self):
        from orders.tests.factories import make_provider
        from wallet.services import get_wallet
        other = make_provider()
        other_account = get_or_create_account_for_legacy_user(other)
        other_wallet = get_wallet(user=other, account=other_account)
        with transaction.atomic():
            platform = get_platform_wallet(for_update=True)
            for wallet in (self.wallet, other_wallet):
                wallet.balance += 300; wallet.save(update_fields=['balance'])
                before = wallet.balance; wallet.balance -= 300; wallet.save(update_fields=['balance'])
                Transaction.objects.create(wallet=wallet, order=self.order, tx_type='DEPOSIT', amount=-300,
                                           balance_before=before, balance_after=wallet.balance)
                before = platform.balance; platform.balance += 300; platform.save(update_fields=['balance'])
                Transaction.objects.create(wallet=platform, order=self.order, tx_type='DEPOSIT_INCOME', amount=300,
                                           balance_before=before, balance_after=platform.balance)
            self.profile.deposit_paid += 300; self.profile.save(update_fields=['deposit_paid'])
        with transaction.atomic(), self.assertRaisesMessage(ValueError, '配对凭证'):
            self.charge()
        self.assertFalse(RefundDepositDeficit.objects.exists())

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '已配对退款冲正流水的押金来源核验分支属正式押金退款链深水区（18-盘点订单售后资金组合）；最终联调启用')
    def test_existing_paired_refund_can_be_reconciled_before_direct_charge(self):
        from orders.models import RefundRecoveryLine
        from wallet.refund_deposit import register_refund_deficit
        with transaction.atomic():
            platform = get_platform_wallet(for_update=True)
            before = self.wallet.balance; self.wallet.balance -= 300; self.wallet.save(update_fields=['balance'])
            debit = Transaction.objects.create(wallet=self.wallet, order=self.order, tx_type='DEPOSIT', amount=-300,
                                               balance_before=before, balance_after=self.wallet.balance)
            before = platform.balance; platform.balance += 300; platform.save(update_fields=['balance'])
            credit = Transaction.objects.create(wallet=platform, order=self.order, tx_type='DEPOSIT_INCOME', amount=300,
                                                balance_before=before, balance_after=platform.balance)
            self.profile.deposit_paid += 300; self.profile.save(update_fields=['deposit_paid'])
            prior = OrderRefund.objects.create(order=self.order, amount=100, reason='此前退款',
                                                operator=self.operator, operator_account=self.operator_account)
            before = self.wallet.balance; self.wallet.balance += 100; self.wallet.save(update_fields=['balance'])
            provider_back = Transaction.objects.create(wallet=self.wallet, order=self.order, tx_type='DEPOSIT', amount=100,
                balance_before=before, balance_after=self.wallet.balance, reversal_of=debit)
            before = platform.balance; platform.balance -= 100; platform.save(update_fields=['balance'])
            store_back = Transaction.objects.create(wallet=platform, order=self.order, tx_type='DEPOSIT_INCOME', amount=-100,
                balance_before=before, balance_after=platform.balance, reversal_of=credit)
            RefundRecoveryLine.objects.create(refund=prior, original_transaction=debit, reversal_transaction=provider_back,
                                               wallet=self.wallet, source='DEPOSIT', amount=100)
            RefundRecoveryLine.objects.create(refund=prior, original_transaction=credit, reversal_transaction=store_back,
                                               wallet=platform, source='DEPOSIT', amount=-100)
            self.profile.deposit_paid -= 100; self.profile.save(update_fields=['deposit_paid'])
            register_refund_deficit(refund=prior, profile=self.profile, provider_transaction=provider_back,
                                    store_transaction=store_back)
            deficit = self.charge()
        self.profile.refresh_from_db()
        self.assertEqual((deficit.amount, self.profile.deposit_paid), (200, 10000))


@skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
            '跨店押金退款来源配对凭证核验属正式押金退款链深水区（18-盘点订单售后资金组合）；最终联调启用')
@override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True)
@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class DirectRefundDepositStoreTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_same_ids_only_debit_the_active_store(self):
        from club_accounts.services import link_legacy_relations
        from orders.tests.factories import make_provider
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        from wallet.models import CashDepositTopup
        from wallet.services import get_wallet

        records = []
        for store in (resolve_store(), xinan_store()):
            with store_context(store):
                provider = make_provider(deposit_mode='CASH', deposit_paid=10000, deposit_required=10000)
                account = get_or_create_account_for_legacy_user(provider); link_legacy_relations(provider, account)
                profile = provider.escort_profile; profile.account = account; profile.save(update_fields=['account'])
                get_wallet(user=provider, account=account)
                operator = make_console_user(['order:refund'])
                staff = get_or_create_account_for_legacy_user(operator)
                operator.admin_membership.account = staff; operator.admin_membership.save(update_fields=['account'])
                platform = get_platform_wallet(); before = platform.balance
                platform.balance += 10000; platform.save(update_fields=['balance'])
                tx = Transaction.objects.create(wallet=platform, tx_type='DEPOSIT_INCOME', amount=10000,
                                                balance_before=before, balance_after=platform.balance)
                CashDepositTopup.objects.create(profile=profile, account=account, operator=staff, amount=10000,
                    paid_before=0, paid_after=10000, target_snapshot=10000, reason='隔离凭证',
                    reference='test', client_request_id='test-store-cash', platform_transaction=tx)
                order = make_completed_order(make_user(), provider, amount=10000, payment_status='PAID')
                refund = OrderRefund.objects.create(order=order, amount=1000, reason='隔离退款',
                                                     operator=operator, operator_account=staff)
                records.append((store, profile.pk, refund.pk, operator, staff, platform.balance))
        self.assertEqual(records[0][1:3], records[1][1:3])
        with store_context(records[0][0]):
            from users.models import EscortProfile
            profile = EscortProfile.objects.get(pk=records[0][1]); refund = OrderRefund.objects.get(pk=records[0][2])
            with transaction.atomic():
                charge_refund_deposit(refund=refund, profile=profile, amount=200,
                                      operator=records[0][3], operator_account=records[0][4])
            self.assertEqual(get_platform_wallet().balance, records[0][5] - 200)
            self.assertEqual(RefundDepositDeficit.objects.count(), 1)
        with store_context(records[1][0]):
            self.assertEqual(get_platform_wallet().balance, records[1][5])
            self.assertEqual(RefundDepositDeficit.objects.count(), 0)
            self.assertEqual(EscortProfile.objects.get(pk=records[1][1]).deposit_paid, 10000)
