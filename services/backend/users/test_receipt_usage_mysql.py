"""Targeted MySQL row-lock check for isolated receipt accounting."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings

from stores.context import store_context
from stores.db import transaction
from users import test_pass_repayment as fixtures
from users.models import PassReceiptUsageEvent, PassRefundRepaymentAllocation, PassRefundRepaymentReceipt
from users.pass_repayment import allocate, verify_receipt
from users.receipt_usage_sources import verify_allocations_locked
from wallet.models import Transaction


@skipUnless(connection.vendor == 'mysql', 'Requires an isolated MySQL test database')
@override_settings(RECEIPT_USAGE_LEDGER_ENABLED=True, RECEIPT_USAGE_LEDGER_INTERNAL_TESTING=False,
                   RECEIPT_USAGE_ALLOCATIONS_INTERNAL_TESTING=False)
class ReceiptUsageMySQLTests(TransactionTestCase):
    setUp = fixtures.PassRefundRepaymentTests.setUp
    receipt = fixtures.PassRefundRepaymentTests.receipt
    refund = fixtures.PassRefundRepaymentTests.refund

    def test_current_read_does_not_wait_for_allocation_row_locked_before_receipt(self):
        with store_context(self.store):
            receipt = self.receipt(amount=500)
            verify_receipt(actor='finance-1', receipt_id=receipt.pk)
            refund = self.refund(200)
            allocation = allocate(actor='finance-1', request_key='mysql-current-read',
                                  receipt_id=receipt.pk, refund_id=refund.pk, amount=200)
        allocation_locked, receipt_locked, verified = Event(), Event(), Event()

        def old_order():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SET SESSION innodb_lock_wait_timeout = 5')
                with store_context(self.store), transaction.atomic():
                    PassRefundRepaymentAllocation.objects.select_for_update().get(pk=allocation.pk)
                    allocation_locked.set()
                    if not receipt_locked.wait(10):
                        raise AssertionError('receipt thread did not start')
                    PassRefundRepaymentReceipt.objects.select_for_update().get(pk=receipt.pk)
            finally:
                close_old_connections()

        def receipt_first():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SET SESSION innodb_lock_wait_timeout = 5')
                if not allocation_locked.wait(10):
                    raise AssertionError('allocation thread did not start')
                with store_context(self.store), transaction.atomic():
                    PassRefundRepaymentReceipt.objects.select_for_update().get(pk=receipt.pk)
                    receipt_locked.set()
                    state = verify_allocations_locked(receipt.pk)
                    self.assertEqual((state.pass_units, state.version), (200, 1))
                    verified.set()
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(old_order)
            second = pool.submit(receipt_first)
            self.assertTrue(verified.wait(15), 'current read blocked on allocation row')
            second.result(timeout=15)
            first.result(timeout=15)

    def test_actual_transaction_override_is_rejected(self):
        with store_context(self.store):
            receipt = self.receipt(amount=500)
            verify_receipt(actor='finance-1', receipt_id=receipt.pk)
            with transaction.atomic():
                state = verify_allocations_locked(receipt.pk)
                self.assertEqual((state.version, state.pass_units), (0, 0))

        def overridden_transaction():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED')
                    cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                with store_context(self.store), transaction.atomic():
                    with self.assertRaisesMessage(ValidationError, 'READ COMMITTED'):
                        verify_allocations_locked(receipt.pk)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(overridden_transaction).result(timeout=15)

    def test_receipt_reader_waits_for_allocation_and_reads_committed_ledger(self):
        with store_context(self.store):
            receipt = self.receipt(amount=500)
            verify_receipt(actor='finance-1', receipt_id=receipt.pk)
            refund = self.refund(500)
            before = (self.wallet.balance, self.shop.balance, Transaction.objects.count())
            allocate(actor='finance-1', request_key='mysql-first-use', receipt_id=receipt.pk,
                     refund_id=refund.pk, amount=200)
        second_use = dict(actor='finance-1', request_key='mysql-second-use', receipt_id=receipt.pk,
                          refund_id=refund.pk, amount=300)
        writer_locked, reader_started, release_writer = Event(), Event(), Event()
        connections_by_role = {}

        def capture_connection(role):
            with connection.cursor() as cursor:
                cursor.execute('SELECT CONNECTION_ID()')
                connections_by_role[role] = cursor.fetchone()[0]

        def write():
            close_old_connections()
            try:
                capture_connection('writer')
                with store_context(self.store), transaction.atomic():
                    allocation = allocate(**second_use)
                    writer_locked.set()
                    if not release_writer.wait(12):
                        raise AssertionError('test did not release the writer')
                return allocation.pk
            finally:
                close_old_connections()

        def read():
            close_old_connections()
            try:
                capture_connection('reader')
                with store_context(self.store), transaction.atomic():
                    reader_started.set()
                    state = verify_allocations_locked(receipt.pk)
                    return state.version, state.pass_units
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(write)
            try:
                self.assertTrue(writer_locked.wait(5), 'writer did not allocate')
                reader = pool.submit(read)
                self.assertTrue(reader_started.wait(5), 'reader did not start')
                self.assertNotEqual(connections_by_role['writer'], connections_by_role['reader'])
                expires = monotonic() + 4
                with connection.cursor() as cursor:
                    while monotonic() < expires and not reader.done():
                        cursor.execute("""
                            SELECT requested.LOCK_DATA
                            FROM performance_schema.data_lock_waits AS waits
                            JOIN performance_schema.data_locks AS requested
                                ON requested.ENGINE = waits.ENGINE
                                AND requested.ENGINE_LOCK_ID = waits.REQUESTING_ENGINE_LOCK_ID
                            JOIN performance_schema.threads AS reader
                                ON reader.THREAD_ID = waits.REQUESTING_THREAD_ID
                            JOIN performance_schema.threads AS writer
                                ON writer.THREAD_ID = waits.BLOCKING_THREAD_ID
                            WHERE reader.PROCESSLIST_ID = %s AND writer.PROCESSLIST_ID = %s
                                AND requested.OBJECT_SCHEMA = DATABASE() AND requested.OBJECT_NAME = %s
                                AND requested.INDEX_NAME = 'PRIMARY' AND requested.LOCK_TYPE = 'RECORD'
                        """, (connections_by_role['reader'], connections_by_role['writer'],
                              PassRefundRepaymentReceipt._meta.db_table))
                        if (str(receipt.pk),) in cursor.fetchall():
                            break
                        sleep(0.025)
                    else:
                        self.fail('reader did not wait on the original receipt row')
            finally:
                release_writer.set()
            allocation_id = writer.result(timeout=10)
            self.assertEqual(reader.result(timeout=10), (2, 500))

        with store_context(self.store):
            self.assertEqual(allocate(**second_use).pk, allocation_id)
            self.assertEqual(list(PassReceiptUsageEvent.objects.filter(state__receipt=receipt)
                                  .order_by('version').values_list('version', 'kind', 'amount')),
                             [(1, 'PASS', 200), (2, 'PASS', 300)])
            self.wallet.refresh_from_db()
            self.shop.refresh_from_db()
            self.assertEqual((self.wallet.balance, self.shop.balance, Transaction.objects.count()), before)
