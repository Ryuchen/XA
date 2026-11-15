from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from console.test_dispatch_capacity import _CapacityTestBase
from orders.models import Order, OrderProvider, OrderRoom, OrderRoomCompletionRequest, OrderRoomConfirmation
from orders.tests.factories import make_order
from orders.tests.test_room_objections import RoomObjectionTests
from wallet.models import SystemConfig


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=False, ROOM_COMPLETION_REVIEW_ENABLED=True,
                   REQUIRE_ORDER_EVIDENCE_IMAGES=False)
class RoomTransferConfirmationTest(_CapacityTestBase):
    def setUp(self):
        super().setUp()
        self.service.room_enabled = True
        self.service.save(update_fields=['room_enabled'])
        self.old, self.old_account, _ = self._new_escort('原陪玩')
        self.new, self.new_account, _ = self._new_escort('接替陪玩')
        self.client = RoomObjectionTests.staff_client.__get__(self)(['order:view', 'order:dispatch'])
        self.staff_client = RoomObjectionTests.staff_client.__get__(self)(['order:view', 'support:edit'])
        self.order = make_order(self.boss, service=self.service, provider=self.old,
            customer_account=self.boss_account, provider_account=self.old_account,
            status=Order.Status.IN_SERVICE, payment_status=Order.PaymentStatus.PAID,
            settlement_mode=Order.SettlementMode.REPORT_APPROVAL,
            amount=10000, commission_rate=20)
        OrderProvider.objects.create(order=self.order, provider=self.old, provider_account=self.old_account,
            settlement_base=10000, provider_income=8000, commission_rate=20)
        self.room = OrderRoom.objects.create(order=self.order)
        self.application = OrderRoomCompletionRequest.objects.create(room=self.room, submitted_by=self.old_account,
            request_id='application', description='已完成', participants=[self.old_account.pk], status='VERIFIED',
            reviewed_by=self.old_account, reviewed_at=timezone.now(), review_note='已核实', review_request_id='review')
        self.wait = OrderRoomConfirmation.objects.create(request=self.application, config_minutes=30, config_version=1,
            started_at=timezone.now(), deadline=timezone.now()+timedelta(minutes=30))
        SystemConfig.objects.update_or_create(key='room_confirmation_deadline',
            defaults={'value': '{"minutes":20,"version":2}'})

    def transfer(self):
        return self.client.post(f'/api/admin/orders/{self.order.pk}/transfer/', {'transfers': [{
            'old_provider_id': self.old_account.pk, 'new_provider_id': self.new_account.pk,
            'split_type': 'PERCENT', 'split_value': 0, 'penalty_amount': 0}]}, format='json')

    def test_successful_transfer_invalidates_old_confirmation_but_preserves_room(self):
        response = self.transfer()
        self.assertEqual(response.data['code'], 0, response.data)
        self.wait.refresh_from_db()
        self.assertEqual((self.wait.status, self.wait.invalidation_reason), ('INVALIDATED', 'TRANSFER'))
        self.assertIsNotNone(self.wait.invalidated_by_id)
        self.assertIsNone(self.wait.deadline)
        self.assertTrue(OrderRoom.objects.filter(pk=self.room.pk, order=self.order).exists())
        self.order.refresh_from_db()
        self.assertEqual(self.order.provider_account_id, self.new_account.pk)
        path = f'/api/orders/orders/{self.order.pk}/room/completion-requests/'
        new_client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(self.new_account)[0])
        old_client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(self.old_account)[0])
        self.start_replacement(new_client)
        application = {'description': '接替后已完成', 'client_request_id': 'replacement'}
        self.assertEqual(old_client.post(path, application, format='json').status_code, 404)
        result = new_client.post(path, application, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        new_pk = result.data['data']['id']
        review = self.staff_client.post(f'/api/admin/room-completion-requests/{new_pk}/review/',
            {'decision': 'VERIFIED', 'note': '重新核实', 'client_request_id': 'replacement-review'}, format='json')
        self.assertEqual(review.status_code, 200, review.data)
        self.assertEqual(OrderRoomConfirmation.objects.get(request_id=new_pk).config_minutes, 20)

    def start_replacement(self, client):
        from orders.models import OrderWorkStint
        stint = OrderWorkStint.objects.get(order=self.order, account=self.new_account, released_at__isnull=True)
        result = client.post(f'/api/orders/orders/{self.order.pk}/participation-start/',
            {'work_stint_id': stint.pk, 'client_request_id': 'replacement-start'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)

    def test_transfer_invalidates_pending_application_and_new_provider_can_resubmit(self):
        self.wait.delete()
        self.application.status = 'PENDING'
        self.application.reviewed_by = self.application.reviewed_at = None
        self.application.review_note = self.application.review_request_id = ''
        self.application.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_note', 'review_request_id'])
        self.assertEqual(self.transfer().data['code'], 0)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, 'REJECTED')
        self.assertIn('自动失效', self.application.review_note)
        self.assertTrue(OrderRoom.objects.filter(pk=self.room.pk, order=self.order).exists())
        denied = self.staff_client.post(f'/api/admin/room-completion-requests/{self.application.pk}/review/',
            {'decision': 'VERIFIED', 'note': '旧申请', 'client_request_id': 'old-review'}, format='json')
        self.assertEqual(denied.status_code, 409, denied.data)
        client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(self.new_account)[0])
        self.start_replacement(client)
        result = client.post(f'/api/orders/orders/{self.order.pk}/room/completion-requests/',
            {'description': '接替后已完成', 'client_request_id': 'new-application'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        reviewed = self.staff_client.post(f"/api/admin/room-completion-requests/{result.data['data']['id']}/review/",
            {'decision': 'VERIFIED', 'note': '重新核实', 'client_request_id': 'new-review'}, format='json')
        self.assertEqual(reviewed.status_code, 200, reviewed.data)
        self.assertEqual(OrderRoomConfirmation.objects.get(request_id=result.data['data']['id']).config_minutes, 20)

    def test_pending_application_restored_if_transfer_fails(self):
        self.wait.delete()
        self.application.status = 'PENDING'
        self.application.reviewed_by = self.application.reviewed_at = None
        self.application.review_note = self.application.review_request_id = ''
        self.application.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_note', 'review_request_id'])
        with patch('console.views.log_only', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                self.transfer()
        self.application.refresh_from_db()
        self.assertEqual((self.application.status, self.application.reviewed_by_id), ('PENDING', None))

    def test_failed_transfer_rolls_back_confirmation_and_assignment(self):
        with patch('orders.room_completion_review.invalidate_for_transfer_locked', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                self.transfer()
        self.wait.refresh_from_db(); self.order.refresh_from_db()
        self.assertEqual(self.wait.status, 'RUNNING')
        self.assertEqual(self.order.provider_account_id, self.old_account.pk)
        self.assertFalse(self.order.providers.filter(provider_account=self.new_account).exists())

    def test_legacy_settlement_room_cannot_transfer_and_pay_old_provider_early(self):
        Order.objects.filter(pk=self.order.pk).update(settlement_mode=Order.SettlementMode.COMPLETION)
        result = self.transfer()
        self.assertEqual(result.data['code'], 400, result.data)
        self.wait.refresh_from_db(); self.order.refresh_from_db()
        self.assertEqual(self.wait.status, 'RUNNING')
        self.assertEqual(self.order.provider_account_id, self.old_account.pk)
        from wallet.models import Transaction
        self.assertFalse(Transaction.objects.filter(order=self.order).exists())

    def test_paused_objection_can_be_resolved_after_transfer_without_resuming_old_wait(self):
        boss = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(self.boss_account)[0])
        result = boss.post(f'/api/orders/orders/{self.order.pk}/room/objections/',
                           {'reason': '需要核对', 'client_request_id': 'objection'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        objection_id = result.data['data']['id']
        self.assertEqual(self.transfer().data['code'], 0)
        path = f'/api/admin/room-objections/{objection_id}/resolve/'
        action = {'resolution': '已转单', 'client_request_id': 'resolve', 'confirmation_ready': True}
        self.assertEqual(self.staff_client.post(path, action, format='json').status_code, 409)
        action['confirmation_ready'] = False
        self.assertEqual(self.staff_client.post(path, action, format='json').status_code, 200)
        self.wait.refresh_from_db()
        self.assertEqual((self.wait.status, self.wait.invalidation_reason), ('INVALIDATED', 'TRANSFER'))
