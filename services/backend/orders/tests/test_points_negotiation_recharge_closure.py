"""Isolated consent, original-source refund and funding release after recharge."""
import tempfile
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework.exceptions import ValidationError

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.confirmation import process_order
from engagement.models import MemberProfile, PointLedger
from orders.models import (AfterSale, Order, OrderPointsFundingRelease, OrderPointsRefund,
                           OrderPointsRefundSettlement, OrderProvider)
from orders.points_funding_evidence import verify_order_funding
from orders.tests import test_points_recharge_closure as base
from orders.tests import test_points_redemption_preview as quote_fixture
from orders.tests.factories import make_console_user
from orders.tests.test_release_flow import screenshot
from stores.context import store_context
from stores.db import transaction
from wallet.models import ProviderReport, Transaction


@override_settings(ALLOWED_HOSTS=['*'], PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
    POINTS_REDEMPTION_PREVIEW_ENABLED=True, DISCOUNT_OPTIONS_PREVIEW_ENABLED=True,
    DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True,
    SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=True,
    SERVICE_POINTS_CHECKOUT_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
    SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True,
    SERVICE_POINTS_SETTLEMENT_TESTING=True, SERVICE_POINTS_NEGOTIATION_TESTING=True,
    SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True, SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True,
    SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True, SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True,
    SERVICE_POINTS_FUNDING_RELEASE_TESTING=True, SERVICE_POINTS_REFUNDED_FUNDING_TESTING=True,
    SERVICE_POINTS_REFUNDED_SETTLEMENT_TESTING=True, SERVICE_POINTS_CONSUMPTION_TESTING=True,
    SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True,
    NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
    ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    LEGACY_FORCE_AUTH_COMPAT=False, REQUIRE_RECHARGE_PROOF=False,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class PointsNegotiatedRechargeClosureTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    setUp = base.PointsRealRechargeClosureTest.setUp
    payload = quote_fixture.PointsServiceQuoteTest.payload
    fund = base.PointsRealRechargeClosureTest.fund
    verify = base.PointsRealRechargeClosureTest.verify

    def test_consent_refund_releases_excess_and_settles_once(self):
        self.assertEqual(self.verify().status_code, 200)
        from django.core.exceptions import ValidationError as DjangoValidationError
        from orders.points_funding_write import allocate as allocate_points
        from orders.points_funding_evidence import verified_order_allocations_locked
        from orders.points_funding_plan import PointsFundingPlanError
        from orders.models import OrderPointsFundingAllocation
        from users.receipt_usage_ledger import initialize_empty_locked
        with store_context(self.second), override_settings(RECEIPT_USAGE_LEDGER_INTERNAL_TESTING=True), transaction.atomic():
            initialize_empty_locked(self.receipt_id)
            with self.assertRaisesMessage(DjangoValidationError, '该到账已建用途账本'):
                allocate_points(actor='finance', request_key='ledger-unconnected-order',
                    receipt_id=self.receipt_id, order_id=self.order.pk, amount=100)
            # A corrupted/preexisting allocation must also be rejected by
            # order-side verification before any refund or release writes.
            allocation = OrderPointsFundingAllocation.objects.create(request_key='fixture-untracked', receipt_id=self.receipt_id,
                redemption=self.order.points_redemption, amount=100, allocated_by='finance')
            with self.assertRaisesMessage(PointsFundingPlanError, '到账用途账本尚未接入订单核对'):
                verified_order_allocations_locked(self.order)
            from users.receipt_usage_sources import record_order_locked, verify_allocations_locked
            event = record_order_locked(allocation)
            self.assertEqual(record_order_locked(allocation).pk, event.pk)
            self.assertEqual(verify_allocations_locked(self.receipt_id).order_units, 100)
            with transaction.atomic():
                OrderPointsFundingAllocation.objects.filter(pk=allocation.pk).update(amount=99)
                with self.assertRaises(DjangoValidationError):
                    verify_allocations_locked(self.receipt_id)
                transaction.set_rollback(True)
            transaction.set_rollback(True)
        paid = self.fund({'request_id': str(uuid4()), 'order_id': self.order.pk, 'amount': 500})
        self.assertEqual(paid.status_code, 200, paid.data)
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media), store_context(self.second):
            order = Order.objects.get(pk=self.order.pk)
            assignment = OrderProvider.objects.get(order=order)
            player = APIClient(HTTP_HOST='xa.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(assignment.provider_account)[0])
            boss = APIClient(HTTP_HOST='xa.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(order.customer_account)[0])
            started = player.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart')
            self.assertEqual(started.data['code'], 0, started.data)
            from orders.points_negotiation_production import require_order_locked
            with transaction.atomic():
                with self.assertRaises(ValidationError):
                    require_order_locked(order.pk, phase='before', refund_equivalent_units=1000)
            with override_settings(SERVICE_POINTS_NEGOTIATION_PRODUCTION_ENABLED=True), transaction.atomic():
                self.assertEqual(require_order_locked(order.pk, phase='before', refund_equivalent_units=1000).pk, order.pk)
                with self.assertRaises(ValidationError):
                    require_order_locked(order.pk, phase='before', refund_equivalent_units=6000)
            user = make_console_user(['order:view', 'support:edit', 'order:refund', 'report:view', 'report:audit'])
            staff = get_or_create_account_for_legacy_user(user)
            user.admin_membership.account = staff; user.admin_membership.save(update_fields=['account'])
            admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
            opened = admin.post('/api/admin/aftersales/open-negotiation/', {'order_id': order.pk,
                'reason': '隔离测试协商退款', 'affected_provider_account_ids': [assignment.provider_account_id],
                'client_request_id': 'recharged-points-case'}, format='json')
            self.assertEqual(opened.data['code'], 0, opened.data)
            case_id = opened.data['data']['id']
            from orders.points_negotiation_preview import preview_locked
            with override_settings(SERVICE_POINTS_NEGOTIATION_PRODUCTION_ENABLED=True), transaction.atomic():
                with patch('orders.points_negotiation_preview.require_isolated_checkout',
                           side_effect=ValidationError('非隔离环境')):
                    estimate = preview_locked(case_id, refund_equivalent_units=1000)
                self.assertEqual((estimate['coin_units'], estimate['points_hundredths']), (660, 1666))
            # Account-only buyers are valid when both the order and wallet
            # agree; retain the mapping check when a legacy user is present.
            from club_accounts.models import LegacyAccountMap
            with transaction.atomic():
                Order.objects.filter(pk=order.pk).update(customer=None)
                type(self.wallet).objects.filter(pk=self.wallet.pk).update(user=None)
                LegacyAccountMap.objects.filter(account=self.account).delete()
                estimate = preview_locked(case_id, refund_equivalent_units=1000)
                self.assertEqual((estimate['coin_units'], estimate['points_hundredths']), (660, 1666))
                transaction.set_rollback(True)
            proposal = admin.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/', {
                'request_id': 'recharged-points-proposal', 'expected_version': 0, 'refund_units': 1000,
                'provider_terms': [{'account_id': assignment.provider_account_id, 'retained_units': 3800}],
                'explanation': '按原支付构成退款'}, format='json')
            self.assertEqual(proposal.data['code'], 0, proposal.data)
            proposal_id = proposal.data['data']['id']
            decisions = f'/api/orders/orders/{order.pk}/negotiation-proposal/'
            self.assertEqual(boss.post(decisions, {'proposal_id': proposal_id, 'accepted': True}, format='json').data['code'], 0)
            execute = f'/api/admin/aftersales/{case_id}/negotiation-execution/'
            body = {'proposal_id': proposal_id, 'client_request_id': 'recharged-points-refund'}
            refused = admin.post(execute, body, format='json')
            self.assertNotEqual(refused.data.get('code'), 0, refused.data)
            self.assertFalse(OrderPointsRefund.objects.filter(redemption__order=order).exists())
            self.assertEqual(player.post(decisions, {'proposal_id': proposal_id, 'accepted': True}, format='json').data['code'], 0)
            from orders.points_negotiation_execution import prepare_locked
            with override_settings(SERVICE_POINTS_NEGOTIATION_PRODUCTION_ENABLED=True), transaction.atomic():
                with patch('orders.points_negotiation_execution.require_isolated_checkout',
                           side_effect=ValidationError('非隔离环境')), patch(
                           'orders.points_negotiation_preview.require_isolated_checkout',
                           side_effect=ValidationError('非隔离环境')):
                    prepared = prepare_locked(case_id, proposal_id, operator=staff)
                self.assertEqual((prepared['calculation']['coin_units'],
                                  prepared['calculation']['points_hundredths'],
                                  prepared['providers'][0]['pending_units']), (660, 1666, 1000))
            # A preexisting allocation attached to a newly versioned receipt
            # is an intentionally inconsistent fixture. The real endpoint
            # must reject it without committing any part of the refund.
            from users.models import PassReceiptUsageState, PassRefundRepaymentReceipt
            with transaction.atomic():
                PassReceiptUsageState.objects.create(receipt_id=self.receipt_id)
                PassRefundRepaymentReceipt.objects.filter(pk=self.receipt_id).update(usage_version=0)
                denied = admin.post(execute, body, format='json')
                self.assertNotEqual(denied.data.get('code'), 0, denied.data)
                self.assertFalse(OrderPointsRefund.objects.filter(redemption__order=order).exists())
                self.assertFalse(OrderPointsFundingRelease.objects.filter(allocation__redemption__order=order).exists())
                self.wallet.refresh_from_db(); self.profile.refresh_from_db()
                self.assertEqual((self.wallet.balance, self.profile.points_balance), (4000, 5000))
                transaction.set_rollback(True)
            def fail_release(**kwargs):
                self.assertEqual(require_order_locked(order.pk, phase='posting').pk, order.pk)
                raise RuntimeError('模拟补资释放写入失败')
            with override_settings(SERVICE_POINTS_NEGOTIATION_PRODUCTION_ENABLED=True), patch(
                    'orders.points_funding_release.OrderPointsFundingReleaseEvidence.objects.create',
                    side_effect=fail_release):
                with self.assertRaisesMessage(RuntimeError, '模拟补资释放写入失败'):
                    admin.post(execute, body, format='json')
            self.wallet.refresh_from_db(); self.profile.refresh_from_db()
            self.assertEqual((self.wallet.balance, self.profile.points_balance), (4000, 5000))
            self.assertFalse(OrderPointsRefund.objects.filter(redemption__order=order).exists())
            self.assertFalse(OrderPointsFundingRelease.objects.filter(allocation__redemption__order=order).exists())
            result = admin.post(execute, body, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            replay = admin.post(execute, body, format='json')
            self.assertEqual(replay.data['code'], 0, replay.data)
            receipt = OrderPointsRefund.objects.get(redemption__order=order)
            self.assertEqual((receipt.coin_units, receipt.points_hundredths), (660, 1666))
            with override_settings(SERVICE_POINTS_NEGOTIATION_PRODUCTION_ENABLED=True), transaction.atomic():
                self.assertEqual(require_order_locked(order.pk, phase='after').pk, order.pk)
                with self.assertRaises(ValidationError):
                    require_order_locked(order.pk, phase='before', refund_equivalent_units=1000)
                from orders.points_benefit_snapshot import verify_locked as verify_benefit_snapshot
                with patch('orders.points_benefit_snapshot.require_isolated_checkout',
                           side_effect=ValidationError('非隔离环境')):
                    self.assertEqual(verify_benefit_snapshot(receipt.redemption).redemption_id, receipt.redemption_id)
            self.wallet.refresh_from_db(); self.profile.refresh_from_db()
            self.assertEqual((self.wallet.balance, self.profile.points_balance), (4660, 6666))
            self.assertEqual(AfterSale.objects.get(pk=case_id).status, 'PARTIALLY_REFUNDED')
            self.assertEqual(sum(OrderPointsFundingRelease.objects.filter(
                allocation__redemption__order=order).values_list('amount', flat=True)), 40)
            # Recreate a versioned receipt around the real committed release
            # inside a rolled-back savepoint. Allocation-only reconciliation
            # must not overlook this unintegrated release on the shared receipt.
            with override_settings(RECEIPT_USAGE_LEDGER_INTERNAL_TESTING=True,
                                   SERVICE_POINTS_FIRST_REFUND_USAGE_TESTING=True), transaction.atomic():
                PassReceiptUsageState.objects.create(receipt_id=self.receipt_id)
                PassRefundRepaymentReceipt.objects.filter(pk=self.receipt_id).update(usage_version=0)
                allocation = OrderPointsFundingAllocation.objects.get(redemption__order=order)
                record_order_locked(allocation)
                with self.assertRaisesMessage(DjangoValidationError, '到账用途与实际分配凭证不一致'):
                    verify_allocations_locked(self.receipt_id)
                transaction.set_rollback(True)
            with transaction.atomic():
                funding = verify_order_funding(order.pk)
                self.assertEqual((funding.required, funding.allocated, funding.remaining), (460, 460, 0))
            report = ProviderReport.objects.get(order=order)
            for kind in ('COMPLETION', 'RESULT'):
                response = player.post(f'/api/wallet/reports/{report.pk}/images/',
                    {'kind': kind, 'image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
            self.assertEqual(player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json').data['code'], 0)
            audited = admin.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
            self.assertEqual(audited.data['code'], 0, audited.data)
            report.refresh_from_db()
            with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
                self.assertTrue(process_order(order.pk))
                self.assertFalse(process_order(order.pk))
            settlement = OrderPointsRefundSettlement.objects.get(posting__points_refund=receipt)
            self.assertEqual((settlement.paid_units, settlement.funding_units, settlement.provider_units), (3340, 460, 3800))
            self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 1)
            self.assertEqual(PointLedger.objects.filter(order=order, kind='ORDER_POINTS_RETURN').count(), 1)
        with store_context(self.first):
            self.assertFalse(OrderPointsRefund.objects.exists())
