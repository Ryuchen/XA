"""Trusted store-DB clocks for refund-deficit release and closure facts."""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase, APITransactionTestCase

from orders.tests.factories import make_completed_order
from stores.db import transaction
from wallet import test_refund_deposit_cash as cash_fixture
from wallet import test_refund_deposit_deficit as deficit_fixture
from wallet.income import release_due_income
from wallet.models import (
    IncomeHold, RefundDepositCashRepayment, RefundDepositIncomeApplication,
    RefundDepositRepayment, Transaction, get_platform_wallet,
)
from wallet.refund_deposit import apply_released_income
from wallet.settlement_clocks import ClockUnavailable, trusted_test_clock
from wallet import test_gift_income_clocks as gift_fixture


@override_settings(
    REFUND_DEPOSIT_DEFICIT_ENABLED=True,
    SETTLEMENT_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class RefundDepositIncomeClockTests(APITransactionTestCase):
    setUp = deficit_fixture.RefundDepositDeficitTest.setUp
    deficit = deficit_fixture.RefundDepositDeficitTest.deficit

    def income_at(self, amount, clock):
        with transaction.atomic():
            profile = type(self.profile).objects.select_for_update().get(pk=self.profile.pk)
            wallet = type(self.wallet).objects.select_for_update().get(pk=self.wallet.pk)
            before = wallet.balance
            wallet.balance += amount
            wallet.save(update_fields=['balance'])
            source = Transaction.objects.create(
                wallet=wallet, order=self.order, tx_type='INCOME', amount=amount,
                balance_before=before, balance_after=wallet.balance,
                remark=f'订单收入：{self.order.order_no}',
            )
            applied = apply_released_income(
                source=source, profile=profile, wallet=wallet, now=clock,
            )
        return source, applied

    def test_income_deficit_close_disconnect_rolls_back_every_money_fact(self):
        deficit = self.deficit(200)
        self.wallet.refresh_from_db()
        self.profile.refresh_from_db()
        self.platform.refresh_from_db()
        before = (
            self.wallet.balance, self.profile.deposit_paid, self.platform.balance,
            Transaction.objects.count(),
        )
        with patch('wallet.refund_deposit.require_clock',
                   side_effect=ClockUnavailable('store DB disconnected'), create=True):
            with self.assertRaises(ClockUnavailable):
                self.income_at(1000, None)
        deficit.refresh_from_db()
        self.wallet.refresh_from_db()
        self.profile.refresh_from_db()
        self.platform.refresh_from_db()
        self.assertEqual((deficit.remaining, deficit.closed_at), (200, None))
        self.assertEqual(
            (self.wallet.balance, self.profile.deposit_paid, self.platform.balance,
             Transaction.objects.count()), before,
        )
        self.assertFalse(RefundDepositIncomeApplication.objects.exists())
        self.assertFalse(RefundDepositRepayment.objects.exists())

    def test_income_close_uses_attested_time_and_historical_retry_does_not_repost(self):
        deficit = self.deficit(200)
        observed = trusted_test_clock(timezone.now() + timedelta(minutes=3))
        source, applied = self.income_at(1000, observed)
        self.assertEqual(applied, 200)
        deficit.refresh_from_db()
        self.wallet.refresh_from_db()
        self.profile.refresh_from_db()
        self.platform.refresh_from_db()
        self.assertEqual((deficit.remaining, deficit.closed_at), (0, observed.at))
        self.assertEqual((self.wallet.balance, self.profile.deposit_paid), (800, 10000))
        application = RefundDepositIncomeApplication.objects.get(source_transaction=source)
        repayment = application.repayments.get()
        self.assertEqual(application.applied_amount, 200)
        self.assertEqual(
            (repayment.provider_transaction.wallet_id, repayment.store_transaction.wallet_id,
             repayment.provider_transaction.deposit_pair_id if hasattr(repayment.provider_transaction, 'deposit_pair_id') else None),
            (self.wallet.pk, self.platform.pk, None),
        )
        self.assertEqual(repayment.store_transaction.deposit_pair_id, repayment.provider_transaction.pk)
        balances = (self.wallet.balance, self.platform.balance, Transaction.objects.count())
        with transaction.atomic():
            profile = type(self.profile).objects.select_for_update().get(pk=self.profile.pk)
            wallet = type(self.wallet).objects.select_for_update().get(pk=self.wallet.pk)
            with patch('wallet.settlement_clocks.sample',
                       side_effect=ClockUnavailable('retry must read original result')):
                self.assertEqual(apply_released_income(
                    source=source, profile=profile, wallet=wallet,
                ), 200)
        self.assertEqual(RefundDepositIncomeApplication.objects.count(), 1)
        self.assertEqual(RefundDepositRepayment.objects.count(), 1)
        self.assertEqual(
            (self.wallet.balance, self.platform.balance, Transaction.objects.count()), balances,
        )

    def test_earnings_release_rejects_raw_datetime_and_enforces_exact_deadline(self):
        deficit = self.deficit(200)
        deadline = timezone.now() + timedelta(hours=1)
        with transaction.atomic():
            profile = type(self.profile).objects.select_for_update().get(pk=self.profile.pk)
            wallet = type(self.wallet).objects.select_for_update().get(pk=self.wallet.pk)
            original = Transaction.objects.create(
                wallet=wallet, order=self.order, tx_type='INCOME', amount=1000,
                balance_before=0, balance_after=1000,
                remark=f'订单收入：{self.order.order_no}',
            )
            hold = IncomeHold.objects.create(
                wallet=wallet, income_transaction=original, amount=1000, release_at=deadline,
            )
            before = wallet.balance
            wallet.balance += 1000
            wallet.save(update_fields=['balance'])
            source = Transaction.objects.create(
                wallet=wallet, order=self.order, tx_type='EARNINGS_RELEASE', amount=1000,
                balance_before=before, balance_after=wallet.balance,
            )
        with transaction.atomic():
            profile = type(self.profile).objects.select_for_update().get(pk=self.profile.pk)
            wallet = type(self.wallet).objects.select_for_update().get(pk=self.wallet.pk)
            with self.assertRaisesMessage(ValueError, '释放时间不一致'):
                apply_released_income(
                    source=source, profile=profile, wallet=wallet, hold=hold,
                    now=trusted_test_clock(deadline - timedelta(microseconds=1)),
                )
        with patch('wallet.refund_deposit.require_clock',
                   side_effect=ClockUnavailable('raw datetime is not evidence'), create=True):
            with transaction.atomic():
                profile = type(self.profile).objects.select_for_update().get(pk=self.profile.pk)
                wallet = type(self.wallet).objects.select_for_update().get(pk=self.wallet.pk)
                with self.assertRaises(ClockUnavailable):
                    apply_released_income(
                        source=source, profile=profile, wallet=wallet, hold=hold, now=deadline,
                    )
        deficit.refresh_from_db()
        self.assertEqual((deficit.remaining, deficit.closed_at), (200, None))
        observed = trusted_test_clock(deadline)
        with transaction.atomic():
            profile = type(self.profile).objects.select_for_update().get(pk=self.profile.pk)
            wallet = type(self.wallet).objects.select_for_update().get(pk=self.wallet.pk)
            self.assertEqual(apply_released_income(
                source=source, profile=profile, wallet=wallet, hold=hold, now=observed,
            ), 200)
        deficit.refresh_from_db()
        self.assertEqual((deficit.remaining, deficit.closed_at), (0, deadline))


@override_settings(
    REFUND_DEPOSIT_DEFICIT_ENABLED=True,
    SETTLEMENT_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True,
    LEGACY_FORCE_AUTH_COMPAT=False,
    LOGIN_OTP_REQUIRED=False,
    KOOK_ENABLED=False,
    WECOM_DISPATCH_ENABLED=False,
)
class LockedIncomeReleaseClockTests(APITestCase):
    send = gift_fixture.GiftIncomeClocksTest.send
    configure = gift_fixture.GiftIncomeClocksTest.configure
    deficit = deficit_fixture.RefundDepositDeficitTest.deficit

    def setUp(self):
        gift_fixture.GiftIncomeClocksTest.setUp(self)
        self.account = self.accounts[1]
        self.profile = self.provider.escort_profile
        self.profile.deposit_mode = 'EARNINGS'
        self.profile.deposit_paid = 10000
        self.profile.save(update_fields=['deposit_mode', 'deposit_paid'])
        self.wallet = self.provider_wallet
        self.platform = get_platform_wallet()
        self.platform.balance = 10000
        self.platform.save(update_fields=['balance'])
        self.order = make_completed_order(
            self.boss, self.provider, amount=10000,
            provider_account=self.account, payment_status='PAID',
        )

    def test_locked_release_disconnect_rolls_back_and_real_retry_reconciles_exactly_once(self):
        deficit = self.deficit(200)
        self.configure(1, 2)
        gift = self.send('gift-deficit-release-clock-disconnect')
        hold = IncomeHold.objects.get(income_transaction__order=gift)
        deadline = gift.completed_at + timedelta(hours=2)
        self.wallet.refresh_from_db()
        self.profile.refresh_from_db()
        self.platform.refresh_from_db()
        before = (self.platform.balance, Transaction.objects.count())
        with patch('wallet.refund_deposit.require_clock',
                   side_effect=ClockUnavailable('clock lost after lock wait'), create=True):
            with self.assertRaises(ClockUnavailable):
                release_due_income(now=trusted_test_clock(deadline))
        hold.refresh_from_db()
        self.wallet.refresh_from_db()
        self.profile.refresh_from_db()
        deficit.refresh_from_db()
        self.platform.refresh_from_db()
        self.assertIsNone(hold.released_at)
        self.assertIsNone(hold.release_transaction_id)
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (0, 1600))
        self.assertEqual((deficit.remaining, deficit.closed_at), (200, None))
        self.assertEqual(self.profile.deposit_paid, 9800)
        self.assertEqual((self.platform.balance, Transaction.objects.count()), before)
        self.assertFalse(RefundDepositIncomeApplication.objects.exists())

        self.assertEqual(release_due_income(now=trusted_test_clock(deadline)), 1)
        hold.refresh_from_db()
        self.wallet.refresh_from_db()
        self.profile.refresh_from_db()
        deficit.refresh_from_db()
        self.assertEqual((hold.released_at, deficit.closed_at), (deadline, deadline))
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (1400, 0))
        self.assertEqual((self.profile.deposit_paid, deficit.remaining), (10000, 0))
        release = Transaction.objects.get(tx_type='EARNINGS_RELEASE', order=gift)
        application = RefundDepositIncomeApplication.objects.get(source_transaction=release)
        self.assertEqual((application.applied_amount, application.account_id), (200, self.account.pk))
        self.assertEqual(release.order_id, gift.pk)
        self.assertEqual(release.balance_after, release.balance_before + release.amount)
        self.assertEqual(release.wallet_id, self.wallet.pk)
        self.assertEqual(release.amount, hold.amount - hold.recovered_amount)
        self.assertEqual(release.amount, 1600)
        self.assertEqual(RefundDepositRepayment.objects.get(application=application).amount, 200)
        counts = (Transaction.objects.count(), RefundDepositRepayment.objects.count())
        self.assertEqual(release_due_income(now=trusted_test_clock(deadline + timedelta(seconds=1))), 0)
        self.assertEqual((Transaction.objects.count(), RefundDepositRepayment.objects.count()), counts)


@override_settings(
    REFUND_DEPOSIT_DEFICIT_ENABLED=True,
    REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True,
    SETTLEMENT_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True,
    LEGACY_FORCE_AUTH_COMPAT=False,
    ALLOWED_HOSTS=['*'],
)
class CashAndPermitClockTests(TestCase):
    post = cash_fixture.cash_fixtures.CashDepositTopupTest.post
    charge = cash_fixture.direct_fixtures.DirectRefundDepositTest.charge
    two_deficits = cash_fixture.CashRefundDeficitTest.two_deficits
    permit = cash_fixture.CashRefundDeficitTest.permit

    def setUp(self):
        cash_fixture.CashRefundDeficitTest.setUp(self)
        self.platform = get_platform_wallet()

    def test_cash_close_disconnect_rolls_back_and_retry_uses_one_attested_timestamp(self):
        first, second = self.two_deficits()
        permit = self.permit()
        self.wallet.refresh_from_db()
        self.profile.refresh_from_db()
        self.platform.refresh_from_db()
        before = (
            self.profile.deposit_paid, self.platform.balance,
            Transaction.objects.count(), RefundDepositCashRepayment.objects.count(),
        )
        body = dict(amount=500, reference='CLOCK-CASH-GAP', client_request_id='clock-cash-gap')
        with patch('wallet.refund_deposit.require_clock',
                   side_effect=ClockUnavailable('cash close clock disconnected'), create=True):
            with self.assertRaises(ClockUnavailable):
                self.post(**body)
        first.refresh_from_db(); second.refresh_from_db(); permit.refresh_from_db()
        self.profile.refresh_from_db(); self.platform.refresh_from_db()
        self.assertEqual((first.remaining, first.closed_at, second.remaining, second.closed_at),
                         (200, None, 300, None))
        self.assertIsNone(permit.closed_at)
        self.assertEqual(
            (self.profile.deposit_paid, self.platform.balance,
             Transaction.objects.count(), RefundDepositCashRepayment.objects.count()), before,
        )
        observed = trusted_test_clock(timezone.now() + timedelta(minutes=5))
        with patch('wallet.settlement_clocks.trusted_test_clock', return_value=observed):
            result = self.post(**body)
        self.assertEqual(result.data['code'], 0, result.data)
        first.refresh_from_db(); second.refresh_from_db(); permit.refresh_from_db()
        self.assertEqual((first.closed_at, second.closed_at, permit.closed_at),
                         (observed.at, observed.at, observed.at))
        self.assertEqual((first.remaining, second.remaining, permit.close_reason), (0, 0, 'CASH_GAP_PAID'))
        self.assertEqual(RefundDepositCashRepayment.objects.count(), 2)
        count = RefundDepositCashRepayment.objects.count()
        self.assertEqual(self.post(**body).status_code, 409)
        self.assertEqual(RefundDepositCashRepayment.objects.count(), count)

    def apply_permit_income(self, clock):
        with transaction.atomic():
            profile = type(self.profile).objects.select_for_update().get(pk=self.profile.pk)
            wallet = type(self.wallet).objects.select_for_update().get(pk=self.wallet.pk)
            before = wallet.balance
            wallet.balance += 100
            wallet.save(update_fields=['balance'])
            source = Transaction.objects.create(
                wallet=wallet, order=self.recovery, tx_type='INCOME', amount=100,
                balance_before=before, balance_after=wallet.balance,
                remark=f'订单收入：{self.recovery.order_no}',
            )
            applied = apply_released_income(
                source=source, profile=profile, wallet=wallet, now=clock,
            )
        return source, applied

    def test_income_settled_permit_requires_clock_and_closed_retry_never_recovers_again(self):
        deficit = self.charge(200)
        permit = self.permit()
        self.recovery = permit.order
        self.recovery.status = 'COMPLETED'
        self.recovery.save(update_fields=['status'])
        self.wallet.refresh_from_db()
        self.profile.refresh_from_db()
        self.platform.refresh_from_db()
        before = (self.wallet.balance, Transaction.objects.count())
        with patch('wallet.settlement_clocks.sample',
                   side_effect=ClockUnavailable('permit close clock disconnected')):
            with self.assertRaises(ClockUnavailable):
                self.apply_permit_income(None)
        deficit.refresh_from_db(); permit.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((deficit.remaining, permit.closed_at), (200, None))
        self.assertEqual((self.wallet.balance, Transaction.objects.count()), before)

        observed = trusted_test_clock(timezone.now() + timedelta(minutes=7))
        source, applied = self.apply_permit_income(observed)
        self.assertEqual(applied, 100)
        deficit.refresh_from_db(); permit.refresh_from_db()
        self.assertEqual((deficit.remaining, deficit.closed_at), (100, None))
        self.assertEqual((permit.close_reason, permit.closed_at), ('INCOME_SETTLED', observed.at))
        self.assertEqual(permit.income_application.source_transaction_id, source.pk)
        self.assertEqual(permit.income_application.applied_amount, 100)
        counts = (Transaction.objects.count(), RefundDepositRepayment.objects.count())
        with transaction.atomic():
            profile = type(self.profile).objects.select_for_update().get(pk=self.profile.pk)
            wallet = type(self.wallet).objects.select_for_update().get(pk=self.wallet.pk)
            with patch('wallet.settlement_clocks.sample',
                       side_effect=ClockUnavailable('closed permit retry needs no new clock')):
                self.assertEqual(apply_released_income(
                    source=source, profile=profile, wallet=wallet,
                ), 100)
        permit.refresh_from_db()
        self.assertEqual((permit.close_reason, permit.closed_at), ('INCOME_SETTLED', observed.at))
        self.assertEqual((Transaction.objects.count(), RefundDepositRepayment.objects.count()), counts)
