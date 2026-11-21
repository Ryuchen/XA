"""Cross-store, real-API coverage for transfer funding revisions."""
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.db.migrations.recorder import MigrationRecorder
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import (Order, OrderOriginalShareTransfer, OrderParticipationSegment, OrderProvider,
                           OrderPointsTransferFundingRelease, OrderPointsTransferFundingRevision)
from orders.points_funding_plan import PointsFundingPlanError
from orders.tests import test_points_checkout_integration as fixture
from orders.tests.factories import make_console_user, make_provider
from orders.tests.test_release_flow import screenshot
from stores.context import store_context
from stores.db import transaction
from users.models import PassRefundRepaymentReceipt
from wallet.models import Transaction


class PointsTransferRevisionScopeTests(SimpleTestCase):
    def test_report_and_settlement_reuse_verification_only_for_the_same_order(self):
        from orders import points_transfer_revision as revisions
        from orders.points_business_access import _bind, permits

        store = SimpleNamespace(slug='scoped-store', database_alias='default')
        connection = SimpleNamespace(alias=store.database_alias, in_atomic_block=True)
        order = Order(pk=1, customer_account_id=10, service_id=20)
        other = Order(pk=2, customer_account_id=10, service_id=20)

        def verify(candidate, *, allow_missing_last):
            self.assertIs(candidate, order)
            self.assertTrue(permits(order, phase='refund', kind='transfer'))
            self.assertFalse(allow_missing_last)
            return ({3: 10}, None)

        with (patch('orders.points_business_access.configured', return_value=True),
              patch('orders.points_business_access.current_store', return_value=store),
              patch.object(revisions, 'current_store', return_value=store),
              patch.object(transaction, 'get_connection', return_value=connection),
              patch.object(revisions, 'require_isolated_checkout', side_effect=ValidationError('requires isolated checkout')),
              patch.object(revisions, '_verify_revisions_locked', side_effect=verify) as read):
            for phase in ('report', 'settlement'):
                with self.subTest(phase=phase), _bind(phase=phase, kind='transfer', order_id=order.pk,
                        account_id=order.customer_account_id, service_id=order.service_id):
                    self.assertEqual(revisions.verify_revisions_locked(order), ({3: 10}, None))
                    self.assertTrue(permits(order, phase=phase, kind='transfer'))
                    self.assertFalse(permits(order, phase='refund', kind='transfer'))
                    with self.assertRaisesMessage(ValidationError, 'requires isolated checkout'):
                        revisions.verify_revisions_locked(other)
            self.assertEqual(read.call_count, 2)


@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
@override_settings(ALLOWED_HOSTS=['*'], PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
    POINTS_REDEMPTION_PREVIEW_ENABLED=True, DISCOUNT_OPTIONS_PREVIEW_ENABLED=True,
    DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True,
    SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=True,
    SERVICE_POINTS_CHECKOUT_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
    SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True,
    SERVICE_POINTS_SETTLEMENT_TESTING=True, ORIGINAL_ATTRIBUTION_ENABLED=True,
    SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, SEGMENT_STANDARD_TRANSFER_ENABLED=True,
    SEGMENT_REPORT_ENABLED=True, SEGMENT_SETTLEMENT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    SERVICE_POINTS_TRANSFER_CAPTURE_TESTING=True, SERVICE_POINTS_TRANSFER_FUNDING_REVISION_TESTING=True,
    SERVICE_POINTS_TRANSFER_SETTLEMENT_TESTING=True, SERVICE_POINTS_CONSUMPTION_TESTING=True,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True, LEGACY_FORCE_AUTH_COMPAT=False,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class PointsTransferRevisionTests(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    setUp = fixture.PointsFundingPlatformRecoveryTests.setUp
    payload = fixture.PointsFundingPlatformRecoveryTests.payload
    quoted_payload = fixture.PointsFundingPlatformRecoveryTests.quoted_payload
    create_assigned_points_order = fixture.PointsFundingPlatformRecoveryTests.create_assigned_points_order
    verify = fixture.PointsFundingPlatformRecoveryTests.verify
    fund = fixture.PointsFundingPlatformRecoveryTests.fund

    def test_zero_new_shortfall_releases_the_whole_earmark(self):
        from orders.points_funding_evidence import verify_order_funding
        from orders.points_funding_release import receipt_points_used_locked
        from stores import test_platform_pass_repayment as platform
        with TemporaryDirectory(prefix='xa-points-transfer-zero-gap-') as media, override_settings(MEDIA_ROOT=media):
            self.assertEqual(self.verify().status_code, 200)
            response = self.fund({'request_id': str(uuid4()), 'order_id': self.order.pk, 'amount': 500})
            self.assertEqual(response.status_code, 200, response.data)
            with store_context(self.second):
                order = Order.objects.get(pk=self.order.pk)
                from orders.points_transfer_revision import record_latest_revision_locked
                with transaction.atomic(), self.assertRaises(PointsFundingPlanError):
                    record_latest_revision_locked(order)
                old = OrderProvider.objects.get(order=order, released_at__isnull=True).provider_account
                player = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(old)[0])
                response = player.post(f'/api/orders/orders/{order.pk}/start/',
                    {'entry_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                replacement = make_provider(deposit_mode='EARNINGS')
                new = get_or_create_account_for_legacy_user(replacement)
                replacement.escort_profile.account = new; replacement.escort_profile.save(update_fields=['account'])
                staff_user = make_console_user(['order:view', 'order:dispatch'])
                staff = get_or_create_account_for_legacy_user(staff_user)
                staff_user.admin_membership.account = staff; staff_user.admin_membership.save(update_fields=['account'])
                admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
                service = order.service
                service.commission_override = True; service.commission_rate = 50
                service.save(update_fields=['commission_override', 'commission_rate'])
                response = admin.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason': '双方同意',
                    'transfers': [{'old_provider_id': old.pk, 'new_provider_id': new.pk,
                        'split_type': 'PERCENT', 'split_value': 0, 'penalty_amount': 0,
                        'original_share': {'retained': 4000, 'transferred': 6000, 'agreement': '双方同意'}}]}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                revision = OrderPointsTransferFundingRevision.objects.get(order=order)
                self.assertEqual(revision.after_funding['additional_store_funding_units'], 0)
                self.assertEqual(list(revision.releases.values_list('amount', flat=True)), [500])
                with transaction.atomic():
                    proof = verify_order_funding(order.pk, allow_unstarted=True)
                    self.assertEqual((proof.required, proof.allocated, proof.remaining), (0, 0, 0))
                    receipt = PassRefundRepaymentReceipt.objects.select_for_update().get(pk=self.receipt_id)
                    self.assertEqual(receipt_points_used_locked(receipt), 0)
            first, second = platform.PlatformPassRepaymentTests.setup_refunds(self)
            for refund_id, amount in ((first, 300), (second, 200)):
                response = platform.PlatformPassRepaymentTests.action(self, self.claim, 'allocate',
                    {'request_id': str(uuid4()), 'refund_id': refund_id, 'amount': amount})
                self.assertEqual(response.status_code, 200, response.data)
            with store_context(self.second):
                receipt = PassRefundRepaymentReceipt.objects.get(pk=self.receipt_id)
                self.assertEqual(sum(receipt.allocations.values_list('amount', flat=True)), 500)
                self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

    def test_release_reuse_and_later_reallocation_with_real_receipt(self):
        from stores import test_platform_pass_repayment as platform
        from orders.points_funding_evidence import verify_order_funding
        from orders.points_funding_release import receipt_points_used_locked
        from orders.points_transfer_revision import verify_revisions_locked
        with TemporaryDirectory(prefix='xa-points-transfer-revision-') as media, override_settings(MEDIA_ROOT=media):
            self.assertEqual(self.verify().status_code, 200)
            funded = self.fund({'request_id': str(uuid4()), 'order_id': self.order.pk, 'amount': 500})
            self.assertEqual(funded.status_code, 200, funded.data)
            with store_context(self.second):
                order = Order.objects.get(pk=self.order.pk)
                old = OrderProvider.objects.get(order=order, released_at__isnull=True).provider_account
                old_client = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(old)[0])
                response = old_client.post(f'/api/orders/orders/{order.pk}/start/',
                    {'entry_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                replacement = make_provider(deposit_mode='EARNINGS')
                new = get_or_create_account_for_legacy_user(replacement)
                replacement.escort_profile.account = new; replacement.escort_profile.save(update_fields=['account'])
                player = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(new)[0])
                staff_user = make_console_user(['order:view', 'order:dispatch', 'report:audit'])
                staff = get_or_create_account_for_legacy_user(staff_user)
                staff_user.admin_membership.account = staff; staff_user.admin_membership.save(update_fields=['account'])
                admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
                service = order.service
                service.commission_override = True; service.commission_rate = 30
                service.save(update_fields=['commission_override', 'commission_rate'])
                url = f'/api/admin/orders/{order.pk}/transfer/'
                def body(source, target, retained, transferred):
                    return {'reason': '双方同意', 'transfers': [{'old_provider_id': source.pk,
                        'new_provider_id': target.pk, 'split_type': 'PERCENT', 'split_value': 0,
                        'penalty_amount': 0, 'original_share': {'retained': retained,
                            'transferred': transferred, 'agreement': '双方同意'}}]}
                first_body = body(old, new, 4000, 6000)
                from django.db import DatabaseError
                with patch('orders.points_transfer_revision.record_latest_revision_locked',
                           side_effect=DatabaseError('injected missing storage')):
                    denied = admin.post(url, first_body, format='json')
                self.assertNotEqual(denied.data.get('code', 400), 0, denied.data)
                self.assertFalse(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists())
                with patch('orders.points_transfer_revision.OrderPointsTransferFundingRelease.objects.bulk_create',
                           side_effect=RuntimeError('injected release failure')):
                    with self.assertRaisesMessage(RuntimeError, 'injected release failure'):
                        admin.post(url, first_body, format='json')
                self.assertFalse(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists())
                self.assertFalse(OrderPointsTransferFundingRevision.objects.filter(order=order).exists())
                with transaction.atomic():
                    receipt = PassRefundRepaymentReceipt.objects.select_for_update().get(pk=self.receipt_id)
                    self.assertEqual(receipt_points_used_locked(receipt), 500)
                response = admin.post(url, first_body, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                replay = admin.post(url, first_body, format='json')
                self.assertNotEqual(replay.data.get('code', 400), 0, replay.data)
                self.assertEqual(OrderPointsTransferFundingRevision.objects.filter(order=order).count(), 1)
                revision = OrderPointsTransferFundingRevision.objects.get(order=order)
                self.assertEqual((revision.before_funding['additional_store_funding_units'],
                                  revision.after_funding['additional_store_funding_units']), (800, 440))
                self.assertEqual(list(revision.releases.values_list('amount', flat=True)), [60])
                with transaction.atomic():
                    receipt = PassRefundRepaymentReceipt.objects.select_for_update().get(pk=self.receipt_id)
                    self.assertEqual(receipt_points_used_locked(receipt), 440)
                    proof = verify_order_funding(order.pk, allow_unstarted=True)
                    self.assertEqual((proof.required, proof.allocated, proof.remaining), (440, 440, 0))
                    from stores.db import connection
                    missing = {OrderPointsTransferFundingRevision._meta.db_table,
                               OrderPointsTransferFundingRelease._meta.db_table}
                    tables = [name for name in connection.introspection.table_names() if name not in missing]
                    with transaction.atomic():
                        # The fast test runner synchronizes models without recording this migration.
                        MigrationRecorder(connection).migration_qs.get_or_create(
                            app='orders', name='0141_orderpointstransferfundingrevision_and_more')
                        with patch.object(connection.introspection, 'table_names', return_value=tables):
                            with self.assertRaises(DatabaseError):
                                receipt_points_used_locked(receipt)
                        transaction.set_rollback(True)
                with transaction.atomic():
                    from orders.models import OrderPointsFundingAllocation
                    from rest_framework.exceptions import ValidationError
                    allocation = OrderPointsFundingAllocation.objects.get(redemption__order=order)
                    OrderPointsFundingAllocation.objects.filter(pk=allocation.pk).update(
                        redemption=None, service_order=order)
                    receipt = PassRefundRepaymentReceipt.objects.select_for_update().get(pk=self.receipt_id)
                    with self.assertRaises(ValidationError):
                        receipt_points_used_locked(receipt)
                    transaction.set_rollback(True)
                from datetime import timedelta
                from django.db import transaction as directory_transaction
                from django.utils import timezone
                from stores.models import PlatformPointsFundingOperation
                with directory_transaction.atomic(using='default'), transaction.atomic():
                    operation = PlatformPointsFundingOperation.objects.using('default').get(claim_id=self.claim)
                    operation.completed_at = timezone.now() + timedelta(days=1)
                    operation.save(using='default', update_fields=['completed_at'])
                    receipt = PassRefundRepaymentReceipt.objects.select_for_update().get(pk=self.receipt_id)
                    with self.assertRaises(ValidationError):
                        receipt_points_used_locked(receipt)
                    directory_transaction.set_rollback(True, using='default')
            # The same platform receipt can now cover another authorized use.
            first_refund, _ = platform.PlatformPassRepaymentTests.setup_refunds(self)
            oversized = platform.PlatformPassRepaymentTests.action(self, self.claim, 'allocate',
                {'request_id': str(uuid4()), 'refund_id': first_refund, 'amount': 61})
            self.assertNotEqual(oversized.status_code, 200, oversized.data)
            reused = platform.PlatformPassRepaymentTests.action(self, self.claim, 'allocate',
                {'request_id': str(uuid4()), 'refund_id': first_refund, 'amount': 60})
            self.assertEqual(reused.status_code, 200, reused.data)
            with store_context(self.second):
                with transaction.atomic():
                    receipt = PassRefundRepaymentReceipt.objects.select_for_update().get(pk=self.receipt_id)
                    self.assertEqual((receipt_points_used_locked(receipt), receipt.allocations.get().amount), (440, 60))
                new_segment = OrderParticipationSegment.objects.get(attribution__order=order,
                    provider_account=new, released_at__isnull=True)
                response = player.post(f'/api/orders/orders/{order.pk}/participation-start/',
                    {'segment_id': new_segment.pk, 'entry_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                service.commission_rate = 25; service.save(update_fields=['commission_rate'])
                returned = admin.post(url, body(new, old, 2000, 4000), format='json')
                self.assertEqual(returned.data['code'], 0, returned.data)
                self.assertEqual(OrderPointsTransferFundingRevision.objects.filter(order=order).count(), 2)
                self.assertEqual(OrderPointsTransferFundingRelease.objects.filter(revision__order=order).count(), 1)
                with transaction.atomic():
                    released, _ = verify_revisions_locked(order)
                    self.assertEqual(sum(released.values()), 60)
                    proof = verify_order_funding(order.pk, allow_unstarted=True)
                    self.assertEqual((proof.required, proof.allocated, proof.remaining), (560, 440, 120))
                returned_segment = OrderParticipationSegment.objects.get(attribution__order=order,
                    provider_account=old, released_at__isnull=True)
                response = old_client.post(f'/api/orders/orders/{order.pk}/participation-start/',
                    {'segment_id': returned_segment.pk, 'entry_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
            other = platform.PlatformPassRepaymentTests.post(self, 'xinan',
                {**self.data, 'request_id': str(uuid4()), 'external_reference': 'TX-SECOND'})
            self.assertEqual(other.status_code, 200, other.data)
            other_claim = other.data['data']['claim_id']
            verified = platform.PlatformPassRepaymentTests.action(self, other_claim, 'verify', {'request_id': str(uuid4())})
            self.assertEqual(verified.status_code, 200, verified.data)
            result = self.client.post(f'/api/admin/platform/pass-refund-repayments/xinan/{other_claim}/points-allocation',
                {'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 120}, format='json',
                HTTP_AUTHORIZATION='Bearer ' + self.token)
            self.assertEqual(result.status_code, 200, result.data)
            with store_context(self.second):
                with transaction.atomic():
                    proof = verify_order_funding(order.pk)
                    self.assertEqual((proof.required, proof.allocated, proof.remaining), (560, 560, 0))
                    self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
                    revision = OrderPointsTransferFundingRevision.objects.filter(order=order).order_by('pk').first()
                    revision.after_funding = {}; revision.save(update_fields=['after_funding'])
                    with self.assertRaises(PointsFundingPlanError):
                        verify_order_funding(order.pk)
                    from rest_framework.exceptions import ValidationError
                    receipt = PassRefundRepaymentReceipt.objects.select_for_update().get(pk=self.receipt_id)
                    with self.assertRaises(ValidationError):
                        receipt_points_used_locked(receipt)
                    transaction.set_rollback(True)
                with transaction.atomic(), override_settings(SERVICE_POINTS_TRANSFER_FUNDING_REVISION_TESTING=False):
                    from rest_framework.exceptions import ValidationError
                    receipt = PassRefundRepaymentReceipt.objects.select_for_update().get(pk=self.receipt_id)
                    with self.assertRaises(ValidationError):
                        receipt_points_used_locked(receipt)
                # Each seat allows at most two transfers, including before service.
                service.commission_rate = 70; service.save(update_fields=['commission_rate'])
                third = admin.post(url, body(old, new, 2000, 2000), format='json')
                self.assertNotEqual(third.data.get('code', 400), 0, third.data)
                self.assertEqual(OrderPointsTransferFundingRevision.objects.filter(order=order).count(), 2)
                with transaction.atomic():
                    proof = verify_order_funding(order.pk, allow_unstarted=True)
                    self.assertEqual((proof.required, proof.allocated, proof.remaining), (560, 560, 0))
                # Complete and report each participation using the real APIs.
                old_segment = OrderParticipationSegment.objects.filter(attribution__order=order,
                    provider_account=old, released_at__isnull=False).order_by('pk').first()
                self.assertNotEqual(old_segment.pk, returned_segment.pk)
                for client, segment in ((old_client, old_segment), (player, new_segment)):
                    response = client.post(f'/api/orders/orders/{order.pk}/participation-evidence/',
                        {'segment_id': segment.pk, 'completion_image': screenshot()}, format='multipart')
                    self.assertEqual(response.data['code'], 0, response.data)
                response = old_client.post(f'/api/orders/orders/{order.pk}/complete/',
                    {'completion_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                def report(client, segment):
                    for action, extra in [('open', {}), ('upload', {'image': screenshot()}), ('submit', {'version': 0})]:
                        response = client.post(f'/api/orders/orders/{order.pk}/segment-report/',
                            {'segment_id': segment.pk, 'action': action, 'client_request_id': str(uuid4()), **extra},
                            format='multipart' if action == 'upload' else 'json')
                        self.assertEqual(response.data['code'], 0, response.data)
                    response = admin.post(f'/api/admin/orders/{order.pk}/segments/{segment.pk}/report-review/',
                        {'action': 'approve', 'version': 1}, format='json')
                    self.assertEqual(response.data['code'], 0, response.data)
                report(old_client, old_segment); report(old_client, returned_segment)
                from datetime import timedelta
                from django.utils import timezone
                from engagement.confirmation import process_order
                from engagement.models import ConsumptionConfirmation
                from orders.models import OrderSegmentPersonSettlement, OrderSegmentSettlementManifest
                from orders.services import settle_order
                future = timezone.now() + timedelta(hours=100)
                def settle():
                    with patch('django.utils.timezone.now', return_value=future), transaction.atomic():
                        return settle_order(Order.objects.select_for_update().get(pk=order.pk))
                self.assertEqual(settle(), [order.providers.get(provider_account=old).provider_id])
                self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                report(player, new_segment)
                self.assertEqual(settle(), [replacement.pk])
                order.refresh_from_db()
                self.assertEqual((order.provider_income, order.settlement_store_funding), (4560, 560))
                manifest = OrderSegmentSettlementManifest.objects.get(order=order)
                self.assertEqual(sum(item['amount'] for item in manifest.plan['points_funding']['allocations']), 560)
                self.assertEqual(OrderSegmentPersonSettlement.objects.filter(manifest=manifest).count(), 2)
                with patch('django.utils.timezone.now', return_value=future):
                    self.assertTrue(process_order(order.pk))
                    self.assertFalse(process_order(order.pk))
                self.assertEqual(ConsumptionConfirmation.objects.get(order=order).amount, 4000)
                self.assertEqual(settle(), [])
            with store_context(self.first):
                self.assertFalse(OrderPointsTransferFundingRevision.objects.exists())
