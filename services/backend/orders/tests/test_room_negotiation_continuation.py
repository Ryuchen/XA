"""Room completion after a verified scoped negotiation with one member continuing."""
from django.test import override_settings
from django.utils import timezone
from datetime import timedelta
from unittest.mock import patch
from rest_framework.test import APITestCase

from orders.models import Order, OrderRoom, OrderRoomConfirmation, OrderRoomCompletionRequest, OrderRoomObjection
from orders.tests.test_negotiation_termination import NegotiationTerminationTests
from orders.tests import test_negotiation_followup as followup_fixtures
from orders.tests.test_provider_aftersale_status import image
from wallet.models import SystemConfig, Transaction


@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, REFUND_DEPOSIT_DEFICIT_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   ROOM_COMPLETION_REVIEW_ENABLED=True, ROOM_BOSS_CONFIRMATION_ENABLED=True,
                   ORIGINAL_ATTRIBUTION_ENABLED=False)
class RoomNegotiationContinuationTest(APITestCase):
    account = NegotiationTerminationTests.account
    client_for = NegotiationTerminationTests.client_for
    case = NegotiationTerminationTests.case
    propose = NegotiationTerminationTests.propose
    accepted = NegotiationTerminationTests.accepted
    execute = NegotiationTerminationTests.execute
    followup_case = followup_fixtures.FollowupPreviewTests.followup_case
    accepted_followup = followup_fixtures.FollowupPreviewTests.accepted_followup
    test_room_followup_real_apis = followup_fixtures.FollowupPreviewTests.test_real_followup_apis_execute_and_recover_both_receipts

    def setUp(self):
        NegotiationTerminationTests.setUp(self)
        Order.objects.filter(pk=self.order.pk).update(room_required_snapshot=True, required_provider_count=2,
                                                       requires_boss_selection=True)
        self.order.refresh_from_db()
        OrderRoom.objects.create(order=self.order)
        SystemConfig.objects.update_or_create(key='room_confirmation_deadline',
            defaults={'value': '{"minutes":30,"version":1}'})

    def open_room_objection(self, *, with_wait=True):
        wait = None
        if with_wait:
            room = OrderRoom.objects.get(order=self.order)
            request = OrderRoomCompletionRequest.objects.create(room=room, submitted_by=self.a_account,
                request_id='disputed-completion', description='等待老板确认',
                participants=sorted([self.a_account.pk, self.b_account.pk]), status='VERIFIED',
                reviewed_by=self.staff, reviewed_at=timezone.now(), review_note='已核实', review_request_id='disputed-review')
            wait = OrderRoomConfirmation.objects.create(request=request, config_minutes=30, config_version=1,
                started_at=timezone.now(), deadline=timezone.now()+timedelta(minutes=30))
        response = self.client_for(self.boss_account).post(f'/api/orders/orders/{self.order.pk}/room/objections/',
            {'reason':'对房间结果有异议', 'client_request_id':'room-dispute'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        return OrderRoomObjection.objects.get(pk=response.data['data']['id']), wait

    def test_full_negotiated_refund_closes_and_links_room_objection(self):
        from console.models import AdminAuditLog
        objection, wait = self.open_room_objection()
        case, proposal = self.accepted(scope=[self.a_account.pk, self.b_account.pk], amount=1000)
        receipt = self.execute(case, proposal)
        objection.refresh_from_db(); wait.refresh_from_db(); self.order.refresh_from_db()
        self.assertEqual((objection.status, objection.resolution_proposal_id, objection.resolved_by_id),
                         ('RESOLVED', proposal.pk, self.operator_account.pk))
        self.assertFalse(objection.confirmation_ready)
        self.assertEqual((wait.status, wait.invalidation_reason), ('INVALIDATED', 'NEGOTIATED_TERMINATION'))
        self.assertEqual(self.order.status, 'COMPLETED')
        self.assertEqual(self.execute(case, proposal).pk, receipt.pk)
        audit = AdminAuditLog.objects.get(resource='room-objection', object_id=str(objection.pk))
        self.assertEqual((audit.request_body['case_id'], audit.request_body['proposal_id']), (case.pk, proposal.pk))
        response = self.client_for(self.boss_account).get(f'/api/orders/orders/{self.order.pk}/room/objections/')
        self.assertEqual(response.data['data']['list'][0]['resolution_proposal_id'], proposal.pk)

    @override_settings(NEGOTIATION_ZERO_EXECUTION_ENABLED=True)
    def test_zero_negotiated_termination_also_closes_room_objection(self):
        from orders.negotiation_zero import terminate_zero_internal
        objection, _ = self.open_room_objection(with_wait=False)
        case, proposal = self.accepted(scope=[self.a_account.pk, self.b_account.pk], amount=0)
        terminate_zero_internal(case.pk, proposal.pk, operator=self.operator_account,
                                legacy_operator=self.operator, request_id='room-zero-dispute')
        objection.refresh_from_db(); self.order.refresh_from_db()
        self.assertEqual((objection.status, objection.resolution_proposal_id, self.order.status),
                         ('RESOLVED', proposal.pk, 'COMPLETED'))
        self.assertIsNone(objection.confirmation_ready)

    def test_refund_audit_failure_restores_room_objection_and_confirmation(self):
        from console.models import AdminAuditLog
        objection, wait = self.open_room_objection()
        case, proposal = self.accepted(scope=[self.a_account.pk, self.b_account.pk], amount=1000)
        original = AdminAuditLog.objects.create
        def fail_refund_audit(*args, **kwargs):
            if kwargs.get('resource') == 'aftersales':
                raise RuntimeError('模拟退款审计失败')
            return original(*args, **kwargs)
        with patch('console.models.AdminAuditLog.objects.create', side_effect=fail_refund_audit):
            with self.assertRaisesMessage(RuntimeError, '模拟退款审计失败'):
                self.execute(case, proposal)
        objection.refresh_from_db(); wait.refresh_from_db(); self.order.refresh_from_db()
        self.assertEqual((objection.status, objection.resolution_proposal_id, wait.status, self.order.status),
                         ('OPEN', None, 'PAUSED', 'IN_SERVICE'))
        self.assertFalse(self.order.refund_records.exists())
        self.assertFalse(AdminAuditLog.objects.filter(resource='room-objection', object_id=str(objection.pk)).exists())

    def test_partial_negotiated_refund_keeps_room_objection_open(self):
        objection, wait = self.open_room_objection()
        case, proposal = self.accepted(amount=1000)
        self.execute(case, proposal)
        objection.refresh_from_db(); wait.refresh_from_db(); self.order.refresh_from_db()
        self.assertEqual((objection.status, objection.resolution_proposal_id, self.order.status),
                         ('OPEN', None, 'IN_SERVICE'))
        self.assertEqual((wait.status, wait.invalidation_reason), ('INVALIDATED', 'NEGOTIATED_TERMINATION'))

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True, NEGOTIATION_ZERO_EXECUTION_ENABLED=True,
                       NEGOTIATION_REFUND_EXECUTION_ENABLED=True)
    def test_full_followup_also_closes_and_links_room_objection(self):
        from orders.negotiation_followup_accounting import terminate_followup_internal
        case, _, proposal = self.accepted_followup()
        objection, _ = self.open_room_objection(with_wait=False)
        link = terminate_followup_internal(case.pk, proposal.pk, operator=self.operator_account,
                                          legacy_operator=self.operator, request_id='room-followup-objection')
        objection.refresh_from_db(); self.order.refresh_from_db()
        self.assertEqual((objection.status, objection.resolution_proposal_id, self.order.status),
                         ('RESOLVED', proposal.pk, 'COMPLETED'))
        self.assertEqual(link.execution.proposal_id, objection.resolution_proposal_id)

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True, NEGOTIATION_ZERO_EXECUTION_ENABLED=True,
                       NEGOTIATION_REFUND_EXECUTION_ENABLED=True)
    def test_both_players_terminate_room_through_linked_negotiations(self):
        from orders.negotiation_followup_accounting import terminate_followup_internal, verify_followup_termination
        case, previous, proposal = self.accepted_followup()
        link = terminate_followup_internal(case.pk, proposal.pk, operator=self.operator_account,
                                          legacy_operator=self.operator, request_id='room-followup')
        retry = terminate_followup_internal(case.pk, proposal.pk, operator=self.operator_account,
                                           legacy_operator=self.operator, request_id='room-followup')
        self.assertEqual(link.pk, retry.pk)
        verify_followup_termination(link)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'COMPLETED')
        self.assertEqual(self.order.completed_at, proposal.accepted_at)
        self.assertEqual(self.order.refund_records.count(), 2)
        self.assertEqual(self.order.providers.get(provider_account=self.a_account).released_at, previous.accepted_at)
        self.assertEqual(self.order.providers.get(provider_account=self.b_account).released_at, proposal.accepted_at)
        self.assertFalse(OrderRoomConfirmation.objects.exists())

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True, NEGOTIATION_ZERO_EXECUTION_ENABLED=True,
                       NEGOTIATION_REFUND_EXECUTION_ENABLED=True)
    def test_linked_room_negotiations_settle_only_after_each_report_freeze(self):
        from orders.negotiation_followup_accounting import terminate_followup_internal
        from orders.services import settle_order
        from orders.tests import test_negotiation_settlement as settlement_fixture
        from wallet.services import get_wallet
        case, _, proposal = self.accepted_followup()
        terminate_followup_internal(case.pk, proposal.pk, operator=self.operator_account,
                                    legacy_operator=self.operator, request_id='settle-room-followup')
        self.order.refresh_from_db()
        start = self.order.completed_at
        settlement_fixture.NegotiationSettlementTest.report(self, self.a_account, start)
        settlement_fixture.NegotiationSettlementTest.report(self, self.b_account, start + timedelta(hours=2))
        a_wallet = get_wallet(user=self.a, account=self.a_account)
        b_wallet = get_wallet(user=self.b, account=self.b_account)
        with patch('django.utils.timezone.now', return_value=start + timedelta(hours=73)):
            self.assertEqual(settle_order(self.order), [self.a.pk])
            self.assertEqual(settle_order(self.order), [])
        a_wallet.refresh_from_db(); b_wallet.refresh_from_db()
        self.assertEqual((a_wallet.balance, b_wallet.balance), (3000, 0))
        with patch('django.utils.timezone.now', return_value=start + timedelta(hours=75)):
            self.assertEqual(settle_order(self.order), [self.b.pk])
            self.assertEqual(settle_order(self.order), [])
        b_wallet.refresh_from_db()
        self.assertEqual(b_wallet.balance, 3000)
        self.assertEqual(self.order.transactions.filter(tx_type='SHOP_INCOME').count(), 1)

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True, NEGOTIATION_ZERO_EXECUTION_ENABLED=True,
                       NEGOTIATION_REFUND_EXECUTION_ENABLED=True)
    def test_zero_followup_invalidates_pending_confirmation(self):
        from orders.negotiation_followup_accounting import terminate_followup_internal, verify_followup_termination
        case, _, proposal = self.accepted_followup(first_refund=0, second_refund=0)
        room = OrderRoom.objects.get(order=self.order)
        request = OrderRoomCompletionRequest.objects.create(room=room, submitted_by=self.b_account,
            request_id='before-followup', description='已完成', participants=[self.b_account.pk], status='VERIFIED',
            reviewed_by=self.staff, reviewed_at=timezone.now(), review_note='已核实', review_request_id='review-followup')
        wait = OrderRoomConfirmation.objects.create(request=request, config_minutes=30, config_version=1,
            started_at=timezone.now(), deadline=timezone.now()+timedelta(minutes=30))
        link = terminate_followup_internal(case.pk, proposal.pk, operator=self.operator_account,
                                          legacy_operator=self.operator, request_id='room-zero-followup')
        verify_followup_termination(link)
        wait.refresh_from_db(); self.order.refresh_from_db()
        self.assertEqual((wait.status, wait.invalidation_reason), ('INVALIDATED', 'NEGOTIATED_TERMINATION'))
        self.assertEqual(self.order.status, 'COMPLETED')
        self.assertFalse(self.order.refund_records.exists())

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True, NEGOTIATION_ZERO_EXECUTION_ENABLED=True,
                       NEGOTIATION_REFUND_EXECUTION_ENABLED=True)
    def test_followup_guard_failure_rolls_back_second_refund_and_room_invalidation(self):
        from orders.negotiation_followup_accounting import terminate_followup_internal
        from orders.state_machine import IllegalTransitionError
        from wallet.services import get_wallet
        case, _, proposal = self.accepted_followup()
        room = OrderRoom.objects.get(order=self.order)
        request = OrderRoomCompletionRequest.objects.create(room=room, submitted_by=self.b_account,
            request_id='rollback-followup', description='已完成', participants=[self.b_account.pk], status='VERIFIED',
            reviewed_by=self.staff, reviewed_at=timezone.now(), review_note='已核实', review_request_id='review-rollback')
        wait = OrderRoomConfirmation.objects.create(request=request, config_minutes=30, config_version=1,
            started_at=timezone.now(), deadline=timezone.now()+timedelta(minutes=30))
        wallet = get_wallet(user=self.boss, account=self.boss_account)
        before = wallet.balance
        with patch('orders.room_negotiated_guard.verified_room_followup_termination', return_value=False):
            with self.assertRaises(IllegalTransitionError):
                terminate_followup_internal(case.pk, proposal.pk, operator=self.operator_account,
                    legacy_operator=self.operator, request_id='rollback-followup')
        wait.refresh_from_db(); self.order.refresh_from_db(); wallet.refresh_from_db()
        self.assertEqual((wait.status, self.order.status, wallet.balance), ('RUNNING', 'IN_SERVICE', before))
        self.assertEqual(self.order.refund_records.count(), 1)
        self.assertEqual(self.order.providers.filter(released_at__isnull=True).count(), 1)

    def test_unaffected_provider_and_boss_finish_after_verified_scoped_refund(self):
        case, proposal = self.accepted()
        self.execute(case, proposal)
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status, self.order.provider_account_id),
                         ('IN_SERVICE', 'PARTIALLY_REFUNDED', self.b_account.pk))
        player = self.client_for(self.b_account)
        result = player.post(f'/api/orders/orders/{self.order.pk}/complete/', {'completion_image': image()}, format='multipart')
        self.assertEqual(result.data['code'], 0, result.data)
        request_path = f'/api/orders/orders/{self.order.pk}/room/completion-requests/'
        request = player.post(request_path, {'description': '剩余服务已完成', 'client_request_id': 'continuation'}, format='json')
        self.assertEqual(request.status_code, 200, request.data)
        self.assertEqual(request.data['data']['participants'], [self.b_account.pk])
        pk = request.data['data']['id']
        review = self.client_for(self.staff).post(f'/api/admin/room-completion-requests/{pk}/review/',
            {'decision': 'VERIFIED', 'note': '核实完成', 'client_request_id': 'review-continuation'}, format='json')
        self.assertEqual(review.status_code, 200, review.data)
        before = Transaction.objects.filter(order=self.order).count()
        result = self.client_for(self.boss_account).post(f'{request_path}{pk}/confirm/',
            {'client_request_id': 'confirm-continuation'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'COMPLETED')
        self.assertIsNone(self.order.settled_at)
        self.assertEqual(Transaction.objects.filter(order=self.order).count(), before)
        self.assertEqual(OrderRoomConfirmation.objects.get(request_id=pk).status, 'CONFIRMED')
        from site_messages.models import Message
        notices = Message.objects.filter(related_order_id=self.order.pk, title='房间订单已完成')
        self.assertEqual(list(notices.values_list('recipient_account_id', flat=True)), [self.b_account.pk])

    def test_unverifiable_termination_cannot_be_used_to_confirm_room(self):
        case, proposal = self.accepted()
        receipt = self.execute(case, proposal)
        player = self.client_for(self.b_account)
        result = player.post(f'/api/orders/orders/{self.order.pk}/complete/', {'completion_image': image()}, format='multipart')
        self.assertEqual(result.data['code'], 0, result.data)
        receipt.termination_processed_at = None
        receipt.save(update_fields=['termination_processed_at'])
        result = player.post(f'/api/orders/orders/{self.order.pk}/room/completion-requests/',
            {'description': '剩余服务已完成', 'client_request_id': 'unverified'}, format='json')
        self.assertEqual(result.status_code, 409, result.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'IN_SERVICE')
        self.assertFalse(OrderRoomConfirmation.objects.exists())

    def test_unaffected_provider_can_finish_after_full_scoped_refund(self):
        from wallet.services import get_wallet
        wallet = get_wallet(user=self.a, account=self.a_account)
        wallet.balance = 6000; wallet.save(update_fields=['balance'])
        case, proposal = self.accepted(amount=10000)
        self.execute(case, proposal)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, 'REFUNDED')
        player = self.client_for(self.b_account)
        result = player.post(f'/api/orders/orders/{self.order.pk}/complete/', {'completion_image': image()}, format='multipart')
        self.assertEqual(result.data['code'], 0, result.data)
        path = f'/api/orders/orders/{self.order.pk}/room/completion-requests/'
        result = player.post(path, {'description': '剩余服务已完成', 'client_request_id': 'fully-refunded'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        pk = result.data['data']['id']
        result = self.client_for(self.staff).post(f'/api/admin/room-completion-requests/{pk}/review/',
            {'decision': 'VERIFIED', 'note': '核实完成', 'client_request_id': 'review-full'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        before = Transaction.objects.filter(order=self.order).count()
        result = self.client_for(self.boss_account).post(f'{path}{pk}/confirm/',
            {'client_request_id': 'confirm-full'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'COMPLETED')
        self.assertIsNone(self.order.settled_at)
        self.assertEqual(Transaction.objects.filter(order=self.order).count(), before)

    def test_scoped_termination_invalidates_old_room_confirmation_atomically(self):
        room = OrderRoom.objects.get(order=self.order)
        request = OrderRoomCompletionRequest.objects.create(room=room, submitted_by=self.a_account,
            request_id='before-negotiation', description='已完成', participants=sorted([self.a_account.pk, self.b_account.pk]),
            status='VERIFIED', reviewed_by=self.staff, reviewed_at=timezone.now(), review_note='已核实', review_request_id='review')
        wait = OrderRoomConfirmation.objects.create(request=request, config_minutes=30, config_version=1,
            started_at=timezone.now(), deadline=timezone.now()+timedelta(minutes=30))
        case, proposal = self.accepted()
        self.execute(case, proposal)
        wait.refresh_from_db()
        self.assertEqual((wait.status, wait.invalidation_reason), ('INVALIDATED', 'NEGOTIATED_TERMINATION'))
        player = self.client_for(self.b_account)
        result = player.post(f'/api/orders/orders/{self.order.pk}/complete/', {'completion_image': image()}, format='multipart')
        self.assertEqual(result.data['code'], 0, result.data)
        result = player.post(f'/api/orders/orders/{self.order.pk}/room/completion-requests/',
            {'description': '剩余服务已完成', 'client_request_id': 'after-negotiation'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)

    @override_settings(ROOM_AUTO_CONFIRMATION_ENABLED=True)
    def test_unaffected_provider_can_be_auto_completed_after_scoped_refund(self):
        from orders.room_auto_confirmation import process_one
        case, proposal = self.accepted()
        self.execute(case, proposal)
        player = self.client_for(self.b_account)
        result = player.post(f'/api/orders/orders/{self.order.pk}/complete/', {'completion_image': image()}, format='multipart')
        self.assertEqual(result.data['code'], 0, result.data)
        path = f'/api/orders/orders/{self.order.pk}/room/completion-requests/'
        result = player.post(path, {'description': '剩余服务已完成', 'client_request_id': 'auto-negotiated'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        pk = result.data['data']['id']
        result = self.client_for(self.staff).post(f'/api/admin/room-completion-requests/{pk}/review/',
            {'decision': 'VERIFIED', 'note': '核实完成', 'client_request_id': 'review-auto'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        wait = OrderRoomConfirmation.objects.get(request_id=pk)
        OrderRoomConfirmation.objects.filter(pk=wait.pk).update(deadline=timezone.now()-timedelta(seconds=1))
        before = Transaction.objects.filter(order=self.order).count()
        self.assertEqual(process_one(wait.pk), 'confirmed')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'COMPLETED')
        self.assertIsNone(self.order.settled_at)
        self.assertEqual(Transaction.objects.filter(order=self.order).count(), before)

    def test_all_participants_negotiated_termination_finishes_room_without_second_boss_confirmation(self):
        case, proposal = self.accepted(scope=[self.a_account.pk, self.b_account.pk], amount=1000)
        receipt = self.execute(case, proposal)
        self.order.refresh_from_db()
        self.assertTrue(receipt.completed_by_negotiation)
        self.assertEqual((self.order.status, self.order.completed_at), ('COMPLETED', proposal.accepted_at))
        self.assertIsNone(self.order.settled_at)
        self.assertEqual(self.order.refund_records.count(), 1)

    def test_all_participants_termination_invalidates_pending_room_confirmation(self):
        room = OrderRoom.objects.get(order=self.order)
        request = OrderRoomCompletionRequest.objects.create(room=room, submitted_by=self.a_account,
            request_id='old-confirmation', description='已完成', participants=sorted([self.a_account.pk, self.b_account.pk]),
            status='VERIFIED', reviewed_by=self.staff, reviewed_at=timezone.now(), review_note='已核实', review_request_id='review')
        wait = OrderRoomConfirmation.objects.create(request=request, config_minutes=30, config_version=1,
            started_at=timezone.now(), deadline=timezone.now()+timedelta(minutes=30))
        case, proposal = self.accepted(scope=[self.a_account.pk, self.b_account.pk], amount=1000)
        self.execute(case, proposal)
        wait.refresh_from_db(); self.order.refresh_from_db()
        self.assertEqual((wait.status, wait.invalidation_reason), ('INVALIDATED', 'NEGOTIATED_TERMINATION'))
        self.assertEqual(self.order.status, 'COMPLETED')

    def test_fake_negotiation_receipt_cannot_bypass_room_confirmation(self):
        from orders.state_machine import transition, IllegalTransitionError
        with self.assertRaises(IllegalTransitionError):
            transition(self.order.pk, Order.Status.COMPLETED, operator=self.operator,
                operator_account=self.operator_account, action='COMPLETE', room_negotiation_execution_id=999)
        with self.assertRaises(IllegalTransitionError):
            transition(self.order.pk, Order.Status.COMPLETED, operator=self.operator,
                operator_account=self.operator_account, action='COMPLETE', room_points_negotiation_posting_id=999)
        with self.assertRaises(IllegalTransitionError):
            transition(self.order.pk, Order.Status.COMPLETED, operator=self.operator,
                operator_account=self.operator_account, action='COMPLETE', room_negotiation_followup_id=999)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'IN_SERVICE')

    def test_failed_room_guard_rolls_back_refund_and_confirmation_invalidation(self):
        from orders.state_machine import IllegalTransitionError
        from wallet.services import get_wallet
        room = OrderRoom.objects.get(order=self.order)
        request = OrderRoomCompletionRequest.objects.create(room=room, submitted_by=self.a_account,
            request_id='rollback-room', description='已完成', participants=sorted([self.a_account.pk, self.b_account.pk]),
            status='VERIFIED', reviewed_by=self.staff, reviewed_at=timezone.now(), review_note='已核实', review_request_id='review')
        wait = OrderRoomConfirmation.objects.create(request=request, config_minutes=30, config_version=1,
            started_at=timezone.now(), deadline=timezone.now()+timedelta(minutes=30))
        case, proposal = self.accepted(scope=[self.a_account.pk, self.b_account.pk], amount=1000)
        wallet = get_wallet(user=self.boss, account=self.boss_account)
        balance_before = wallet.balance
        with patch('orders.room_negotiated_guard.verified_room_termination', return_value=False):
            with self.assertRaises(IllegalTransitionError):
                self.execute(case, proposal)
        wait.refresh_from_db(); self.order.refresh_from_db(); wallet.refresh_from_db()
        self.assertEqual((wait.status, self.order.status, wallet.balance), ('RUNNING', 'IN_SERVICE', balance_before))
        self.assertFalse(self.order.refund_records.exists())
        self.assertEqual(self.order.providers.filter(released_at__isnull=True).count(), 2)

    @override_settings(NEGOTIATION_ZERO_EXECUTION_ENABLED=True)
    def test_zero_refund_agreed_termination_finishes_room_without_extra_confirmation(self):
        from orders.negotiation_zero import terminate_zero_internal
        case, proposal = self.accepted(scope=[self.a_account.pk, self.b_account.pk], amount=0)
        before = Transaction.objects.filter(order=self.order).count()
        receipt = terminate_zero_internal(case.pk, proposal.pk, operator=self.operator_account,
                                          legacy_operator=self.operator, request_id='room-zero-termination')
        self.order.refresh_from_db()
        self.assertTrue(receipt.completed_by_negotiation)
        self.assertEqual((self.order.status, self.order.completed_at), ('COMPLETED', proposal.accepted_at))
        self.assertIsNone(self.order.settled_at)
        self.assertEqual(Transaction.objects.filter(order=self.order).count(), before)
        self.assertFalse(self.order.refund_records.exists())

    @override_settings(NEGOTIATION_ZERO_EXECUTION_ENABLED=True)
    def test_zero_refund_scoped_termination_leaves_other_player_to_finish_room(self):
        from orders.negotiation_zero import terminate_zero_internal
        case, proposal = self.accepted(scope=[self.a_account.pk], amount=0)
        receipt = terminate_zero_internal(case.pk, proposal.pk, operator=self.operator_account,
                                          legacy_operator=self.operator, request_id='room-zero-scoped')
        self.assertFalse(receipt.completed_by_negotiation)
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.provider_account_id), ('IN_SERVICE', self.b_account.pk))
        player = self.client_for(self.b_account)
        result = player.post(f'/api/orders/orders/{self.order.pk}/complete/', {'completion_image': image()}, format='multipart')
        self.assertEqual(result.data['code'], 0, result.data)
        path = f'/api/orders/orders/{self.order.pk}/room/completion-requests/'
        result = player.post(path, {'description': '剩余服务已完成', 'client_request_id': 'zero-continuing'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        pk = result.data['data']['id']
        result = self.client_for(self.staff).post(f'/api/admin/room-completion-requests/{pk}/review/',
            {'decision': 'VERIFIED', 'note': '核实完成', 'client_request_id': 'zero-continuing-review'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        result = self.client_for(self.boss_account).post(f'{path}{pk}/confirm/',
            {'client_request_id': 'zero-continuing-confirm'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'COMPLETED')
        self.assertIsNone(self.order.settled_at)
