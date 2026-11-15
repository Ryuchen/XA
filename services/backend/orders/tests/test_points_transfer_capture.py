"""Real transfer and funding reconciliation on two disposable store databases."""
from unittest import skipUnless

from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import (Order, OrderOriginalShareTransfer, OrderParticipationSegment,
                           OrderProvider, OrderSegmentFinancialCoverage, OrderSegmentFinancialSnapshot,
                           OrderSegmentTransferBasis)
from orders.points_funding_plan import PointsFundingPlanError
from orders.points_transfer_funding import plan_points_transfer
from orders.tests.factories import make_console_user, make_provider
from orders.tests import test_points_checkout_integration as fixture
from orders.tests.test_release_flow import screenshot
from stores.context import store_context
from stores.db import transaction
from wallet.models import Transaction


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
    SERVICE_POINTS_TRANSFER_CAPTURE_TESTING=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    REQUIRE_ORDER_EVIDENCE_IMAGES=True, LEGACY_FORCE_AUTH_COMPAT=False,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class PointsTransferCaptureTwoStoreTests(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    setUp = fixture.PointsFundingPlatformRecoveryTests.setUp
    payload = fixture.PointsFundingPlatformRecoveryTests.payload
    quoted_payload = fixture.PointsFundingPlatformRecoveryTests.quoted_payload
    create_assigned_points_order = fixture.PointsFundingPlatformRecoveryTests.create_assigned_points_order
    verify = fixture.PointsFundingPlatformRecoveryTests.verify
    fund = fixture.PointsFundingPlatformRecoveryTests.fund

    @override_settings(SERVICE_POINTS_TRANSFER_FUNDING_REVISION_TESTING=True)
    def test_historical_fixed_and_changed_commission_use_each_entry_snapshot(self):
        from tempfile import TemporaryDirectory
        from orders.points_transfer_history import plan_historic_points_transfer
        with TemporaryDirectory(prefix='xa-points-transfer-fixed-') as media, override_settings(MEDIA_ROOT=media):
            with store_context(self.second):
                order = Order.objects.get(pk=self.order.pk)
                old = OrderProvider.objects.get(order=order, released_at__isnull=True).provider_account
                old_client = APIClient(HTTP_HOST='xa.esports.club',
                    HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(old)[0])
                response = old_client.post(f'/api/orders/orders/{order.pk}/start/',
                    {'entry_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                replacement = make_provider(deposit_mode='EARNINGS')
                new = get_or_create_account_for_legacy_user(replacement)
                replacement.escort_profile.account = new; replacement.escort_profile.save(update_fields=['account'])
                player = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(new)[0])
                staff_user = make_console_user(['order:view', 'order:dispatch'])
                staff = get_or_create_account_for_legacy_user(staff_user)
                staff_user.admin_membership.account = staff; staff_user.admin_membership.save(update_fields=['account'])
                admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
                service = order.service
                service.commission_override = True; service.commission_type = 'FIXED'; service.commission_fixed = 5000
                service.save(update_fields=['commission_override', 'commission_type', 'commission_fixed'])
                def transfer(source, target, retained, transferred):
                    response = admin.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason': '双方同意',
                        'transfers': [{'old_provider_id': source.pk, 'new_provider_id': target.pk,
                            'split_type': 'PERCENT', 'split_value': 0, 'penalty_amount': 0,
                            'original_share': {'retained': retained, 'transferred': transferred, 'agreement': '双方同意'}}]}, format='json')
                    self.assertEqual(response.data['code'], 0, response.data)
                transfer(old, new, 4000, 6000)
                new_segment = OrderParticipationSegment.objects.get(attribution__order=order, provider_account=new)
                response = player.post(f'/api/orders/orders/{order.pk}/participation-start/',
                    {'segment_id': new_segment.pk, 'entry_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                service.commission_type = 'PERCENT'; service.commission_rate = 25
                service.save(update_fields=['commission_type', 'commission_rate'])
                transfer(new, old, 2000, 4000)
                events = list(OrderOriginalShareTransfer.objects.filter(
                    previous_segment__attribution__order=order).order_by('pk'))
                with transaction.atomic():
                    first = plan_historic_points_transfer(order.pk, through_transfer_id=events[0].pk)
                    second = plan_historic_points_transfer(order.pk, through_transfer_id=events[1].pk)
                    self.assertEqual([(row[2], row[3], row[4]) for row in first.segment_shares],
                                     [(2400, 480, 1920), (3600, 3000, 600)])
                    self.assertEqual([(row[2], row[3], row[4]) for row in second.segment_shares],
                                     [(2400, 480, 1920), (1200, 1000, 200), (2400, 600, 1800)])
                    self.assertEqual((first.funding.additional_store_funding_units, first.funding.store_income_units), (0, 1480))
                    self.assertEqual((second.funding.additional_store_funding_units, second.funding.store_income_units), (0, 80))
                self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

    def test_real_transfer_uses_deal_before_points_and_recalculates_funding(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory(prefix='xa-points-transfer-') as media, override_settings(MEDIA_ROOT=media):
            with store_context(self.second):
                order = Order.objects.get(pk=self.order.pk)
                assignment = OrderProvider.objects.get(order=order, released_at__isnull=True)
                old = assignment.provider_account
                old_client = APIClient(HTTP_HOST='xa.esports.club',
                    HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(old)[0])
                started = old_client.post(f'/api/orders/orders/{order.pk}/start/',
                                          {'entry_image': screenshot()}, format='multipart')
                self.assertEqual(started.data['code'], 0, started.data)
                replacement = make_provider(deposit_mode='EARNINGS')
                replacement_account = get_or_create_account_for_legacy_user(replacement)
                replacement.escort_profile.account = replacement_account
                replacement.escort_profile.save(update_fields=['account'])
                staff_user = make_console_user(['order:view', 'order:dispatch'])
                staff = get_or_create_account_for_legacy_user(staff_user)
                staff_user.admin_membership.account = staff
                staff_user.admin_membership.save(update_fields=['account'])
                admin = APIClient(HTTP_HOST='admin.esports.club',
                    HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
                service = order.service
                service.commission_override = True
                service.commission_rate = 30
                service.save(update_fields=['commission_override', 'commission_rate'])
                body = {'reason': '实际积分订单转单', 'transfers': [{
                    'old_provider_id': old.pk, 'new_provider_id': replacement_account.pk,
                    'split_type': 'PERCENT', 'split_value': 0, 'penalty_amount': 0,
                    'original_share': {'retained': 4000, 'transferred': 6000, 'agreement': '双方同意'}}]}
                url = f'/api/admin/orders/{order.pk}/transfer/'
                with override_settings(SERVICE_POINTS_TRANSFER_CAPTURE_TESTING=False):
                    denied = admin.post(url, body, format='json')
                    self.assertNotEqual(denied.data.get('code', 400), 0, denied.data)
                self.assertFalse(OrderOriginalShareTransfer.objects.filter(
                    previous_segment__attribution__order=order).exists())
                with transaction.atomic():
                    from engagement.models import PointLedger
                    ledger = PointLedger.objects.get(order=order, kind='ORDER_POINTS_REDEEM')
                    PointLedger.objects.filter(pk=ledger.pk).update(amount=ledger.amount + 1)
                    denied = admin.post(url, body, format='json')
                    self.assertNotEqual(denied.data.get('code', 400), 0, denied.data)
                    self.assertFalse(OrderOriginalShareTransfer.objects.filter(
                        previous_segment__attribution__order=order).exists())
                    transaction.set_rollback(True)
                with transaction.atomic():
                    from orders.models import OrderSegmentSettlementManifest
                    OrderSegmentSettlementManifest.objects.create(order=order, plan={}, plan_hash='0' * 64)
                    denied = admin.post(url, body, format='json')
                    self.assertNotEqual(denied.data.get('code', 400), 0, denied.data)
                    self.assertFalse(OrderOriginalShareTransfer.objects.filter(
                        previous_segment__attribution__order=order).exists())
                    transaction.set_rollback(True)
                with transaction.atomic():
                    from orders.models import OrderPointsFundingAllocation, OrderPointsRedemption
                    OrderPointsFundingAllocation.objects.create(
                        request_key='fixture-existing-allocation', receipt_id=self.receipt_id,
                        redemption=OrderPointsRedemption.objects.get(order=order), amount=1,
                        allocated_by='test-finance')
                    denied = admin.post(url, body, format='json')
                    self.assertNotEqual(denied.data.get('code', 400), 0, denied.data)
                    self.assertFalse(OrderOriginalShareTransfer.objects.filter(
                        previous_segment__attribution__order=order).exists())
                    transaction.set_rollback(True)
                before = Transaction.objects.filter(order=order).count()
                transferred = admin.post(url, body, format='json')
                self.assertEqual(transferred.data['code'], 0, transferred.data)
                self.assertEqual(Transaction.objects.filter(order=order).count(), before)
                coverage = OrderSegmentFinancialCoverage.objects.get(attribution__order=order)
                self.assertEqual((coverage.source, coverage.deal_amount, coverage.status),
                                 ('WALLET_POINTS', 6000, 'REVIEW'))
                allocation = OrderSegmentTransferBasis.objects.get(transfer__previous_segment__attribution__order=order)
                self.assertEqual((allocation.basis_before, allocation.retained_basis, allocation.transferred_basis),
                                 (6000, 2400, 3600))
                snapshots = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order)
                                 .order_by('segment_id'))
                self.assertEqual([(row.incoming_basis, row.retained_basis, row.commission_rate, row.income_at_entry)
                                  for row in snapshots], [(6000, 2400, 20, 4800), (3600, None, 30, 2520)])
                incoming = OrderParticipationSegment.objects.get(attribution__order=order,
                    provider_account=replacement_account, released_at__isnull=True)
                with transaction.atomic():
                    with self.assertRaises(PointsFundingPlanError):
                        plan_points_transfer(order.pk)
                    with self.assertRaises(PointsFundingPlanError):
                        plan_points_transfer(order.pk, preview_unstarted=True)
                    with override_settings(SERVICE_POINTS_TRANSFER_FUNDING_REVISION_TESTING=True):
                        provisional = plan_points_transfer(order.pk, preview_unstarted=True)
                        self.assertTrue(provisional.preview_only)
                        self.assertEqual(provisional.segments.persons, ())
                        self.assertEqual((provisional.funding.provider_income_units,
                                          provisional.funding.additional_store_funding_units), (4440, 440))
                    self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
                replacement_client = APIClient(HTTP_HOST='xa.esports.club',
                    HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(replacement_account)[0])
                started = replacement_client.post(f'/api/orders/orders/{order.pk}/participation-start/',
                    {'segment_id': incoming.pk, 'entry_image': screenshot()}, format='multipart')
                self.assertEqual(started.data['code'], 0, started.data)
                with transaction.atomic():
                    result = plan_points_transfer(order.pk)
                    self.assertEqual([(row.basis, row.income) for row in result.segments.segments],
                                     [(2400, 1920), (3600, 2520)])
                    self.assertEqual((result.funding.deal_units, result.funding.customer_coin_units,
                        result.funding.provider_income_units, result.funding.additional_store_funding_units),
                        (6000, 4000, 4440, 440))
                    self.assertEqual((result.funding.inviter_income_units, result.funding.store_income_units), (0, 0))
                with transaction.atomic():
                    allocation.remainder_numerator += 1
                    allocation.save(update_fields=['remainder_numerator'])
                    with self.assertRaises(PointsFundingPlanError):
                        plan_points_transfer(order.pk)
                    transaction.set_rollback(True)
                with override_settings(SERVICE_POINTS_TRANSFER_CAPTURE_TESTING=False), transaction.atomic():
                    with self.assertRaises(PointsFundingPlanError):
                        plan_points_transfer(order.pk)
                # Returning to the original person creates another participation
                # segment with that person's newly applicable commission.
                service.commission_rate = 25
                service.save(update_fields=['commission_rate'])
                returned_body = {'reason': '双方同意转回', 'transfers': [{
                    'old_provider_id': replacement_account.pk, 'new_provider_id': old.pk,
                    'split_type': 'PERCENT', 'split_value': 0, 'penalty_amount': 0,
                    'original_share': {'retained': 2000, 'transferred': 4000, 'agreement': '双方确认转回'}}]}
                returned = admin.post(url, returned_body, format='json')
                self.assertEqual(returned.data['code'], 0, returned.data)
                returned_segment = OrderParticipationSegment.objects.get(attribution__order=order,
                    provider_account=old, released_at__isnull=True)
                with transaction.atomic(), self.assertRaises(PointsFundingPlanError):
                    plan_points_transfer(order.pk)
                started = old_client.post(f'/api/orders/orders/{order.pk}/participation-start/',
                    {'segment_id': returned_segment.pk, 'entry_image': screenshot()}, format='multipart')
                self.assertEqual(started.data['code'], 0, started.data)
                with transaction.atomic():
                    again = plan_points_transfer(order.pk)
                    self.assertEqual([(row.basis, row.income) for row in again.segments.segments],
                                     [(2400, 1920), (1200, 840), (2400, 1800)])
                    self.assertEqual((again.funding.provider_income_units, again.funding.additional_store_funding_units),
                                     (4560, 560))
                    own = next(row for row in again.segments.persons if row.account_id == old.pk)
                    self.assertEqual(len(own.segment_ids), 2)
                    from orders.points_transfer_history import plan_historic_points_transfer
                    with self.assertRaises(PointsFundingPlanError):
                        plan_historic_points_transfer(order.pk, through_transfer_id=0)
                    events = list(OrderOriginalShareTransfer.objects.filter(
                        previous_segment__attribution__order=order).order_by('pk'))
                    with override_settings(SERVICE_POINTS_TRANSFER_FUNDING_REVISION_TESTING=True):
                        initial = plan_historic_points_transfer(order.pk, through_transfer_id=0)
                        first = plan_historic_points_transfer(order.pk, through_transfer_id=events[0].pk)
                        second = plan_historic_points_transfer(order.pk, through_transfer_id=events[1].pk)
                        self.assertEqual([(item.funding.provider_income_units, item.funding.additional_store_funding_units)
                                          for item in (initial, first, second)], [(4800, 800), (4440, 440), (4560, 560)])
                        self.assertEqual([row[2] for row in first.segment_shares], [2400, 3600])
                        self.assertEqual([row[2] for row in second.segment_shares], [2400, 1200, 2400])
                        with self.assertRaises(PointsFundingPlanError):
                            plan_historic_points_transfer(order.pk, through_transfer_id=events[-1].pk + 1)
                with transaction.atomic(), override_settings(SERVICE_POINTS_TRANSFER_FUNDING_REVISION_TESTING=True):
                    from orders.points_transfer_history import plan_historic_points_transfer
                    later_basis = OrderSegmentTransferBasis.objects.get(transfer=events[-1])
                    later_basis.remainder_numerator += 1
                    later_basis.save(update_fields=['remainder_numerator'])
                    with self.assertRaises(PointsFundingPlanError):
                        plan_historic_points_transfer(order.pk, through_transfer_id=events[0].pk)
                    transaction.set_rollback(True)
                self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
            with store_context(self.first):
                self.assertFalse(OrderOriginalShareTransfer.objects.exists())
            # Platform finance verifies the actual shared receipt, then
            # allocates part of it through the real multi-database endpoint.
            self.assertEqual(self.verify().status_code, 200)
            allocated = self.fund()
            self.assertEqual(allocated.status_code, 200, allocated.data)
            replay = self.fund()
            self.assertEqual(replay.status_code, 200, replay.data)
            self.assertEqual(replay.data, allocated.data)
            from uuid import uuid4
            second_allocation = self.fund({'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 200})
            self.assertEqual(second_allocation.status_code, 200, second_allocation.data)
            with store_context(self.second), transaction.atomic():
                from orders.points_funding_evidence import verify_order_funding
                from orders.points_transfer_funding import preview_points_transfer_rebalance
                verified = verify_order_funding(order.pk)
                self.assertEqual((verified.required, verified.allocated, verified.remaining, verified.fully_funded),
                                 (560, 500, 60, False))
                with self.assertRaises(PointsFundingPlanError):
                    preview_points_transfer_rebalance(order.pk)
                with override_settings(SERVICE_POINTS_TRANSFER_FUNDING_REVISION_TESTING=True):
                    proposal = preview_points_transfer_rebalance(order.pk)
                    self.assertEqual((proposal.allocation.allocated_before, proposal.allocation.required,
                                      proposal.allocation.remaining, proposal.allocation.release_parts),
                                     (500, 560, 60, ()))
                    self.assertTrue(proposal.funding.preview_only)
                denied = admin.post(url, body, format='json')
                self.assertNotEqual(denied.data.get('code', 400), 0, denied.data)
                self.assertEqual(OrderOriginalShareTransfer.objects.filter(
                    previous_segment__attribution__order=order).count(), 2)
            with store_context(self.second), transaction.atomic():
                from orders.models import OrderPointsFundingAction
                action = OrderPointsFundingAction.objects.order_by('pk').first()
                action.result = {}
                action.save(update_fields=['result'])
                with self.assertRaises(PointsFundingPlanError):
                    verify_order_funding(order.pk)
                transaction.set_rollback(True)

    @override_settings(SERVICE_POINTS_TRANSFER_SETTLEMENT_TESTING=True, SERVICE_POINTS_CONSUMPTION_TESTING=True)
    def test_full_funding_precedes_independent_transferred_provider_settlement(self):
        from datetime import timedelta
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from uuid import uuid4
        from django.utils import timezone
        from orders.models import OrderSegmentPersonSettlement, OrderSegmentSettlementManifest
        from orders.services import settle_order
        with TemporaryDirectory(prefix='xa-points-transfer-settle-') as media, override_settings(MEDIA_ROOT=media):
            with store_context(self.second):
                order = Order.objects.get(pk=self.order.pk)
                old = OrderProvider.objects.get(order=order, released_at__isnull=True).provider_account
                old_client = APIClient(HTTP_HOST='xa.esports.club',
                    HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(old)[0])
                self.assertEqual(old_client.post(f'/api/orders/orders/{order.pk}/start/',
                    {'entry_image': screenshot()}, format='multipart').data['code'], 0)
                replacement = make_provider(deposit_mode='EARNINGS')
                replacement_account = get_or_create_account_for_legacy_user(replacement)
                replacement.escort_profile.account = replacement_account
                replacement.escort_profile.save(update_fields=['account'])
                player = APIClient(HTTP_HOST='xa.esports.club',
                    HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(replacement_account)[0])
                staff_user = make_console_user(['order:view', 'order:dispatch', 'report:audit'])
                staff = get_or_create_account_for_legacy_user(staff_user)
                staff_user.admin_membership.account = staff
                staff_user.admin_membership.save(update_fields=['account'])
                admin = APIClient(HTTP_HOST='admin.esports.club',
                    HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
                service = order.service
                service.commission_override = True; service.commission_rate = 30
                service.save(update_fields=['commission_override', 'commission_rate'])
                transferred = admin.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason': '双方同意',
                    'transfers': [{'old_provider_id': old.pk, 'new_provider_id': replacement_account.pk,
                        'split_type': 'PERCENT', 'split_value': 0, 'penalty_amount': 0,
                        'original_share': {'retained': 4000, 'transferred': 6000, 'agreement': '双方同意'}}]}, format='json')
                self.assertEqual(transferred.data['code'], 0, transferred.data)
                old_segment = OrderParticipationSegment.objects.get(attribution__order=order, provider_account=old)
                new_segment = OrderParticipationSegment.objects.get(attribution__order=order, provider_account=replacement_account)
                evidence = old_client.post(f'/api/orders/orders/{order.pk}/participation-evidence/',
                    {'segment_id': old_segment.pk, 'completion_image': screenshot()}, format='multipart')
                self.assertEqual(evidence.data['code'], 0, evidence.data)
                started = player.post(f'/api/orders/orders/{order.pk}/participation-start/',
                    {'segment_id': new_segment.pk, 'entry_image': screenshot()}, format='multipart')
                self.assertEqual(started.data['code'], 0, started.data)
                completed = player.post(f'/api/orders/orders/{order.pk}/complete/',
                    {'completion_image': screenshot()}, format='multipart')
                self.assertEqual(completed.data['code'], 0, completed.data)
                def report(client, segment):
                    url = f'/api/orders/orders/{order.pk}/segment-report/'
                    for action, extra in [('open', {}), ('upload', {'image': screenshot()}), ('submit', {'version': 0})]:
                        payload = {'segment_id': segment.pk, 'action': action, 'client_request_id': str(uuid4()), **extra}
                        response = client.post(url, payload, format='multipart' if action == 'upload' else 'json')
                        self.assertEqual(response.data['code'], 0, response.data)
                    reviewed = admin.post(f'/api/admin/orders/{order.pk}/segments/{segment.pk}/report-review/',
                        {'action': 'approve', 'version': 1}, format='json')
                    self.assertEqual(reviewed.data['code'], 0, reviewed.data)
                report(old_client, old_segment)
                future = timezone.now() + timedelta(hours=100)
                def settle():
                    with patch('django.utils.timezone.now', return_value=future), transaction.atomic():
                        return settle_order(Order.objects.select_for_update().get(pk=order.pk))
                self.assertEqual(settle(), [])
                self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
            self.assertEqual(self.verify().status_code, 200)
            self.assertEqual(self.fund({'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 440}).status_code, 200)
            with store_context(self.second):
                from engagement.confirmation import process_order
                from engagement.models import ConsumptionConfirmation, PointLedger
                self.assertEqual(settle(), [order.providers.get(provider_account=old).provider_id])
                manifest = OrderSegmentSettlementManifest.objects.get(order=order)
                self.assertEqual(manifest.plan['points_funding']['calculation']['additional_store_funding_units'], 440)
                self.assertFalse(manifest.finalized_at)
                self.assertEqual(OrderSegmentPersonSettlement.objects.filter(manifest=manifest).count(), 1)
                self.assertEqual(settle(), [])
                with transaction.atomic():
                    from orders.models import OrderPointsFundingAction
                    from orders.settlement import SettlementError
                    action = OrderPointsFundingAction.objects.get()
                    action.result = {}; action.save(update_fields=['result'])
                    with self.assertRaises(SettlementError):
                        settle()
                    transaction.set_rollback(True)
                self.assertEqual(OrderSegmentPersonSettlement.objects.filter(manifest=manifest).count(), 1)
                with patch('django.utils.timezone.now', return_value=future):
                    self.assertFalse(process_order(order.pk))
                self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                report(player, new_segment)
                with patch('orders.segment_settlement._finalize', side_effect=RuntimeError('injected failure')):
                    with self.assertRaisesMessage(RuntimeError, 'injected failure'):
                        settle()
                self.assertEqual(OrderSegmentPersonSettlement.objects.filter(manifest=manifest).count(), 1)
                self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 1)
                self.assertEqual(settle(), [replacement.pk])
                order.refresh_from_db(); manifest.refresh_from_db()
                self.assertIsNotNone(order.settled_at)
                self.assertEqual((order.provider_income, order.inviter_commission, order.shop_income), (4440, 0, 0))
                self.assertEqual(order.settlement_store_funding, 440)
                from django.db import IntegrityError
                with self.assertRaises(IntegrityError), transaction.atomic():
                    Order.objects.filter(pk=order.pk).update(funding_terms=None)
                self.assertEqual(OrderSegmentPersonSettlement.objects.filter(manifest=manifest).count(), 2)
                self.assertEqual(settle(), [])
                with transaction.atomic():
                    from orders.models import OrderPointsBenefitSnapshot
                    from rest_framework.exceptions import ValidationError
                    benefit = OrderPointsBenefitSnapshot.objects.get(redemption__order=order)
                    benefit.terms = {**benefit.terms, 'points_rate': benefit.terms['points_rate'] + 1}
                    benefit.save(update_fields=['terms'])
                    with patch('django.utils.timezone.now', return_value=future), self.assertRaises(ValidationError):
                        process_order(order.pk)
                    self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                    transaction.set_rollback(True)
                with patch('django.utils.timezone.now', return_value=future):
                    self.assertTrue(process_order(order.pk))
                    self.assertFalse(process_order(order.pk))
                confirmation = ConsumptionConfirmation.objects.get(order=order)
                self.assertEqual(confirmation.amount, 4000)
                expected = 4000 * order.funding_terms['points_rate'] * order.funding_terms['birthday_multiplier'] // 100
                self.assertEqual(PointLedger.objects.get(order=order, kind='CONSUMPTION').amount, expected)
