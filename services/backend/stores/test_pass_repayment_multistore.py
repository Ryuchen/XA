from django.test import TestCase
from django.utils import timezone

from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from users.models import PassRefundRepaymentAllocation, PassRefundRepaymentReceipt
from users.pass_repayment import allocate, register_receipt, verify_receipt


class PassRepaymentStoreIsolationTests(TestCase):
    databases = {'default', 'store_b'}

    def test_same_identifiers_in_separate_stores_never_cross_allocate(self):
        from stores.test_platform_pass_refunds import PlatformPassRefundTests
        first, second = resolve_store(), xinan_store()
        with store_context(first):
            a = PlatformPassRefundTests.create_shortfall(self, 100)
            receipt = register_receipt(actor='finance', request_key='request', external_reference='ref', amount=100,
                received_at=timezone.now(), evidence='凭证')
            verify_receipt(actor='finance', receipt_id=receipt.pk)
            allocate(actor='finance', request_key='allocation', receipt_id=receipt.pk, refund_id=a.pk, amount=100)
            ids = (receipt.pk, a.pk)
        with store_context(second):
            b = PlatformPassRefundTests.create_shortfall(self, 100)
            receipt2 = register_receipt(actor='finance', request_key='request', external_reference='ref', amount=100,
                received_at=timezone.now(), evidence='凭证')
            self.assertEqual((receipt2.pk, b.pk), ids)
            self.assertEqual(PassRefundRepaymentAllocation.objects.count(), 0)
            self.assertIsNone(receipt2.verified_at)
            verify_receipt(actor='finance', receipt_id=receipt2.pk)
            allocate(actor='finance', request_key='allocation', receipt_id=receipt2.pk, refund_id=b.pk, amount=40)
        with store_context(first):
            self.assertEqual(PassRefundRepaymentReceipt.objects.get(pk=ids[0]).allocations.get().amount, 100)
        with store_context(second):
            self.assertEqual(PassRefundRepaymentReceipt.objects.get(pk=ids[0]).allocations.get().amount, 40)
