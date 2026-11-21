from contextlib import nullcontext
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase, TestCase, override_settings

from orders.models import Order, OrderOriginalShareTransfer, OrderPointsFundingAllocation
from orders.tests import test_points_checkout_integration as fixtures
from stores import test_platform_pass_repayment as platform_tests
from stores.context import store_context
from stores.platform_points_funding import _addon_funding_scope


@override_settings(SERVICE_POINTS_MYSQL_ENABLED=True)
class PlatformTransferFundingScopeTests(SimpleTestCase):
    def test_order_lock_precedes_classifying_transfer_for_read_and_write(self):
        order = Order(pk=31)
        for receipt_id in (None, 45):
            with (self.subTest(receipt_id=receipt_id),
                  patch.object(Order.objects, 'select_for_update') as lock,
                  patch.object(Order.objects, 'filter') as unlocked,
                  patch.object(OrderOriginalShareTransfer.objects, 'filter') as history,
                  patch('orders.points_service_payment.needs_integrated_processing', return_value=True),
                  patch('orders.financial_lock.lock_existing_order_money') as read_guard,
                  patch('orders.points_transfer_refund_access.after_transfer', return_value=nullcontext()) as transferred,
                  patch('orders.points_business_access.service_order') as untransferred):
                locked_query = lock.return_value.filter.return_value
                locked_query.first.return_value = order
                unlocked.return_value.first.return_value = order

                def history_after_lock(*, previous_segment__attribution__order):
                    locked_query.first.assert_called_once_with()
                    self.assertIs(previous_segment__attribution__order, order)
                    return history.return_value

                history.side_effect = history_after_lock
                history.return_value.exists.return_value = True
                with _addon_funding_scope(order.pk, receipt_id):
                    transferred.assert_called_once_with(order.pk, phase='settlement', extra_receipt_id=receipt_id)
                lock.return_value.filter.assert_called_once_with(pk=order.pk)
                untransferred.assert_not_called()
                self.assertEqual(read_guard.call_count, int(receipt_id is None))


@override_settings(**fixtures.PointsFundingPlatformRecoveryTests._overridden_settings)
class SharedFundingReplayEvidenceTests(TestCase):
    databases = {'default', 'store_b'}
    payload = fixtures.PointsFundingPlatformRecoveryTests.payload
    quoted_payload = fixtures.PointsFundingPlatformRecoveryTests.quoted_payload
    create_assigned_points_order = fixtures.PointsFundingPlatformRecoveryTests.create_assigned_points_order
    setUp = fixtures.PointsFundingPlatformRecoveryTests.setUp
    fund = fixtures.PointsFundingPlatformRecoveryTests.fund
    verify = fixtures.PointsFundingPlatformRecoveryTests.verify

    def test_platform_retry_and_recovery_reject_other_receipt_tampering(self):
        self.assertEqual(self.verify().status_code, 200)
        first = self.fund()
        self.assertEqual(first.status_code, 200, first.data)
        registered = platform_tests.PlatformPassRepaymentTests.post(self, 'xinan', {
            **self.data, 'request_id': str(uuid4()), 'external_reference': 'second-replay-receipt'})
        self.assertEqual(registered.status_code, 200, registered.data)
        claim = registered.data['data']['claim_id']
        verified = platform_tests.PlatformPassRepaymentTests.action(self, claim, 'verify', {'request_id': str(uuid4())})
        self.assertEqual(verified.status_code, 200, verified.data)
        url = f'/api/admin/platform/pass-refund-repayments/xinan/{claim}/points-allocation'
        payload = {'request_id': str(uuid4()), 'order_id': self.order.pk, 'amount': 200}
        second = self.client.post(url, payload, format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(second.status_code, 200, second.data)
        operation_id = second.data['data']['operation_id']
        self.assertEqual(self.client.post(url, payload, format='json', HTTP_AUTHORIZATION='Bearer ' + self.token).status_code, 200)
        with store_context(self.second):
            OrderPointsFundingAllocation.objects.filter(pk=first.data['data']['allocation_id']).update(amount=301)
        retry = self.client.post(url, payload, format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual((retry.status_code, retry.data['errorCode']), (409, 'POINTS_FUNDING_REVIEW'))
        recover = self.client.post(f'/api/admin/platform/points-funding/xinan/{operation_id}/recover', {}, format='json',
                                   HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual((recover.status_code, recover.data['errorCode']), (409, 'POINTS_FUNDING_REVIEW'))
