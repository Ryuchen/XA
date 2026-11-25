import json
from unittest.mock import patch

from django.test import override_settings
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.tokens import issue_account_tokens
from orders.models import (Order, OrderParticipationSegment, OrderRoom, OrderRoomCompletionRequest,
                           OrderRoomConfirmation)
from orders.tests import test_prestart_handoff as handoff_fixture
from orders.tests import test_release_flow as flow
from orders.tests.test_room_objections import RoomObjectionTests
from wallet.models import SystemConfig, Transaction


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   SEGMENT_STANDARD_TRANSFER_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
                   ROOM_COMPLETION_REVIEW_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, REQUIRE_RECHARGE_PROOF=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class RoomTransferConfirmationTest(APITransactionTestCase):
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    replacement = handoff_fixture.PrestartHandoffTest.replacement

    def setUp(self):
        handoff_fixture.PrestartHandoffTest.setUp(self)
        self.service.room_enabled = True
        self.service.save(update_fields=['room_enabled'])
        self.set_confirmation_rule(30, 1)
        self.boss_account = self.accounts[self.boss.pk]
        self.old_account = self.accounts[self.provider.pk]
        replacement = self.replacement()
        self.new_account = self.accounts[replacement.pk]
        self.dispatcher = RoomObjectionTests.staff_client.__get__(self)(['order:view', 'order:dispatch'])
        self.staff_client = RoomObjectionTests.staff_client.__get__(self)(['order:view', 'support:edit'])
        self.order = self.new_order()
        self.room = OrderRoom.objects.get(order=self.order)
        self.order_url = f'/api/orders/orders/{self.order.pk}/'
        self.application_url = self.order_url + 'room/completion-requests/'
        self.login(self.provider)
        self.ok(self.client.post(self.order_url + 'start/',
            {'entry_image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(self.order_url + 'complete/',
            {'completion_image': flow.screenshot()}, format='multipart'))
        application = self.ok(self.client.post(self.application_url,
            {'description': '原履约段已完成', 'client_request_id': 'application'}, format='json'))
        self.application = OrderRoomCompletionRequest.objects.get(pk=application['id'])

    def set_confirmation_rule(self, minutes, version):
        SystemConfig.objects.update_or_create(key='room_confirmation_deadline',
            defaults={'value': json.dumps({'minutes': minutes, 'version': version})})

    def verify_application(self):
        url = f'/api/admin/room-completion-requests/{self.application.pk}/review/'
        self.ok(self.staff_client.post(url,
            {'decision': 'VERIFIED', 'note': '核实原履约段', 'client_request_id': 'review'}, format='json'))
        return OrderRoomConfirmation.objects.get(request=self.application)

    def transfer(self):
        return self.dispatcher.post(f'/api/admin/orders/{self.order.pk}/transfer/', {'transfers': [{
            'old_provider_id': self.old_account.pk, 'new_provider_id': self.new_account.pk,
            'split_type': 'PERCENT', 'split_value': 0, 'penalty_amount': 0,
            'original_share': {'retained': 4000, 'transferred': 6000, 'agreement': '双方确认各自履约段份额'},
        }]}, format='json')

    def test_successful_transfer_invalidates_old_confirmation_but_preserves_room(self):
        wait = self.verify_application()
        incomes = Transaction.objects.filter(order=self.order, tx_type=Transaction.TxType.INCOME,
                                             status=Transaction.Status.SUCCESS)
        self.set_confirmation_rule(20, 2)
        self.service.commission_override = True
        self.service.commission_rate = 30
        self.service.save(update_fields=['commission_override', 'commission_rate'])
        response = self.transfer()
        self.assertEqual(response.data['code'], 0, response.data)
        wait.refresh_from_db()
        self.assertEqual((wait.status, wait.invalidation_reason), ('INVALIDATED', 'TRANSFER'))
        self.assertIsNotNone(wait.invalidated_by_id)
        self.assertIsNone(wait.deadline)
        self.assertTrue(OrderRoom.objects.filter(pk=self.room.pk, order=self.order).exists())
        self.order.refresh_from_db()
        self.assertEqual(self.order.provider_account_id, self.new_account.pk)
        self.assertIsNone(self.order.settled_at)
        self.assertFalse(incomes.exists())
        rates = OrderParticipationSegment.objects.filter(attribution__order=self.order).order_by('pk').values_list(
            'financial_snapshot__commission_rate', flat=True)
        self.assertEqual(list(rates), [20, 30])
        new_client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(self.new_account)[0])
        old_client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(self.old_account)[0])
        self.complete_replacement(new_client)
        application = {'description': '接替后已完成', 'client_request_id': 'replacement'}
        self.assertEqual(old_client.post(self.application_url, application, format='json').status_code, 404)
        result = new_client.post(self.application_url, application, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        new_pk = result.data['data']['id']
        review = self.staff_client.post(f'/api/admin/room-completion-requests/{new_pk}/review/',
            {'decision': 'VERIFIED', 'note': '重新核实', 'client_request_id': 'replacement-review'}, format='json')
        self.assertEqual(review.status_code, 200, review.data)
        self.assertEqual(OrderRoomConfirmation.objects.get(request_id=new_pk).config_minutes, 20)
        self.order.refresh_from_db()
        self.assertIsNone(self.order.settled_at)
        self.assertFalse(incomes.exists())

        self.login(self.boss)
        requests = self.ok(self.client.get(self.application_url))['list']
        by_id = {row['id']: row['confirmation'] for row in requests}
        former_wait = by_id[self.application.pk]
        self.assertEqual((former_wait['status'], former_wait['invalidation_reason']), ('INVALIDATED', 'TRANSFER'))
        self.assertIsNone(former_wait['deadline'])
        current_wait = by_id[new_pk]
        self.assertEqual((current_wait['status'], current_wait['config_minutes'], current_wait['config_version']),
                         ('RUNNING', 20, 2))
        self.assertIsNotNone(current_wait['deadline'])

    def complete_replacement(self, client):
        segment = OrderParticipationSegment.objects.get(attribution__order=self.order,
            provider_account=self.new_account, released_at__isnull=True)
        self.ok(client.post(self.order_url + 'participation-start/',
            {'segment_id': segment.pk, 'entry_image': flow.screenshot(), 'client_request_id': 'replacement-start'},
            format='multipart'))
        self.ok(client.post(self.order_url + 'complete/',
            {'completion_image': flow.screenshot()}, format='multipart'))

    def test_transfer_invalidates_pending_application_and_new_provider_can_resubmit(self):
        self.set_confirmation_rule(20, 2)
        self.assertEqual(self.transfer().data['code'], 0)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, 'REJECTED')
        self.assertIn('自动失效', self.application.review_note)
        self.assertTrue(OrderRoom.objects.filter(pk=self.room.pk, order=self.order).exists())
        denied = self.staff_client.post(f'/api/admin/room-completion-requests/{self.application.pk}/review/',
            {'decision': 'VERIFIED', 'note': '旧申请', 'client_request_id': 'old-review'}, format='json')
        self.assertEqual(denied.status_code, 409, denied.data)
        client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(self.new_account)[0])
        self.complete_replacement(client)
        result = client.post(self.application_url,
            {'description': '接替后已完成', 'client_request_id': 'new-application'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        reviewed = self.staff_client.post(f"/api/admin/room-completion-requests/{result.data['data']['id']}/review/",
            {'decision': 'VERIFIED', 'note': '重新核实', 'client_request_id': 'new-review'}, format='json')
        self.assertEqual(reviewed.status_code, 200, reviewed.data)
        self.assertEqual(OrderRoomConfirmation.objects.get(request_id=result.data['data']['id']).config_minutes, 20)

    def test_pending_application_restored_if_transfer_fails(self):
        with patch('console.views.log_only', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                self.transfer()
        self.application.refresh_from_db()
        self.assertEqual((self.application.status, self.application.reviewed_by_id), ('PENDING', None))

    def test_failed_transfer_rolls_back_confirmation_and_assignment(self):
        wait = self.verify_application()
        with patch('orders.room_completion_review.invalidate_for_transfer_locked', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                self.transfer()
        wait.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(wait.status, 'RUNNING')
        self.assertEqual(self.order.provider_account_id, self.old_account.pk)
        self.assertFalse(self.order.providers.filter(provider_account=self.new_account).exists())

    def test_legacy_settlement_room_cannot_transfer_and_pay_old_provider_early(self):
        wait = self.verify_application()
        ledger = Transaction.objects.filter(order=self.order).order_by('pk').values_list('pk', 'tx_type', 'amount')
        before = list(ledger)
        self.assertEqual([(kind, amount) for _, kind, amount in before], [('PAY', -self.order.amount)])
        Order.objects.filter(pk=self.order.pk).update(settlement_mode=Order.SettlementMode.COMPLETION)
        result = self.transfer()
        self.assertEqual(result.data['code'], 400, result.data)
        wait.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(wait.status, 'RUNNING')
        self.assertEqual(self.order.provider_account_id, self.old_account.pk)
        self.assertEqual(list(ledger), before)

    def test_paused_objection_can_be_resolved_after_transfer_without_resuming_old_wait(self):
        wait = self.verify_application()
        boss = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(self.boss_account)[0])
        result = boss.post(self.order_url + 'room/objections/',
                           {'reason': '需要核对', 'client_request_id': 'objection'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        objection_id = result.data['data']['id']
        self.assertEqual(self.transfer().data['code'], 0)
        path = f'/api/admin/room-objections/{objection_id}/resolve/'
        action = {'resolution': '已转单', 'client_request_id': 'resolve', 'confirmation_ready': True}
        self.assertEqual(self.staff_client.post(path, action, format='json').status_code, 409)
        action['confirmation_ready'] = False
        self.assertEqual(self.staff_client.post(path, action, format='json').status_code, 200)
        wait.refresh_from_db()
        self.assertEqual((wait.status, wait.invalidation_reason), ('INVALIDATED', 'TRANSFER'))
