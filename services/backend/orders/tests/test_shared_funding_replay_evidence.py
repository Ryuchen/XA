from uuid import uuid4

from django.test import TestCase, override_settings

from orders.models import OrderPointsFundingAllocation
from orders.tests import test_points_checkout_integration as fixtures
from stores import test_platform_pass_repayment as platform_tests
from stores.context import store_context


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
