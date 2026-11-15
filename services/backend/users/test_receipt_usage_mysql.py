"""Targeted MySQL row-lock check for isolated receipt accounting."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings

from stores.context import store_context
from stores.db import transaction
from users import test_pass_repayment as fixtures
from users.models import PassRefundRepaymentAllocation, PassRefundRepaymentReceipt
from users.pass_repayment import allocate, verify_receipt
from users.receipt_usage_ledger import initialize_empty_locked
from users.receipt_usage_sources import verify_allocations_locked


@skipUnless(connection.vendor == 'mysql', 'Requires an isolated MySQL test database')
@override_settings(RECEIPT_USAGE_LEDGER_INTERNAL_TESTING=True,
    RECEIPT_USAGE_ALLOCATIONS_INTERNAL_TESTING=True, SERVICE_POINTS_CHECKOUT_TESTING=True,
    BOSS_FUNDING_SELECTION_ENABLED=True)
class ReceiptUsageMySQLTests(TransactionTestCase):
    setUp = fixtures.PassRefundRepaymentTests.setUp
    receipt = fixtures.PassRefundRepaymentTests.receipt
    refund = fixtures.PassRefundRepaymentTests.refund

    def test_current_read_does_not_wait_for_allocation_row_locked_before_receipt(self):
        with store_context(self.store):
            receipt = self.receipt(amount=500)
            verify_receipt(actor='finance-1', receipt_id=receipt.pk)
            with transaction.atomic():
                initialize_empty_locked(receipt.pk)
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
                initialize_empty_locked(receipt.pk)

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
