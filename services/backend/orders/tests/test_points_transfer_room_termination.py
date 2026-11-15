"""Real points-paid room transfer, then a negotiation covering both participants."""
from tempfile import TemporaryDirectory
from unittest import skipUnless
from datetime import timedelta

from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderParticipationSegment, OrderProvider
from orders.tests import test_points_checkout_integration as fixture
from orders.tests.factories import make_console_user, make_provider
from orders.tests.test_release_flow import screenshot
from stores.context import store_context
from stores.db import transaction


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
    SEGMENT_REFUND_PREVIEW_ENABLED=True, SERVICE_POINTS_TRANSFER_CAPTURE_TESTING=True,
    SERVICE_POINTS_TRANSFER_FUNDING_REVISION_TESTING=True, SERVICE_POINTS_TRANSFER_SETTLEMENT_TESTING=True,
    SERVICE_POINTS_TRANSFER_REFUND_PREVIEW_TESTING=True, NEGOTIATION_PROPOSAL_ENABLED=True,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
    CELERY_TASK_ALWAYS_EAGER=False, ROOM_COMPLETION_REVIEW_ENABLED=True, ROOM_BOSS_CONFIRMATION_ENABLED=True,
    SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True, SERVICE_POINTS_NEGOTIATION_TESTING=True,
    SERVICE_POINTS_TRANSFER_REFUND_AGREEMENT_TESTING=True,
    SERVICE_POINTS_TRANSFER_REFUND_INTERNAL_AGREEMENT_TESTING=True,
    SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True, SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True,
    SERVICE_POINTS_TRANSFER_REFUND_POSTING_TESTING=True, SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True,
    SERVICE_POINTS_TRANSFER_REFUND_TERMINATION_TESTING=True, SERVICE_POINTS_FUNDING_RELEASE_TESTING=True,
    SERVICE_POINTS_TRANSFER_REFUND_RELEASE_TESTING=True, SERVICE_POINTS_TRANSFER_REFUND_FUNDING_TESTING=True,
    SERVICE_POINTS_TRANSFER_REFUND_REPORT_TESTING=True, SERVICE_POINTS_TRANSFER_REFUND_SETTLEMENT_TESTING=True,
    SERVICE_POINTS_CONSUMPTION_TESTING=True, SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True,
    SERVICE_POINTS_TRANSFER_REFUND_CONSUMPTION_TESTING=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True)
class PointsTransferRoomTerminationTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    setUp = fixture.PointsFundingPlatformRecoveryTests.setUp
    payload = fixture.PointsFundingPlatformRecoveryTests.payload
    quoted_payload = fixture.PointsFundingPlatformRecoveryTests.quoted_payload

    def create_assigned_points_order(self):
        self.service.room_enabled = True
        self.service.save(update_fields=['room_enabled'])
        return fixture.PointsCheckoutIntegrationTests.create_assigned_points_order(self)

    def test_transferred_and_current_providers_can_confirm_termination_together(self):
        from orders.points_negotiation_termination import execute_and_terminate
        with TemporaryDirectory(prefix='xa-points-transfer-room-') as media, override_settings(MEDIA_ROOT=media):
            with store_context(self.second):
                order = Order.objects.get(pk=self.order.pk)
                previous = OrderProvider.objects.get(order=order, released_at__isnull=True).provider_account
                boss = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(order.customer_account)[0])
                old_player = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(previous)[0])
                response = old_player.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                replacement = make_provider(deposit_mode='EARNINGS')
                new = get_or_create_account_for_legacy_user(replacement)
                replacement.escort_profile.account = new; replacement.escort_profile.save(update_fields=['account'])
                player = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(new)[0])
                staff_user = make_console_user(['order:view', 'order:dispatch', 'order:refund', 'support:edit'])
                staff = get_or_create_account_for_legacy_user(staff_user)
                staff_user.admin_membership.account = staff; staff_user.admin_membership.save(update_fields=['account'])
                admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
                response = admin.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason': '双方同意', 'transfers': [
                    {'old_provider_id': previous.pk, 'new_provider_id': new.pk, 'split_type': 'PERCENT',
                     'split_value': 0, 'penalty_amount': 0, 'original_share': {'retained': 4000,
                         'transferred': 6000, 'agreement': '双方同意'}}]}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                segment = OrderParticipationSegment.objects.get(attribution__order=order, provider_account=new)
                old_segment = OrderParticipationSegment.objects.get(attribution__order=order, provider_account=previous)
                previous_assignment = OrderProvider.objects.get(order=order, provider_account=previous)
                self.assertEqual(previous_assignment.released_at, old_segment.released_at)
                self.assertEqual(old_segment.released_at, old_segment.original_transfer_out.created_at)
                response = player.post(f'/api/orders/orders/{order.pk}/participation-start/',
                    {'segment_id': segment.pk, 'entry_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                request_body = {'order_id': order.pk,
                    'reason': '两位陪玩同意终止退款', 'affected_provider_account_ids': [previous.pk, new.pk],
                    'client_request_id': 'room-transfer-full-case'}
                with override_settings(SERVICE_POINTS_TRANSFER_REFUND_AGREEMENT_TESTING=False):
                    denied = admin.post('/api/admin/aftersales/open-negotiation/', request_body, format='json')
                    self.assertGreaterEqual(denied.status_code, 400)
                with transaction.atomic():
                    OrderParticipationSegment.objects.filter(pk=old_segment.pk).update(original_share=0)
                    denied = admin.post('/api/admin/aftersales/open-negotiation/', request_body, format='json')
                    self.assertGreaterEqual(denied.status_code, 400)
                    transaction.set_rollback(True)
                unrelated = make_provider(deposit_mode='EARNINGS')
                outsider = get_or_create_account_for_legacy_user(unrelated)
                denied = admin.post('/api/admin/aftersales/open-negotiation/',
                    {**request_body, 'affected_provider_account_ids': [previous.pk, new.pk, outsider.pk]}, format='json')
                self.assertGreaterEqual(denied.status_code, 400)
                response = admin.post('/api/admin/aftersales/open-negotiation/', request_body, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                case_id = response.data['data']['id']
                preview = admin.get(f'/api/admin/aftersales/{case_id}/negotiation-preview/?refund_units=1000')
                self.assertEqual(preview.data['code'], 0, preview.data)
                terms = [{'account_id': row['account_id'], 'retained_units': row['retained_units']}
                         for row in preview.data['data']['provider_terms']]
                self.assertEqual({row['account_id'] for row in terms}, {previous.pk, new.pk})
                response = admin.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/',
                    {'request_id': 'room-transfer-full-proposal', 'expected_version': 0, 'refund_units': 1000,
                     'provider_terms': terms, 'explanation': '两位陪玩与老板确认'}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                proposal_id = response.data['data']['id']
                for actor in (boss, old_player, player):
                    response = actor.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
                        {'proposal_id': proposal_id, 'accepted': True}, format='json')
                    self.assertEqual(response.data['code'], 0, response.data)
                from rest_framework.exceptions import ValidationError
                with transaction.atomic():
                    OrderProvider.objects.filter(pk=previous_assignment.pk).update(
                        released_at=previous_assignment.released_at - timedelta(seconds=1))
                    with self.assertRaises(ValidationError):
                        execute_and_terminate(case_id, proposal_id, operator=staff, legacy_operator=staff_user,
                                              request_id='room-transfer-invalid', _transfer_testing=True)
                    self.assertFalse(order.refund_records.exists())
                    self.assertEqual(Order.objects.get(pk=order.pk).status, 'IN_SERVICE')
                    transaction.set_rollback(True)
                posting = execute_and_terminate(case_id, proposal_id, operator=staff, legacy_operator=staff_user,
                                                request_id='room-transfer-full-execute', _transfer_testing=True)
                order.refresh_from_db()
                self.assertEqual((order.status, order.completed_at), ('COMPLETED', posting.accepted_at))
                self.assertEqual(order.refund_records.count(), 1)
                retry = execute_and_terminate(case_id, proposal_id, operator=staff, legacy_operator=staff_user,
                                              request_id='room-transfer-full-execute', _transfer_testing=True)
                self.assertEqual(retry.pk, posting.pk)
