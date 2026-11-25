"""IncomeHold release must require trusted store-DB clock evidence."""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from orders.tests.factories import make_provider, make_wallet
from stores.db import transaction
from wallet.income import hold_provider_income, release_due_income
from wallet.models import IncomeHold, SettlementClockEvidence, Transaction
from wallet.settlement_clocks import ClockUnavailable, trusted_test_clock


@override_settings(SETTLEMENT_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True)
class IncomeHoldClockEvidenceTest(TestCase):
    def setUp(self):
        self.provider = make_provider(deposit_mode='CASH')
        self.wallet = make_wallet(self.provider, balance=0, frozen_amount=100)
        self.income = Transaction.objects.create(
            wallet=self.wallet, order=None, tx_type='INCOME', amount=100,
            balance_before=0, balance_after=100, remark='历史待核收益')
        self.freeze = Transaction.objects.create(
            wallet=self.wallet, order=None, tx_type='EARNINGS_FREEZE', amount=-100,
            balance_before=100, balance_after=0, remark='历史冻结')
        self.hold = IncomeHold.objects.create(
            wallet=self.wallet, income_transaction=self.income, amount=100,
            release_at=timezone.now() - timedelta(hours=1), freeze_transaction=self.freeze)

    def assert_still_frozen(self):
        self.hold.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertIsNone(self.hold.released_at)
        self.assertIsNone(self.hold.release_transaction_id)
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (0, 100))
        self.assertFalse(Transaction.objects.filter(tx_type='EARNINGS_RELEASE').exists())

    def test_legacy_due_hold_without_freeze_receipt_stays_frozen(self):
        observed = trusted_test_clock(self.hold.release_at + timedelta(hours=2))
        self.assertEqual(release_due_income(now=observed), 0)
        self.assert_still_frozen()

    def test_raw_datetime_cannot_authorize_release(self):
        with self.assertRaises(ClockUnavailable):
            release_due_income(now=timezone.now())
        self.assert_still_frozen()


@override_settings(SETTLEMENT_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True)
class ProviderIncomeHoldClockTest(TestCase):
    def setUp(self):
        self.provider = make_provider(deposit_mode='CASH')
        self.wallet = make_wallet(self.provider, balance=100)
        self.income = Transaction.objects.create(
            wallet=self.wallet, order=None, tx_type='INCOME', amount=100,
            balance_before=0, balance_after=100, remark='隔离收益冻结时钟')
        self.frozen_at = timezone.now()
        self.clock = trusted_test_clock(self.frozen_at)
        with transaction.atomic():
            self.hold = hold_provider_income(
                self.income, profile=self.provider.escort_profile, hold_hours=72,
                deposit_percent=0, now=self.clock)
        self.deadline = self.frozen_at + timedelta(hours=72)
        self.wallet.refresh_from_db()

    def release(self, at):
        return release_due_income(now=trusted_test_clock(at))

    def assert_still_frozen(self):
        self.hold.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertIsNone(self.hold.released_at)
        self.assertIsNone(self.hold.release_transaction_id)
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (0, 100))
        self.assertFalse(Transaction.objects.filter(tx_type='EARNINGS_RELEASE').exists())

    def test_freeze_and_release_receipts_bound_deadline_and_exact_funds(self):
        self.assertEqual(self.hold.release_at, self.deadline)
        freeze_receipt = SettlementClockEvidence.objects.get(
            order=None, subject_type='HOLD', subject_id=self.hold.pk, operation='FREEZE')
        self.assertEqual(freeze_receipt.observed_at, self.frozen_at)
        self.assertEqual(freeze_receipt.deadline_at, self.deadline)
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (0, 100))

        self.assertEqual(self.release(self.deadline - timedelta(microseconds=1)), 0)
        self.assert_still_frozen()
        self.assertEqual(self.release(self.deadline), 1)
        self.hold.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (100, 0))
        self.assertEqual(self.hold.released_at, self.deadline)
        released = Transaction.objects.get(tx_type='EARNINGS_RELEASE')
        self.assertEqual((released.amount, released.balance_before, released.balance_after), (100, 0, 100))
        release_receipt = SettlementClockEvidence.objects.get(
            order=None, subject_type='HOLD', subject_id=self.hold.pk, operation='RELEASE')
        self.assertEqual(release_receipt.observed_at, self.deadline)
        self.assertEqual(release_receipt.deadline_at, self.deadline)
        self.assertEqual(self.release(self.deadline + timedelta(seconds=1)), 0)
        self.assertEqual(Transaction.objects.filter(tx_type='EARNINGS_RELEASE').count(), 1)

    def test_tampered_deadline_never_authorizes_early_release(self):
        IncomeHold.objects.filter(pk=self.hold.pk).update(release_at=self.deadline - timedelta(hours=1))
        self.hold.refresh_from_db()
        self.assertEqual(self.release(self.hold.release_at), 0)
        self.assert_still_frozen()

    def test_tampered_freeze_evidence_stays_frozen(self):
        receipt = SettlementClockEvidence.objects.get(
            order=None, subject_type='HOLD', subject_id=self.hold.pk, operation='FREEZE')
        SettlementClockEvidence.objects.filter(pk=receipt.pk).update(
            clock_evidence={**receipt.clock_evidence, 'database_identity': 'another-store-database'})
        self.assertEqual(self.release(self.deadline), 0)
        self.assert_still_frozen()

    def test_release_receipt_failure_rolls_back_money_and_retry_is_exact(self):
        from wallet.settlement_clocks import record as real_record

        def fail_release(*args, **kwargs):
            if args[3] == 'RELEASE':
                raise ClockUnavailable('injected release receipt failure')
            return real_record(*args, **kwargs)

        with patch('wallet.income.record', side_effect=fail_release):
            with self.assertRaises(ClockUnavailable):
                self.release(self.deadline)
        self.assert_still_frozen()
        self.assertEqual(self.release(self.deadline), 1)
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (100, 0))
        self.assertEqual(Transaction.objects.filter(tx_type='EARNINGS_RELEASE').count(), 1)

    def test_locked_clock_disconnect_keeps_frozen_then_real_retry_releases(self):
        with (patch('django.utils.timezone.now', return_value=self.deadline),
              patch('wallet.income.sample', side_effect=ClockUnavailable('store DB disconnected'))):
            self.assertEqual(release_due_income(), 0)
        self.assert_still_frozen()
        with patch('django.utils.timezone.now', return_value=self.deadline):
            self.assertEqual(release_due_income(), 1)
            self.assertEqual(release_due_income(), 0)
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (100, 0))
