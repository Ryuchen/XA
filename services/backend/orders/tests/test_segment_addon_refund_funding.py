from unittest import skipUnless
from uuid import uuid4

from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from orders.tests import test_segment_addon_settlement as fixture
from stores import test_platform_pass_repayment as platform
from stores.context import store_context
from wallet.models import Transaction


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated store databases')
@override_settings(ALLOWED_HOSTS=['*'], PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
    BOSS_FUNDING_SELECTION_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=True,
    SERVICE_POINTS_FUNDING_TESTING=True, SERVICE_ORDINARY_TRANSFER_FUNDING_TESTING=True,
    ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
    ADDON_SETTLEMENT_INTERNAL_TESTING=True, ADDON_SEGMENT_SETTLEMENT_INTERNAL_TESTING=True,
    ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True, ADDON_SEGMENT_REFUND_REVISION_INTERNAL_TESTING=True,
    ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    SEGMENT_REPORT_ENABLED=True, SEGMENT_SETTLEMENT_ENABLED=True, SEGMENT_STANDARD_TRANSFER_ENABLED=True,
    PRESTART_SCOPE_SNAPSHOT_ENABLED=True, PRESTART_HANDOFF_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True, REQUIRE_RECHARGE_PROOF=True,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
    CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class SegmentAddonRefundFundingTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    setUp = platform.PlatformPassRepaymentTests.setUp
    post_receipt = platform.PlatformPassRepaymentTests.post
    action = platform.PlatformPassRepaymentTests.action

    def test_refund_releases_excess_funding_without_repaying_first_player(self):
        with store_context(self.first):
            worker = fixture.SegmentAddonSettlementTest(methodName='test_real_transfer_then_addon_settles_and_confirms_buyer_consumption')
            worker.client = APIClient()
            worker.setUp()
            self.addCleanup(worker.doCleanups)
            worker.service.commission_override = True
            worker.service.commission_type = 'PERCENT'
            worker.service.commission_rate = 0
            worker.service.save(update_fields=['commission_override', 'commission_type', 'commission_rate'])
            order, replacement, old, current, payment_id = worker.completed_addon_after_transfer(addon_compensation=1000)
            worker.login(worker.boss)
            case = worker.ok(worker.client.post(f'/api/orders/orders/{order.pk}/aftersale/', {
                'reason': '加购未履行', 'affected_provider_account_ids': [worker.accounts[replacement.pk].pk],
                'client_request_id': 'funded-segment-addon-case'}, format='json'))
            worker.submit_and_approve(order, old, worker.provider)
            worker.mature(order)
            self.assertEqual(worker.settle(order), [])
        receipt = self.post_receipt('shenglang', {**self.data, 'amount': 2000})
        self.assertEqual(receipt.status_code, 200, receipt.data)
        claim = receipt.data['data']['claim_id']
        self.assertEqual(self.action(claim, 'verify', {'request_id': str(uuid4())}, slug='shenglang').status_code, 200)
        url = f'/api/admin/platform/pass-refund-repayments/shenglang/{claim}/points-allocation'
        allocation = self.client.post(url, {'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 2000},
            format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(allocation.status_code, 200, allocation.data)
        with store_context(self.first):
            from engagement.confirmation import process_order
            self.assertFalse(process_order(order.pk))
            first_income = Transaction.objects.get(order=order, tx_type='INCOME')
            self.assertEqual(first_income.amount, 4000)
            worker.login(worker.admin)
            staff_url = f'/api/admin/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/'
            proposal = worker.ok(worker.client.post(staff_url, {'request_id': 'funded-full-addon-refund',
                'amount_raw': 5000, 'explanation': '双方同意退还加购', 'case_id': case['id']}, format='json'))
            user_url = f'/api/orders/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/{proposal["id"]}/'
            for user in (worker.boss, replacement):
                worker.login(user)
                worker.ok(worker.client.post(user_url, {'accepted': True}, format='json'))
            worker.login(worker.admin)
            worker.ok(worker.client.post(staff_url + f'{proposal["id"]}/execute/', {}, format='json'))
            from orders.models import OrderAddonFundingReleaseRun
            release = OrderAddonFundingReleaseRun.objects.get(refund__payment_id=payment_id)
            self.assertEqual((release.allocated_before, release.required_amount, release.released_amount),
                             (2000, 1000, 1000))
            from orders.points_funding_release import receipt_points_used_locked
            from stores.db import transaction
            with transaction.atomic():
                self.assertEqual(receipt_points_used_locked(release.releases.get().allocation.receipt), 1000)
            worker.ok(worker.client.post(f'/api/admin/aftersales/{case["id"]}/resolve/',
                {'resolution': '加购退款完成'}, format='json'))
            worker.submit_and_approve(order, current, replacement)
            worker.mature(order)
            self.assertTrue(process_order(order.pk))
            self.assertEqual(Transaction.objects.filter(pk=first_income.pk, amount=4000).count(), 1)
            order.refresh_from_db()
            self.assertEqual((order.provider_income, order.settlement_store_funding), (10000, 1000))
            from orders.models import OrderAddonConsumption, OrderSegmentAddonSettlementRevision
            self.assertEqual(OrderAddonConsumption.objects.get(payment_id=payment_id).refunded_units, 5000)
            self.assertEqual(OrderSegmentAddonSettlementRevision.objects.filter(manifest__order=order).count(), 1)
        with store_context(self.second):
            from orders.models import OrderSegmentAddonSettlementRevision
            self.assertFalse(OrderSegmentAddonSettlementRevision.objects.exists())
