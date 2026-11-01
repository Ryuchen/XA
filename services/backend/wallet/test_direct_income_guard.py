"""Direct-channel holds remain frozen until trusted settlement is implemented."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from unittest.mock import patch

from django.db import close_old_connections, transaction
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.direct_payment_evidence_compare import flag_payment_evidence_risk
from orders.direct_payment_models import DirectPaymentIntent
from orders.models import Order
from orders.tests.factories import make_order, make_provider, make_user, make_wallet
from wallet.income import _direct_income_blocked, release_due_income
from wallet.models import IncomeHold, Transaction


class DirectHoldFixture:
    def setUp(self):
        boss = make_user()
        account = get_or_create_account_for_legacy_user(boss)
        provider = make_provider()
        self.wallet = make_wallet(provider, balance=0, frozen_amount=3000)
        self.direct_order = make_order(boss, customer_account=account)
        self.intent = DirectPaymentIntent.objects.create(
            order=self.direct_order, account=account, client_request_id='test-direct-hold',
            request_fingerprint='x', merchant_order_no='test-direct-hold',
            appid='wx-test', mchid='mch-test', openid='openid-test', amount_fen=10000,
            currency='CNY', payer_total_fen=10000, payer_currency='CNY',
            status='PAID', transaction_id='test-direct-tx', paid_at=timezone.now(),
        )
        self.now = timezone.now()
        self.direct_hold = self.make_hold(self.direct_order)
        self.balance_hold = self.make_hold(make_order(boss, customer_account=account))
        self.historical_hold = self.make_hold(None)

    def make_hold(self, order):
        income = Transaction.objects.create(wallet=self.wallet, order=order, amount=1000, tx_type='INCOME')
        return IncomeHold.objects.create(wallet=self.wallet, income_transaction=income,
            amount=1000, release_at=self.now - timedelta(seconds=1))


class DirectIncomeGuardTest(DirectHoldFixture, TestCase):
    def test_paid_direct_hold_cannot_starve_balance_or_orderless_holds(self):
        self.assertEqual(release_due_income(now=self.now, limit=1), 1)
        self.assertEqual(release_due_income(now=self.now, limit=1), 1)
        self.assertEqual(release_due_income(now=self.now, limit=1), 0)
        self.wallet.refresh_from_db()
        self.direct_hold.refresh_from_db()
        self.balance_hold.refresh_from_db()
        self.historical_hold.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (2000, 1000))
        self.assertIsNone(self.direct_hold.released_at)
        self.assertIsNotNone(self.balance_hold.released_at)
        self.assertIsNotNone(self.historical_hold.released_at)
        self.assertEqual(Transaction.objects.filter(tx_type='EARNINGS_RELEASE').count(), 2)

    def test_stale_candidate_is_rechecked_for_paid_and_review_intents(self):
        candidate = [(self.direct_hold.pk, self.wallet.pk, self.direct_order.pk)]
        with patch('wallet.income._due_income_candidates', return_value=candidate):
            self.assertEqual(release_due_income(now=self.now), 0)
            self.intent.status = 'REVIEW'
            self.intent.evidence_risk_at = timezone.now()
            self.intent.save(update_fields=['status', 'evidence_risk_at'])
            self.assertEqual(release_due_income(now=self.now), 0)
        self.wallet.refresh_from_db()
        self.direct_hold.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (0, 3000))
        self.assertIsNone(self.direct_hold.released_at)
        self.assertFalse(Transaction.objects.filter(tx_type='EARNINGS_RELEASE').exists())


@skipUnlessDBFeature('has_select_for_update')
class DirectIncomeGuardRaceTest(DirectHoldFixture, TransactionTestCase):
    def test_risk_commits_before_scanner_acquires_order_lock(self):
        risk_locked, commit_risk, candidate_seen, scan_finished = Event(), Event(), Event(), Event()
        candidate = [(self.direct_hold.pk, self.wallet.pk, self.direct_order.pk)]

        def mark_risk():
            close_old_connections()
            try:
                with transaction.atomic():
                    Order.objects.select_for_update().get(pk=self.direct_order.pk)
                    intent = DirectPaymentIntent.objects.select_for_update().get(pk=self.intent.pk)
                    flag_payment_evidence_risk(intent)
                    risk_locked.set()
                    if not commit_risk.wait(15):
                        raise AssertionError('Risk transaction was not released')
            finally:
                close_old_connections()

        def scan():
            close_old_connections()
            try:
                return release_due_income(now=self.now)
            finally:
                scan_finished.set()
                close_old_connections()

        def stale_candidate(*args):
            candidate_seen.set()
            return candidate

        with patch('wallet.income._due_income_candidates', side_effect=stale_candidate), ThreadPoolExecutor(max_workers=2) as pool:
            risk = pool.submit(mark_risk)
            try:
                self.assertTrue(risk_locked.wait(10), 'Risk path did not lock the order')
                scanner = pool.submit(scan)
                self.assertTrue(candidate_seen.wait(10), 'Scanner did not receive the stale candidate')
                self.assertFalse(scan_finished.wait(1), 'Scanner did not wait for the order lock')
            finally:
                commit_risk.set()
            risk.result(timeout=15)
            self.assertEqual(scanner.result(timeout=15), 0)

        self.intent.refresh_from_db()
        self.wallet.refresh_from_db()
        self.direct_hold.refresh_from_db()
        self.assertEqual(self.intent.status, 'REVIEW')
        self.assertIsNotNone(self.intent.evidence_risk_at)
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (0, 3000))
        self.assertIsNone(self.direct_hold.released_at)
        self.assertFalse(Transaction.objects.filter(tx_type='EARNINGS_RELEASE').exists())

    def test_scanner_locks_order_before_concurrent_risk_update(self):
        scanner_locked, release_scanner, risk_attempted, risk_finished = Event(), Event(), Event(), Event()
        candidate = [(self.direct_hold.pk, self.wallet.pk, self.direct_order.pk)]

        def hold_scanner(order_id):
            scanner_locked.set()
            if not release_scanner.wait(15):
                raise AssertionError('Scanner order lock was not released')
            return _direct_income_blocked(order_id)

        def scan():
            close_old_connections()
            try:
                return release_due_income(now=self.now)
            finally:
                close_old_connections()

        def mark_risk():
            close_old_connections()
            try:
                with transaction.atomic():
                    risk_attempted.set()
                    Order.objects.select_for_update().get(pk=self.direct_order.pk)
                    intent = DirectPaymentIntent.objects.select_for_update().get(pk=self.intent.pk)
                    flag_payment_evidence_risk(intent)
            finally:
                risk_finished.set()
                close_old_connections()

        with patch('wallet.income._due_income_candidates', return_value=candidate), \
             patch('wallet.income._direct_income_blocked', side_effect=hold_scanner), \
             ThreadPoolExecutor(max_workers=2) as pool:
            scanner = pool.submit(scan)
            try:
                self.assertTrue(scanner_locked.wait(10), 'Scanner did not lock the order')
                risk = pool.submit(mark_risk)
                self.assertTrue(risk_attempted.wait(10), 'Risk path did not attempt the order lock')
                self.assertFalse(risk_finished.wait(1), 'Risk path did not wait for the order lock')
            finally:
                release_scanner.set()
            self.assertEqual(scanner.result(timeout=15), 0)
            risk.result(timeout=15)

        self.intent.refresh_from_db()
        self.wallet.refresh_from_db()
        self.direct_hold.refresh_from_db()
        self.assertEqual(self.intent.status, 'REVIEW')
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (0, 3000))
        self.assertIsNone(self.direct_hold.released_at)
        self.assertFalse(Transaction.objects.filter(tx_type='EARNINGS_RELEASE').exists())
