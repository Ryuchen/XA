"""Real database snapshot boundaries; no dispatch or payment is simulated."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.conf import settings
from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from console.grab_config import save_grab_window
from orders.grab_pool_snapshot import create_initial_snapshot
from orders.models import Order, OrderGrabPoolSnapshot
from orders.tests.factories import make_order, make_service, make_user
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.tests_hosts import xinan_store
from wallet.models import SystemConfig


class GrabPoolSnapshotTest(TransactionTestCase):
    def setUp(self):
        self.order = make_order(make_user(), service=make_service(), status=Order.Status.PENDING,
                                payment_status=Order.PaymentStatus.PAID)
        save_grab_window(30, 0, lambda *args: None)

    def create(self):
        return create_initial_snapshot(self.order.pk)

    def test_rules_and_deadlines_are_stored_once_and_not_rewritten(self):
        with transaction.atomic():
            first = self.create()
        self.assertEqual((first.window_seconds, first.config_version, first.required_provider_count), (30, 1, 1))
        self.assertEqual((first.first_deadline - first.opened_at).total_seconds(), 30)
        self.assertEqual((first.original_total_deadline - first.opened_at).total_seconds(), 1800)
        self.assertEqual(first.total_deadline, first.original_total_deadline)
        self.assertEqual(first.visibility_delays['GOLD'], 30)
        save_grab_window(60, 1, lambda *args: None)
        SystemConfig.objects.update_or_create(key='provider_pass_delay_seconds_GOLD', defaults={'value': '20'})
        with transaction.atomic():
            retry = self.create()
            self.assertEqual((retry.pk, retry.window_seconds, retry.visibility_delays['GOLD']), (first.pk, 30, 30))
        self.order.refresh_from_db()
        self.assertIsNone(self.order.pool_entered_at)
        self.assertEqual(self.order.status, Order.Status.PENDING)

    def test_failure_and_transaction_rollback_leave_no_partial_snapshot(self):
        with self.assertRaises(RuntimeError):
            self.create()
        with self.assertRaisesMessage(ValueError, 'rollback test'):
            with transaction.atomic():
                self.create()
                raise ValueError('rollback test')
        self.assertFalse(OrderGrabPoolSnapshot.objects.exists())
        SystemConfig.objects.filter(key='automatic_grab_window').update(value='bad')
        with transaction.atomic(), self.assertRaises(ValueError):
            self.create()
        self.assertFalse(OrderGrabPoolSnapshot.objects.exists())

    def test_wrong_order_state_and_ambiguous_count_rejected(self):
        for changes in ({'payment_status': 'UNPAID'}, {'status': 'GRABBED'},
                        {'is_gift_snapshot': True}, {'requires_boss_selection': True},
                        {'required_provider_count': None}):
            Order.objects.filter(pk=self.order.pk).update(**changes)
            with transaction.atomic(), self.assertRaises(ValueError):
                self.create()
            Order.objects.filter(pk=self.order.pk).update(payment_status='PAID', status='PENDING',
                is_gift_snapshot=False, requires_boss_selection=False, required_provider_count=1)
        self.assertFalse(OrderGrabPoolSnapshot.objects.exists())

    def test_first_window_extends_a_shorter_total_timeout(self):
        Order.objects.filter(pk=self.order.pk).update(pending_timeout_minutes=1)
        save_grab_window(90, 1, lambda *args: None)
        with transaction.atomic():
            snapshot = self.create()
        self.assertEqual((snapshot.original_total_deadline - snapshot.opened_at).total_seconds(), 60)
        self.assertEqual((snapshot.total_deadline - snapshot.opened_at).total_seconds(), 90)

    def test_staff_arranged_double_uses_paid_provider_count(self):
        # Existing legacy escort_mode can still say SINGLE for an unassigned
        # double order; the paid provider-count snapshot is authoritative.
        Order.objects.filter(pk=self.order.pk).update(required_provider_count=2, escort_mode='SINGLE', dispatch_route='STAFF')
        with transaction.atomic():
            snapshot = self.create()
        self.assertEqual(snapshot.required_provider_count, 2)
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.dispatch_route), ('PENDING', 'STAFF'))

    def test_real_row_lock_serializes_duplicate_openings(self):
        if connection.vendor != 'mysql':
            self.skipTest('requires isolated MySQL row locks')
        barrier = Barrier(2)
        def attempt(_):
            close_old_connections()
            try:
                with transaction.atomic():
                    # Establish a potentially stale repeatable-read snapshot
                    # before either transaction acquires the order row lock.
                    OrderGrabPoolSnapshot.objects.count()
                    barrier.wait(timeout=10)
                    return self.create().pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(attempt, range(2)))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(OrderGrabPoolSnapshot.objects.count(), 1)

    def test_real_current_read_does_not_freeze_stale_pass_delays(self):
        if connection.vendor != 'mysql':
            self.skipTest('requires isolated MySQL repeatable-read transactions')
        def change_delay():
            close_old_connections()
            try:
                from users.pass_rules import lock_pass_config
                with transaction.atomic():
                    lock_pass_config()
                    SystemConfig.objects.update_or_create(key='provider_pass_delay_seconds_GOLD', defaults={'value': '20'})
            finally:
                close_old_connections()
        with transaction.atomic():
            # Establish a consistent-read snapshot before the concurrent edit.
            SystemConfig.objects.count()
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(change_delay).result(timeout=10)
            snapshot = self.create()
            self.assertEqual(snapshot.visibility_delays['GOLD'], 20)

    def test_real_current_read_does_not_freeze_stale_window(self):
        if connection.vendor != 'mysql':
            self.skipTest('requires isolated MySQL repeatable-read transactions')
        def change_window():
            close_old_connections()
            try:
                save_grab_window(40, 1, lambda *args: None)
            finally:
                close_old_connections()
        with transaction.atomic():
            SystemConfig.objects.count()
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(change_window).result(timeout=10)
            snapshot = self.create()
            self.assertEqual((snapshot.window_seconds, snapshot.config_version), (40, 2))


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated databases')
class GrabPoolSnapshotMultiStoreTest(TransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    reset_sequences = True

    def test_same_order_ids_keep_independent_store_rules(self):
        stores = (resolve_store(), xinan_store())
        ids = []
        for store, seconds in zip(stores, (30, 60)):
            with store_context(store):
                order = make_order(make_user(), service=make_service(), payment_status='PAID')
                save_grab_window(seconds, 0, lambda *args: None)
                with transaction.atomic():
                    snapshot = create_initial_snapshot(order.pk)
                ids.append(order.pk)
                self.assertEqual(snapshot.window_seconds, seconds)
        self.assertEqual(ids[0], ids[1])
        for store, seconds in zip(stores, (30, 60)):
            with store_context(store):
                self.assertEqual(OrderGrabPoolSnapshot.objects.get(order_id=ids[0]).window_seconds, seconds)
