import os
"""Isolated checks of the unwired receipt-local ledger primitives."""
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from stores.context import store_context
from stores.db import transaction
from users.models import (PassReceiptUsageEvent, PassReceiptUsageState, PassRefundRepaymentAllocation,
                          PassRefundRepaymentReceipt)
from users.pass_repayment import allocate, verify_receipt
from users.receipt_usage_ledger import append_locked, initialize_empty_locked, verify_locked
from users import test_pass_repayment as pass_tests


@override_settings(RECEIPT_USAGE_LEDGER_INTERNAL_TESTING=True, SERVICE_POINTS_CHECKOUT_TESTING=True,
                   BOSS_FUNDING_SELECTION_ENABLED=True)
class ReceiptUsageLedgerTests(TestCase):
    setUp = pass_tests.PassRefundRepaymentTests.setUp
    receipt = pass_tests.PassRefundRepaymentTests.receipt
    refund = pass_tests.PassRefundRepaymentTests.refund

    @override_settings(RECEIPT_USAGE_ALLOCATIONS_INTERNAL_TESTING=True)
    def test_locked_usage_amounts_require_the_same_receipt_and_read_scope(self):
        from orders.points_business_access import _scope
        from users.receipt_usage_guard import capture_allocation_usage_locked, used_allocation_units_locked
        from users.receipt_usage_sources import verify_allocations_locked

        with store_context(self.store):
            with transaction.atomic():
                receipt = self._verified_receipt()
                initialize_empty_locked(receipt.pk)
                receipt.refresh_from_db()
                with patch('users.receipt_usage_sources.verify_allocations_locked', wraps=verify_allocations_locked) as verify:
                    usage = capture_allocation_usage_locked(receipt)
                    self.assertEqual(used_allocation_units_locked(receipt, verified_usage=usage), (0, 0))
                    self.assertEqual(used_allocation_units_locked(receipt, verified_usage=usage), (0, 0))
                    verify.assert_called_once_with(receipt.pk)
                    with patch.object(receipt, 'usage_version', 1), self.assertRaises(ValidationError):
                        used_allocation_units_locked(receipt, verified_usage=usage)
                    with transaction.atomic(), self.assertRaises(ValidationError):
                        used_allocation_units_locked(receipt, verified_usage=usage)
                    token = _scope.set(('tenant', 'store', 2, 1, 3, 'refund', 'addon'))
                    try:
                        with self.assertRaises(ValidationError):
                            used_allocation_units_locked(receipt, verified_usage=usage)
                    finally:
                        _scope.reset(token)
                    other_instance = PassRefundRepaymentReceipt.objects.get(pk=receipt.pk)
                    with self.assertRaises(ValidationError):
                        used_allocation_units_locked(other_instance, verified_usage=usage)
                    self.assertEqual(used_allocation_units_locked(receipt), (0, 0))
                    self.assertEqual(verify.call_count, 2)
            with transaction.atomic(), self.assertRaises(ValidationError):
                used_allocation_units_locked(receipt, verified_usage=usage)

    def test_unlocked_current_reads_require_supported_mysql_isolation(self):
        from unittest.mock import MagicMock
        from users.receipt_usage_sources import _require_current_unlocked_reads
        connection = MagicMock(vendor='mysql', in_atomic_block=True)
        cursor = connection.cursor.return_value.__enter__.return_value
        with patch('users.receipt_usage_sources.transaction.get_connection', return_value=connection):
            cursor.fetchone.return_value = ('READ-COMMITTED',)
            _require_current_unlocked_reads()
            cursor.fetchone.return_value = ('REPEATABLE-READ',)
            with self.assertRaisesMessage(ValidationError, 'READ COMMITTED'):
                _require_current_unlocked_reads()
            cursor.fetchone.return_value = None
            with self.assertRaisesMessage(ValidationError, 'READ COMMITTED'):
                _require_current_unlocked_reads()
            from django.db import DatabaseError
            cursor.execute.side_effect = DatabaseError('performance schema unavailable')
            with self.assertRaisesMessage(ValidationError, '无法核对'):
                _require_current_unlocked_reads()
            connection.in_atomic_block = False
            with self.assertRaises(ValidationError):
                _require_current_unlocked_reads()

    def _verified_receipt(self):
        receipt = self.receipt(amount=1000)
        verify_receipt(actor='finance-1', receipt_id=receipt.pk)
        return receipt

    @skipUnless(os.environ.get('XA_RECEIPT_USAGE_LEDGER_FORMAL') == '1',
                '共享到账用途账本释放链仍在接线（18-盘点共享到账账本待最终联调）；断言保留')
    def test_conservation_idempotency_and_per_allocation_release(self):
        with store_context(self.store), transaction.atomic():
            receipt = self._verified_receipt()
            with self.settings(RECEIPT_USAGE_LEDGER_INTERNAL_TESTING=False):
                with self.assertRaises(ValidationError):
                    initialize_empty_locked(receipt.pk)
            initialize_empty_locked(receipt.pk)
            order = dict(request_key='order-request', kind='ORDER', allocation_key='order-1',
                         amount=500, source_digest='a' * 64)
            first = append_locked(receipt.pk, **order)
            self.assertEqual(append_locked(receipt.pk, **order).pk, first.pk)
            with self.assertRaises(ValidationError):
                append_locked(receipt.pk, **{**order, 'amount': 499})
            append_locked(receipt.pk, request_key='pass-request', kind='PASS', allocation_key='pass-1',
                          amount=300, source_digest='b' * 64)
            append_locked(receipt.pk, request_key='release-request', kind='RELEASE', allocation_key='order-1',
                          amount=200, source_digest='c' * 64)
            state = verify_locked(receipt.pk)
            self.assertEqual((state.version, state.pass_units, state.order_units, state.released_units), (3, 300, 500, 200))
            from users.receipt_usage_sources import verify_allocations_locked
            with self.assertRaisesMessage(ValidationError, '释放来源尚未接入'):
                verify_allocations_locked(receipt.pk)
            for bad in (
                dict(request_key='extra', kind='PASS', allocation_key='pass-2', amount=401),
                dict(request_key='other-order-release', kind='RELEASE', allocation_key='order-2', amount=1),
                dict(request_key='over-release', kind='RELEASE', allocation_key='order-1', amount=301),
                dict(request_key='same-order', kind='ORDER', allocation_key='order-1', amount=1),
            ):
                with self.assertRaises(ValidationError):
                    append_locked(receipt.pk, source_digest='d' * 64, **bad)
            self.assertEqual(PassReceiptUsageEvent.objects.count(), 3)
            with transaction.atomic():
                PassReceiptUsageEvent.objects.filter(pk=first.pk).update(amount=499)
                with self.assertRaises(ValidationError):
                    verify_locked(receipt.pk)
                transaction.set_rollback(True)
            with transaction.atomic():
                PassRefundRepaymentReceipt.objects.filter(pk=receipt.pk).update(usage_version=99)
                with self.assertRaises(ValidationError):
                    verify_locked(receipt.pk)
                transaction.set_rollback(True)

    def test_rejects_existing_untracked_use_and_rolls_back_event(self):
        with store_context(self.store):
            receipt = self._verified_receipt()
            refund = self.refund(100)
            allocate(actor='finance-1', request_key='existing-pass', receipt_id=receipt.pk,
                     refund_id=refund.pk, amount=100)
            with transaction.atomic(), self.assertRaises(ValidationError):
                initialize_empty_locked(receipt.pk)
            self.assertFalse(PassReceiptUsageState.objects.filter(receipt=receipt).exists())
            clean = self.receipt(key='r2', reference='bank-2', amount=1000)
            verify_receipt(actor='finance-1', receipt_id=clean.pk)
            with transaction.atomic():
                with patch.object(PassRefundRepaymentReceipt, 'save', side_effect=RuntimeError('simulate init failure')):
                    with self.assertRaises(RuntimeError):
                        initialize_empty_locked(clean.pk)
                self.assertFalse(PassReceiptUsageState.objects.filter(receipt=clean).exists())
                initialize_empty_locked(clean.pk)
            with transaction.atomic():
                PassReceiptUsageState.objects.filter(receipt=clean).delete()
                with self.assertRaises(ValidationError):
                    verify_locked(clean.pk)
                with self.assertRaises(ValidationError):
                    initialize_empty_locked(clean.pk)
                transaction.set_rollback(True)
            with transaction.atomic():
                with patch.object(PassReceiptUsageState, 'save', side_effect=RuntimeError('simulate counter failure')):
                    with self.assertRaises(RuntimeError):
                        append_locked(clean.pk, request_key='counter-failure', kind='ORDER', allocation_key='order-1',
                                      amount=200, source_digest='a' * 64)
                self.assertEqual(verify_locked(clean.pk).version, 0)
                self.assertFalse(PassReceiptUsageEvent.objects.filter(state_id=clean.pk).exists())
                with patch.object(PassRefundRepaymentReceipt, 'save', side_effect=RuntimeError('simulate receipt failure')):
                    with self.assertRaises(RuntimeError):
                        append_locked(clean.pk, request_key='receipt-failure', kind='ORDER', allocation_key='order-1',
                                      amount=200, source_digest='a' * 64)
                self.assertEqual(verify_locked(clean.pk).version, 0)
                self.assertFalse(PassReceiptUsageEvent.objects.filter(state_id=clean.pk).exists())
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    append_locked(clean.pk, request_key='rolled-back', kind='ORDER', allocation_key='order-1',
                                  amount=200, source_digest='a' * 64)
                    raise RuntimeError('simulate writer failure')
            with transaction.atomic():
                state = verify_locked(clean.pk)
                self.assertEqual((state.version, state.order_units), (0, 0))
                self.assertFalse(state.events.exists())

    @skipUnless(os.environ.get('XA_RECEIPT_USAGE_LEDGER_FORMAL') == '1',
                '共享到账用途账本释放链仍在接线（18-盘点共享到账账本待最终联调）；断言保留')
    def test_source_verifier_detects_missing_or_tampered_pass_allocation(self):
        from users.receipt_usage_sources import record_pass_locked, verify_allocations_locked
        with store_context(self.store), transaction.atomic():
            receipt = self._verified_receipt()
            initialize_empty_locked(receipt.pk)
            refund = self.refund(200)
            with self.assertRaises(ValidationError):
                allocate(actor='finance-1', request_key='pass-actual', receipt_id=receipt.pk,
                         refund_id=refund.pk, amount=200)
            self.assertFalse(PassRefundRepaymentAllocation.objects.filter(receipt=receipt).exists())
            # A local fixture exercises the verifier while the real writer is
            # deliberately blocked pending complete ledger integration.
            allocation = PassRefundRepaymentAllocation.objects.create(request_key='pass-actual', receipt=receipt,
                refund=refund, amount=200, allocated_by='finance-1')
            with self.assertRaises(ValidationError):
                verify_allocations_locked(receipt.pk)
            event = record_pass_locked(allocation)
            self.assertEqual(record_pass_locked(allocation).pk, event.pk)
            from django.db import connection
            from django.test.utils import CaptureQueriesContext
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(verify_allocations_locked(receipt.pk).pass_units, 200)
            self.assertFalse(any('"orders_order"' in row['sql'] for row in queries.captured_queries))
            with transaction.atomic():
                type(allocation).objects.filter(pk=allocation.pk).update(amount=199)
                with self.assertRaises(ValidationError):
                    verify_allocations_locked(receipt.pk)
                transaction.set_rollback(True)


@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
@override_settings(RECEIPT_USAGE_LEDGER_INTERNAL_TESTING=True, SERVICE_POINTS_CHECKOUT_TESTING=True,
                   BOSS_FUNDING_SELECTION_ENABLED=True)
class ReceiptUsageTwoStoreTests(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_equal_receipt_ids_have_independent_versions_and_amounts(self):
        from django.utils import timezone
        from users.pass_repayment import register_receipt
        seen = []
        for store, amount in ((SimpleNamespace(slug='shenglang', database_alias='default'), 100),
                              (SimpleNamespace(slug='xinan', database_alias='store_b'), 200)):
            with store_context(store), transaction.atomic():
                receipt = register_receipt(actor='finance', request_key='same-request', external_reference='same-bank',
                    amount=1000, received_at=timezone.now(), evidence='隔离测试到账')
                verify_receipt(actor='finance', receipt_id=receipt.pk)
                initialize_empty_locked(receipt.pk)
                append_locked(receipt.pk, request_key='same-use', kind='ORDER', allocation_key='same-order',
                              amount=amount, source_digest='a' * 64)
                state = verify_locked(receipt.pk)
                seen.append((receipt.pk, state.version, state.order_units, state.chain_hash))
        self.assertEqual(seen[0][:2], seen[1][:2])
        self.assertEqual([row[2] for row in seen], [100, 200])
        self.assertNotEqual(seen[0][3], seen[1][3])

    def test_allocation_object_cannot_cross_stores_even_when_ids_match(self):
        from django.utils import timezone
        from users.pass_repayment import register_receipt
        from users.receipt_usage_sources import record_pass_locked, verify_allocations_locked
        prior = None
        for store in (SimpleNamespace(slug='shenglang', database_alias='default'),
                      SimpleNamespace(slug='xinan', database_alias='store_b')):
            with store_context(store), transaction.atomic():
                fixture = SimpleNamespace()
                pass_tests.PassRefundRepaymentTests.setUp(fixture)
                refund = pass_tests.PassRefundRepaymentTests.refund(fixture, 100)
                receipt = register_receipt(actor='finance', request_key='same-receipt', external_reference='same-bank',
                    amount=1000, received_at=timezone.now(), evidence='隔离测试到账')
                verify_receipt(actor='finance', receipt_id=receipt.pk)
                initialize_empty_locked(receipt.pk)
                allocation = PassRefundRepaymentAllocation.objects.create(request_key='same-allocation',
                    receipt=receipt, refund=refund, amount=100, allocated_by='finance')
                if prior is not None:
                    self.assertEqual((allocation.pk, receipt.pk), (prior.pk, prior.receipt_id))
                    with self.assertRaises(ValidationError):
                        record_pass_locked(prior)
                record_pass_locked(allocation)
                self.assertEqual(verify_allocations_locked(receipt.pk).pass_units, 100)
                prior = allocation
