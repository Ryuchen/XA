"""Paid marker and commit observation use real isolated store transactions."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.db import close_old_connections, connection, transaction
from django.conf import settings
from django.test import TransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from common.mysql_test_support import mysql_locking_statements, require_disposable_mysql
from orders.grab_opening_intent import observe_committed_payment, record_in_payment_transaction
from orders.models import Order, OrderGrabPoolOpeningIntent, OrderGrabPoolSnapshot
from orders.tests.factories import make_order, make_user, make_wallet
from wallet.models import Transaction
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store


class OpeningFixture(TransactionTestCase):
    def setUp(self):
        self.boss = make_user()
        self.account = get_or_create_account_for_legacy_user(self.boss)
        self.wallet = make_wallet(self.boss, balance=20000)
        self.wallet.account = self.account
        self.wallet.save(update_fields=['account'])
        self.order = make_order(self.boss, customer_account=self.account, amount=10000,
                                payment_status=Order.PaymentStatus.UNPAID)

    def create_paid_intent(self):
        order = Order.objects.select_for_update().get(pk=self.order.pk)
        order.payment_status = Order.PaymentStatus.PAID
        order.save(update_fields=['payment_status'])
        self.wallet.balance = 10000
        self.wallet.save(update_fields=['balance'])
        pay = Transaction.objects.create(wallet=self.wallet, order=order, tx_type='PAY', amount=-10000,
            balance_before=20000, balance_after=10000, tx_no=uuid4().hex)
        return record_in_payment_transaction(order.pk, pay.pk)


class GrabOpeningIntentTests(OpeningFixture):
    def test_observation_only_after_commit_and_retries_preserve_original_bound(self):
        with transaction.atomic():
            intent = self.create_paid_intent()
            self.assertEqual(intent.state, 'PENDING')
            self.assertEqual(record_in_payment_transaction(self.order.pk, intent.payment_id).pk, intent.pk)
            with self.assertRaisesMessage(RuntimeError, '独立最外层事务'):
                observe_committed_payment(self.order.pk)
            self.assertFalse(OrderGrabPoolSnapshot.objects.filter(order=self.order).exists())
        observed = observe_committed_payment(self.order.pk)
        self.assertEqual(observed.state, 'OBSERVED')
        self.assertIsNotNone(observed.commit_observed_at)
        replay = observe_committed_payment(self.order.pk)
        self.assertEqual((replay.pk, replay.commit_observed_at), (intent.pk, observed.commit_observed_at))
        self.assertFalse(OrderGrabPoolSnapshot.objects.filter(order=self.order).exists())

    def test_rolled_back_payment_cannot_produce_an_opening_intent(self):
        with self.assertRaisesMessage(RuntimeError, 'abort'):
            with transaction.atomic():
                self.create_paid_intent()
                raise RuntimeError('abort')
        with self.assertRaisesMessage(ValueError, '尚未提交或不存在'):
            observe_committed_payment(self.order.pk)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, 'UNPAID')
        self.assertFalse(OrderGrabPoolOpeningIntent.objects.exists())
        self.assertFalse(Transaction.objects.filter(order=self.order).exists())

    def test_failed_observation_is_retryable_without_an_open_pool(self):
        with transaction.atomic():
            self.create_paid_intent()
        with patch('orders.grab_opening_intent._clock_observation', side_effect=ValueError('clock unavailable')):
            with self.assertRaisesMessage(ValueError, 'clock unavailable'):
                observe_committed_payment(self.order.pk)
        self.assertEqual(OrderGrabPoolOpeningIntent.objects.get(order=self.order).state, 'PENDING')
        self.assertEqual(observe_committed_payment(self.order.pk).state, 'OBSERVED')
        self.assertFalse(OrderGrabPoolSnapshot.objects.filter(order=self.order).exists())


@skipUnless('store_b' in settings.DATABASES, 'Requires isolated second store')
class GrabOpeningIntentStoreIsolationTest(OpeningFixture):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_other_store_cannot_observe_an_order_or_opening_intent(self):
        second = xinan_store()
        with transaction.atomic():
            self.create_paid_intent()
        with store_context(second):
            with self.assertRaisesMessage(ValueError, '尚未提交或不存在'):
                observe_committed_payment(self.order.pk)
            self.assertFalse(OrderGrabPoolOpeningIntent.objects.exists())
        self.assertEqual(observe_committed_payment(self.order.pk).state, 'OBSERVED')


@skipUnless(connection.vendor == 'mysql', 'Requires isolated MySQL row locking')
class GrabOpeningIntentMySQLTest(OpeningFixture):
    def test_uncommitted_payment_cannot_be_observed_and_retry_is_idempotent(self):
        require_disposable_mysql(connection)
        store = resolve_store()
        entered, release = Event(), Event()

        def pay():
            close_old_connections()
            try:
                with store_context(store), transaction.atomic():
                    self.create_paid_intent()
                    entered.set()
                    if not release.wait(20):
                        raise RuntimeError('payment timeout')
            finally:
                close_old_connections()

        def observe():
            close_old_connections()
            try:
                with store_context(store):
                    return observe_committed_payment(self.order.pk).pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            payment = pool.submit(pay)
            self.assertTrue(entered.wait(15))
            observation = pool.submit(observe)
            try:
                deadline = monotonic() + 10
                while monotonic() < deadline and not mysql_locking_statements(connection, 'orders_order'):
                    sleep(.05)
                self.assertGreater(mysql_locking_statements(connection, 'orders_order'), 0)
                self.assertFalse(observation.done())
            finally:
                release.set()
            payment.result(timeout=20)
            intent_id = observation.result(timeout=20)
        self.assertEqual(observe_committed_payment(self.order.pk).pk, intent_id)
        self.assertEqual(OrderGrabPoolOpeningIntent.objects.count(), 1)
        self.assertFalse(OrderGrabPoolSnapshot.objects.filter(order=self.order).exists())
