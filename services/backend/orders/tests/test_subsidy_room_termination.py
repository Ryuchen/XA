"""A confirmed zero-payment safety-box agreement also finishes its room."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import Order, OrderRoom, OrderRoomCompletionRequest, OrderRoomConfirmation, OrderRoomObjection
from orders.negotiation_proposals import decide
from orders.tests.factories import make_console_user
from orders.tests.test_subsidy_agreement import SubsidyAgreementFixture
from wallet.models import Transaction


@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_NEGOTIATION_ENABLED=True,
                   NEGOTIATION_PROPOSAL_ENABLED=True, ROOM_COMPLETION_REVIEW_ENABLED=True,
                   ROOM_BOSS_CONFIRMATION_ENABLED=True, ORIGINAL_ATTRIBUTION_ENABLED=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class SubsidyRoomTerminationTest(SubsidyAgreementFixture, TestCase):
    def setUp(self):
        super().setUp()
        Order.objects.filter(pk=self.order.pk).update(room_required_snapshot=True)
        self.order.refresh_from_db()
        self.room = OrderRoom.objects.create(order=self.order)
        self.operator = make_console_user(['order:refund', 'order:view', 'support:edit'])
        self.operator_account = get_or_create_account_for_legacy_user(self.operator)
        self.operator.admin_membership.account = self.operator_account
        self.operator.admin_membership.save(update_fields=['account'])

    def accepted(self):
        proposal = self.propose()
        decide(self.case.pk, actor=self.customer, proposal_id=proposal.pk, accepted=True)
        decide(self.case.pk, actor=self.accounts[1], proposal_id=proposal.pk, accepted=True)
        proposal.refresh_from_db()
        return proposal

    def objection(self):
        from coupons.test_coupon_availability_workflow import CouponAvailabilityWorkflowTest
        req = OrderRoomCompletionRequest.objects.create(room=self.room, submitted_by=self.accounts[1],
            request_id='room-done', description='待核实', participants=[self.accounts[1].pk], status='VERIFIED',
            reviewed_by=self.staff, reviewed_at=timezone.now(), review_note='通过', review_request_id='review')
        wait = OrderRoomConfirmation.objects.create(request=req, config_minutes=30, config_version=1,
            started_at=timezone.now(), deadline=timezone.now()+timedelta(minutes=30))
        client = CouponAvailabilityWorkflowTest.client_for(self.customer)
        response = client.post(f'/api/orders/orders/{self.order.pk}/room/objections/',
            {'reason':'房间结果有异议', 'client_request_id':'dispute'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        return OrderRoomObjection.objects.get(pk=response.data['data']['id']), wait

    def execute(self, proposal):
        from orders.subsidy_execution import execute
        return execute(self.case.pk, proposal.pk, operator=self.operator_account,
                       legacy_operator=self.operator, request_id='subsidy-room')

    def test_both_confirmed_termination_closes_objection_and_settles_after_clocks(self):
        from console.models import AdminAuditLog
        objection, wait = self.objection()
        proposal = self.accepted()
        receipt = self.execute(proposal)
        self.assertEqual(self.execute(proposal).pk, receipt.pk)
        objection.refresh_from_db(); wait.refresh_from_db(); self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.completed_at), ('COMPLETED', proposal.accepted_at))
        self.assertEqual((objection.status, objection.resolution_proposal_id), ('RESOLVED', proposal.pk))
        self.assertEqual((wait.status, wait.invalidation_reason), ('INVALIDATED', 'NEGOTIATED_TERMINATION'))
        self.assertFalse(objection.confirmation_ready)
        self.assertTrue(AdminAuditLog.objects.filter(resource='room-objection', object_id=str(objection.pk)).exists())
        self.assertEqual(AdminAuditLog.objects.get(resource='room-objection', object_id=str(objection.pk)).path,
                         f'/api/admin/aftersales/{self.case.pk}/subsidy-negotiation-execution/')
        self.assertFalse(self.order.refund_records.exists())
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').exists())
        self.settle(self.completed+timedelta(hours=83))
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.settled_at)
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').count(), 2)

    def test_failed_room_audit_rolls_back_agreement_and_paused_timer(self):
        from console.models import AdminAuditLog
        from orders.models import SubsidyAgreementExecution
        objection, wait = self.objection()
        proposal = self.accepted()
        original = AdminAuditLog.objects.create
        def fail_room_audit(*args, **kwargs):
            if kwargs.get('resource') == 'room-objection':
                raise RuntimeError('房间审计失败')
            return original(*args, **kwargs)
        with patch('console.models.AdminAuditLog.objects.create', side_effect=fail_room_audit):
            with self.assertRaisesMessage(RuntimeError, '房间审计失败'):
                self.execute(proposal)
        self.order.refresh_from_db(); self.case.refresh_from_db(); objection.refresh_from_db(); wait.refresh_from_db()
        self.shares[1].refresh_from_db()
        self.assertEqual((self.order.status, self.case.status, objection.status, wait.status, self.shares[1].amount),
                         ('IN_SERVICE', 'OPEN', 'OPEN', 'PAUSED', 4800))
        self.assertFalse(SubsidyAgreementExecution.objects.filter(order=self.order).exists())

    def test_missing_room_never_executes(self):
        self.room.delete()
        with self.assertRaises(ValidationError):
            self.accepted()
        self.assertFalse(self.order.refund_records.exists())

    def test_room_evidence_tampering_blocks_subsidy_settlement(self):
        from console.models import AdminAuditLog
        from orders.settlement import SettlementError
        objection, _ = self.objection()
        self.execute(self.accepted())
        AdminAuditLog.objects.filter(resource='room-objection', object_id=str(objection.pk)).delete()
        with self.assertRaisesMessage(SettlementError, '协商凭证待核对'):
            self.settle(self.completed+timedelta(hours=83))
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').exists())

    def test_removing_objection_confirmation_link_blocks_settlement(self):
        from orders.settlement import SettlementError
        objection, _ = self.objection()
        self.execute(self.accepted())
        OrderRoomObjection.objects.filter(pk=objection.pk).update(
            confirmation=None, remaining_microseconds=None, confirmation_ready=None)
        with self.assertRaisesMessage(SettlementError, '协商凭证待核对'):
            self.settle(self.completed+timedelta(hours=83))
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').exists())

    def test_room_cannot_complete_using_unknown_subsidy_receipt(self):
        from orders.state_machine import IllegalTransitionError, transition
        with self.assertRaises(IllegalTransitionError):
            transition(self.order.pk, Order.Status.COMPLETED, operator=self.operator,
                operator_account=self.operator_account, action='COMPLETE', room_subsidy_agreement_id=99999)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'IN_SERVICE')

    def test_three_roles_execute_room_case_via_real_endpoints(self):
        from coupons.test_coupon_availability_workflow import CouponAvailabilityWorkflowTest
        objection, _ = self.objection()
        staff = CouponAvailabilityWorkflowTest.client_for(self.operator_account)
        boss = CouponAvailabilityWorkflowTest.client_for(self.customer)
        player = CouponAvailabilityWorkflowTest.client_for(self.accounts[1])
        base = f'/api/admin/aftersales/{self.case.pk}'
        proposal = staff.post(base+'/negotiation-proposal/', {'request_id':'room-proposal', 'expected_version':0,
            'refund_units':0, 'provider_terms':[{'account_id':self.accounts[1].pk,'retained_units':2000}],
            'explanation':'核定已完成服务报酬'}, format='json')
        self.assertEqual(proposal.data['code'], 0, proposal.data)
        proposal_id = proposal.data['data']['id']
        for client in (boss, player):
            result = client.post(f'/api/orders/orders/{self.order.pk}/negotiation-proposal/',
                                 {'proposal_id':proposal_id, 'accepted':True}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
        path = base+'/subsidy-negotiation-execution/'
        payload = {'proposal_id':proposal_id, 'client_request_id':'room-api-execution'}
        result = staff.post(path, payload, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        again = staff.get(path, payload)
        self.assertEqual(again.data['data']['execution_id'], result.data['data']['execution_id'])
        self.order.refresh_from_db(); objection.refresh_from_db()
        self.assertEqual((self.order.status, objection.status, objection.resolution_proposal_id),
                         ('COMPLETED', 'RESOLVED', proposal_id))


@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_NEGOTIATION_ENABLED=True,
                   NEGOTIATION_PROPOSAL_ENABLED=True, ROOM_COMPLETION_REVIEW_ENABLED=True,
                   ROOM_BOSS_CONFIRMATION_ENABLED=True, ORIGINAL_ATTRIBUTION_ENABLED=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
@skipUnlessDBFeature('has_select_for_update')
class SubsidyRoomTerminationRaceTest(SubsidyAgreementFixture, TransactionTestCase):
    accepted = SubsidyRoomTerminationTest.accepted
    objection = SubsidyRoomTerminationTest.objection
    execute = SubsidyRoomTerminationTest.execute

    def setUp(self):
        super().setUp()
        Order.objects.filter(pk=self.order.pk).update(room_required_snapshot=True)
        self.order.refresh_from_db()
        self.room = OrderRoom.objects.create(order=self.order)
        self.operator = make_console_user(['order:refund', 'order:view', 'support:edit'])
        self.operator_account = get_or_create_account_for_legacy_user(self.operator)
        self.operator.admin_membership.account = self.operator_account
        self.operator.admin_membership.save(update_fields=['account'])

    def test_concurrent_retry_closes_one_objection_once(self):
        from console.models import AdminAuditLog
        from orders.models import SubsidyAgreementExecution
        objection, wait = self.objection()
        proposal = self.accepted()
        gate = Barrier(2)
        def run(_):
            close_old_connections()
            try:
                gate.wait(timeout=10)
                return self.execute(proposal).pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts = list(pool.map(run, (0, 1)))
        objection.refresh_from_db(); wait.refresh_from_db()
        self.assertEqual(receipts[0], receipts[1])
        self.assertEqual(SubsidyAgreementExecution.objects.filter(order=self.order).count(), 1)
        self.assertEqual(AdminAuditLog.objects.filter(resource='room-objection', object_id=str(objection.pk)).count(), 1)
        self.assertEqual((objection.status, wait.status), ('RESOLVED', 'INVALIDATED'))
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').exists())


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_NEGOTIATION_ENABLED=True,
                   NEGOTIATION_PROPOSAL_ENABLED=True, ROOM_COMPLETION_REVIEW_ENABLED=True,
                   ROOM_BOSS_CONFIRMATION_ENABLED=True, ORIGINAL_ATTRIBUTION_ENABLED=False,
                   LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class SubsidyRoomTerminationStoresTest(SubsidyAgreementFixture, TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def setUp(self):
        pass

    def test_same_room_and_order_ids_only_close_authenticated_store(self):
        from rest_framework.test import APIClient
        from club_accounts.tokens import issue_account_tokens
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        entries = []
        for store in (resolve_store(), xinan_store()):
            with store_context(store):
                SubsidyAgreementFixture.setUp(self)
                Order.objects.filter(pk=self.order.pk).update(room_required_snapshot=True)
                room = OrderRoom.objects.create(order=self.order)
                objection = OrderRoomObjection.objects.create(room=room, customer=self.customer,
                    request_id='store-dispute', reason='结果有异议')
                operator = make_console_user(['order:view', 'order:refund', 'support:edit'])
                account = get_or_create_account_for_legacy_user(operator)
                operator.admin_membership.account = account; operator.admin_membership.save(update_fields=['account'])
                proposal = self.propose()
                decide(self.case.pk, actor=self.customer, proposal_id=proposal.pk, accepted=True)
                decide(self.case.pk, actor=self.accounts[1], proposal_id=proposal.pk, accepted=True)
                entries.append((store, self.order.pk, self.case.pk, proposal.pk, objection.pk,
                                issue_account_tokens(account)[0]))
        first, second = entries
        self.assertEqual(first[1:5], second[1:5])
        client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+first[5])
        result = client.post(f'/api/admin/aftersales/{first[2]}/subsidy-negotiation-execution/',
                             {'proposal_id':first[3], 'client_request_id':'room-tenant'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        with store_context(first[0]):
            self.assertEqual(Order.objects.get(pk=first[1]).status, 'COMPLETED')
            self.assertEqual(OrderRoomObjection.objects.get(pk=first[4]).status, 'RESOLVED')
        with store_context(second[0]):
            self.assertEqual(Order.objects.get(pk=second[1]).status, 'IN_SERVICE')
            self.assertEqual(OrderRoomObjection.objects.get(pk=second[4]).status, 'OPEN')
