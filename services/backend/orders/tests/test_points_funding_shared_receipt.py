from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.db.migrations.recorder import MigrationRecorder
from django.test import TestCase, override_settings
from unittest.mock import patch

from orders.models import OrderPointsFundingAllocation
from orders.points_service_payment import record_locked
from orders.tests import test_points_service_payment as service_tests
from stores.context import store_context
from stores.db import connection, transaction
from users import test_pass_repayment as pass_tests
from users.models import PassRefundRepaymentAllocation
from users.pass_repayment import allocate, verify_receipt
from stores import test_platform_pass_repayment as platform_tests


@override_settings(SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True)
class SharedOfflineReceiptTests(TestCase):
    receipt = pass_tests.PassRefundRepaymentTests.receipt
    refund = pass_tests.PassRefundRepaymentTests.refund
    make_token = service_tests.ServicePointsReceiptTests.make_token
    create_payment = service_tests.ServicePointsReceiptTests.create_payment

    def setUp(self):
        service_tests.ServicePointsReceiptTests.setUp(self)
        token = self.make_token()
        with transaction.atomic():
            order, payment = self.create_payment()
            self.redemption = record_locked(order_id=order.pk, payment_id=payment.pk, token=token)
        pass_tests.PassRefundRepaymentTests.setUp(self)

    def test_pass_repayment_respects_points_allocation_of_same_receipt(self):
        with store_context(self.store):
            refund = self.refund(1000)
            receipt = self.receipt(amount=1000)
            verify_receipt(actor='finance-2', receipt_id=receipt.pk)
            # Represents a persisted earmark; no public points-allocation
            # writer is enabled, and this alone does not release any income.
            OrderPointsFundingAllocation.objects.create(request_key='points-1', receipt=receipt,
                redemption=self.redemption, amount=600, allocated_by='finance-1')
            with self.assertRaises(ValidationError):
                allocate(actor='finance-1', request_key='too-much', receipt_id=receipt.pk,
                         refund_id=refund.pk, amount=401)
            row = allocate(actor='finance-1', request_key='pass-1', receipt_id=receipt.pk,
                           refund_id=refund.pk, amount=400)
            self.assertEqual(row.amount, 400)
            self.assertEqual(PassRefundRepaymentAllocation.objects.count(), 1)
            self.assertEqual(receipt.points_funding_allocations.get().amount, 600)

    def test_missing_table_is_compatible_only_before_migration(self):
        with store_context(self.store):
            refund = self.refund(1000)
            receipt = self.receipt(amount=1000)
            verify_receipt(actor='finance-2', receipt_id=receipt.pk)
            tables = [name for name in connection.introspection.table_names()
                      if name != OrderPointsFundingAllocation._meta.db_table]
            MigrationRecorder.Migration.objects.filter(app='orders', name='0124_orderpointsfundingallocation').delete()
            with patch.object(connection.introspection, 'table_names', return_value=tables):
                allocate(actor='finance-1', request_key='old-schema', receipt_id=receipt.pk,
                         refund_id=refund.pk, amount=100)
                MigrationRecorder.Migration.objects.create(app='orders', name='0124_orderpointsfundingallocation')
                with self.assertRaises(DatabaseError):
                    allocate(actor='finance-1', request_key='missing-after-migration', receipt_id=receipt.pk,
                             refund_id=refund.pk, amount=100)


@override_settings(ALLOWED_HOSTS=['*'], PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
                   SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True)
class SharedOfflineReceiptPlatformTests(TestCase):
    databases = {'default', 'store_b'}
    post = platform_tests.PlatformPassRepaymentTests.post
    action = platform_tests.PlatformPassRepaymentTests.action
    reconciliation = platform_tests.PlatformPassRepaymentTests.reconciliation
    setup_refunds = platform_tests.PlatformPassRepaymentTests.setup_refunds
    make_token = service_tests.ServicePointsReceiptTests.make_token
    create_payment = service_tests.ServicePointsReceiptTests.create_payment

    def setUp(self):
        platform_tests.PlatformPassRepaymentTests.setUp(self)
        with store_context(self.second):
            service_tests.ServicePointsReceiptTests.setUp(self)
            token = self.make_token()
            with transaction.atomic():
                order, payment = self.create_payment()
                self.redemption = record_locked(order_id=order.pk, payment_id=payment.pk, token=token)

    def test_reconciliation_fails_closed_and_pass_api_respects_shared_funds(self):
        from uuid import uuid4
        refund_id, _ = self.setup_refunds()
        registered = self.post('xinan')
        self.assertEqual(registered.status_code, 200, registered.data)
        claim_id, receipt_id = registered.data['data']['claim_id'], registered.data['data']['receipt_id']
        self.assertEqual(self.action(claim_id, 'verify', {'request_id': str(uuid4())}).status_code, 200)
        with store_context(self.second):
            OrderPointsFundingAllocation.objects.create(request_key='future-points', receipt_id=receipt_id,
                redemption=self.redemption, amount=300, allocated_by=str(self.finance.pk))
        denied = self.action(claim_id, 'allocate', {'request_id': str(uuid4()), 'refund_id': refund_id, 'amount': 201})
        self.assertEqual((denied.status_code, denied.data['errorCode']), (409, 'REPAYMENT_REJECTED'))
        accepted = self.action(claim_id, 'allocate', {'request_id': str(uuid4()), 'refund_id': refund_id, 'amount': 200})
        self.assertEqual(accepted.status_code, 200, accepted.data)
        response = self.reconciliation()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['data']['status'], 'REVIEW')
        self.assertIsNone(response.data['data']['claims'][0]['receipt']['unallocated_amount'])
        with store_context(self.first):
            self.assertFalse(OrderPointsFundingAllocation.objects.exists())
