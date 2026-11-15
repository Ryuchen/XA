"""Zero-refund negotiation evidence and feature-gated termination tests."""
from unittest.mock import patch
from unittest import skipUnless

from django.conf import settings
from django.db import IntegrityError
from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase
from stores.db import transaction

from orders.models import NegotiationExecution
from orders.negotiation_zero import apply_zero, verify_zero_execution
from orders.tests import test_negotiation_termination as termination_fixture
from wallet.models import Transaction
from wallet.services import get_wallet


@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, REFUND_DEPOSIT_DEFICIT_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ZeroNegotiationPreparationTests(APITestCase):
    account = termination_fixture.NegotiationTerminationTests.account
    client_for = termination_fixture.NegotiationTerminationTests.client_for
    case = termination_fixture.NegotiationTerminationTests.case
    propose = termination_fixture.NegotiationTerminationTests.propose
    accepted = termination_fixture.NegotiationTerminationTests.accepted
    setUp = termination_fixture.NegotiationTerminationTests.setUp

    def apply(self, case, proposal, request_id='zero-termination-1'):
        with transaction.atomic():
            return apply_zero(case.pk, proposal.pk, operator=self.operator_account,
                              legacy_operator=self.operator, request_id=request_id)

    def test_accepted_zero_terms_create_no_refund_or_money_movement(self):
        case, proposal = self.accepted(amount=0)
        boss = get_wallet(user=self.boss, account=self.boss_account)
        before = (boss.balance, Transaction.objects.count())
        receipt = self.apply(case, proposal)
        self.assertEqual(self.apply(case, proposal).pk, receipt.pk)
        boss.refresh_from_db(); self.order.refresh_from_db(); case.refresh_from_db()
        self.assertEqual((boss.balance, Transaction.objects.count()), before)
        self.assertEqual((self.order.status, self.order.payment_status, case.status), ('IN_SERVICE', 'PAID', 'OPEN'))
        self.assertFalse(self.order.refund_records.exists())
        self.assertIsNone(receipt.refund_id); self.assertIsNone(receipt.customer_transaction_id)
        self.assertIsNone(receipt.termination_processed_at)
        share = receipt.shares.get()
        self.assertEqual((share.original_income, share.retained_income, share.burden), (4000, 4000, 0))

    def test_changed_receipt_or_request_cannot_be_replayed(self):
        case, proposal = self.accepted(amount=0)
        receipt = self.apply(case, proposal)
        with self.assertRaises(ValidationError):
            self.apply(case, proposal, 'different-request')
        receipt.zero_allocation[0]['retained'] -= 1
        receipt.save(update_fields=['zero_allocation'])
        with self.assertRaises(ValidationError), transaction.atomic():
            verify_zero_execution(receipt)
        self.assertFalse(self.order.refund_records.exists())

    def test_zero_operation_key_cannot_be_reused_for_a_later_refund(self):
        from orders.negotiation_execution import apply_refund
        from orders.negotiation_proposals import decide
        case, proposal = self.accepted(amount=0)
        self.apply(case, proposal, 'same-operation-key')
        replacement = self.propose(case, version=proposal.version, request_id='replacement-proposal', refund=1000)
        for actor in (self.boss_account, self.a_account):
            decide(case.pk, actor=actor, proposal_id=replacement.pk, accepted=True)
        with override_settings(NEGOTIATION_REFUND_EXECUTION_ENABLED=True):
            response = self.client_for(self.operator_account).get(
                f'/api/admin/aftersales/{case.pk}/negotiation-execution/',
                {'proposal_id':replacement.pk, 'client_request_id':'same-operation-key'})
        self.assertEqual(response.status_code, 400, response.data)
        with self.assertRaisesMessage(ValidationError, '凭证已用于其他协商处理'):
            apply_refund(case.pk, replacement.pk, operator=self.operator_account,
                         legacy_operator=self.operator, request_id='same-operation-key')
        self.assertFalse(self.order.refund_records.exists())

    @override_settings(NEGOTIATION_REFUND_EXECUTION_ENABLED=True)
    def test_preparation_never_looks_like_a_completed_refund_to_staff_api(self):
        case, proposal = self.accepted(amount=0)
        receipt = self.apply(case, proposal)
        from orders.negotiation_execution import verify_execution
        with self.assertRaisesMessage(ValidationError, '不包含退款'):
            verify_execution(receipt)
        client = self.client_for(self.operator_account)
        url = f'/api/admin/aftersales/{case.pk}/negotiation-execution/'
        payload = {'proposal_id':proposal.pk, 'client_request_id':'zero-termination-1'}
        self.assertEqual(client.get(url, payload).status_code, 400)
        self.assertEqual(client.post(url, payload, format='json').status_code, 400)
        case.refresh_from_db(); self.order.refresh_from_db()
        self.assertEqual((case.status, self.order.status, self.order.payment_status), ('OPEN', 'IN_SERVICE', 'PAID'))
        self.assertFalse(self.order.refund_records.exists())

    def test_share_write_failure_rolls_back_receipt(self):
        case, proposal = self.accepted(amount=0)
        with patch('orders.negotiation_zero.NegotiationExecutionShare.objects.create', side_effect=RuntimeError('write failed')):
            with self.assertRaisesMessage(RuntimeError, 'write failed'):
                self.apply(case, proposal)
        self.assertFalse(NegotiationExecution.objects.exists())
        self.assertFalse(self.order.refund_records.exists())

    def test_preparation_refuses_to_run_outside_the_caller_transaction(self):
        case, proposal = self.accepted(amount=0)
        with patch('orders.negotiation_zero.transaction.get_connection') as connection:
            connection.return_value.in_atomic_block = False
            with self.assertRaisesMessage(RuntimeError, '完整终止事务'):
                apply_zero(case.pk, proposal.pk, operator=self.operator_account,
                           legacy_operator=self.operator, request_id='outside-transaction')
        self.assertFalse(NegotiationExecution.objects.exists())

    def test_changed_order_money_snapshot_is_rejected(self):
        case, proposal = self.accepted(amount=0)
        receipt = self.apply(case, proposal)
        self.order.shop_income -= 10; self.order.save(update_fields=['shop_income'])
        with self.assertRaisesMessage(ValidationError, '履约份额不完整'), transaction.atomic():
            verify_zero_execution(receipt)
        self.assertFalse(self.order.refund_records.exists())

    @override_settings(NEGOTIATION_ZERO_EXECUTION_ENABLED=True)
    def test_zero_settlement_rechecks_original_payment_and_sources(self):
        from datetime import timedelta
        from orders.negotiation_zero import terminate_zero_internal
        from orders.settlement import SettlementError
        from orders.tests import test_negotiation_settlement as settlement_fixture
        case, proposal = self.accepted(amount=0)
        receipt = terminate_zero_internal(case.pk, proposal.pk, operator=self.operator_account,
                                          legacy_operator=self.operator, request_id='zero-payment-check')
        self.order.status = 'COMPLETED'; self.order.completed_at = proposal.accepted_at
        self.order.save(update_fields=['status', 'completed_at'])
        settlement_fixture.NegotiationSettlementTest.report(self, self.a_account, proposal.accepted_at)
        for bad in ('payment', 'funding'):
            with self.subTest(bad=bad), transaction.atomic():
                if bad == 'payment':
                    Transaction.objects.filter(order=self.order, tx_type='PAY').update(amount=-9990)
                else:
                    self.order.funding_allocations.update(refunded_amount=10)
                with self.assertRaisesMessage(SettlementError, '协商终止凭证待核对'):
                    settlement_fixture.NegotiationSettlementTest.settle(
                        self, receipt, proposal.accepted_at + timedelta(hours=73))
                transaction.set_rollback(True)
        self.assertEqual(get_wallet(user=self.a, account=self.a_account).balance, 0)
        self.assertFalse(self.order.refund_records.exists())

    def test_persisted_zero_marker_never_falls_through_to_ordinary_settlement(self):
        from django.utils import timezone
        from orders.models import Order
        from orders.negotiation_settlement import execution_for_order
        from orders.negotiation_zero import MARKER
        from orders.services import settle_order
        from orders.settlement import SettlementError
        case, proposal = self.accepted(amount=0)
        receipt = self.apply(case, proposal)
        stale_order = Order.objects.get(pk=self.order.pk)
        self.order.funding_terms = {**self.order.funding_terms, MARKER:{'execution_id':receipt.pk}}
        self.order.status = 'COMPLETED'; self.order.completed_at = timezone.now()
        self.order.save(update_fields=['funding_terms', 'status', 'completed_at'])
        with transaction.atomic():
            self.assertEqual(execution_for_order(self.order, lock=True).pk, receipt.pk)
            self.assertEqual(execution_for_order(stale_order, lock=True).pk, receipt.pk)
            with self.assertRaisesMessage(SettlementError, '协商终止凭证待核对'):
                settle_order(self.order)
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type__in=['INCOME', 'SHOP_INCOME']).exists())

    def test_malformed_or_missing_zero_marker_receipt_fails_closed(self):
        from orders.negotiation_settlement import execution_for_order
        from orders.negotiation_zero import MARKER
        from orders.settlement import SettlementError
        for value in ({'execution_id': True}, {'execution_id':999999}):
            self.order.funding_terms = {**self.order.funding_terms, MARKER:value}
            self.order.save(update_fields=['funding_terms'])
            with self.assertRaises(SettlementError), transaction.atomic():
                execution_for_order(self.order, lock=True)
        self.assertFalse(self.order.refund_records.exists())

    @override_settings(NEGOTIATION_ZERO_EXECUTION_ENABLED=True)
    def test_legacy_refund_paths_cannot_bypass_zero_termination_on_stale_order(self):
        from orders.models import Order
        from orders.negotiation_proposals import create
        from orders.negotiation_zero import terminate_zero_internal
        from orders.refunds import RefundError, build_refund_plan
        from orders.services import refund_to_customer
        case, proposal = self.accepted(amount=0)
        stale = Order.objects.get(pk=self.order.pk)
        terminate_zero_internal(case.pk, proposal.pk, operator=self.operator_account,
                                legacy_operator=self.operator, request_id='zero-refund-guard')
        boss = get_wallet(user=self.boss, account=self.boss_account); before = boss.balance
        with self.assertRaises(RefundError) as preview:
            build_refund_plan(stale, refund_amount=1000)
        self.assertEqual(preview.exception.code, 'ZERO_NEGOTIATION_REFUND_UNAVAILABLE')
        with self.assertRaises(RefundError), transaction.atomic():
            refund_to_customer(stale, refund_amount=1000, client_request_id='unsafe-legacy-refund')
        case.refresh_from_db(); case.status = 'OPEN'; case.client_request_id = 'later-case'
        case.save(update_fields=['status', 'client_request_id'])
        with self.assertRaisesMessage(ValidationError, '新售后方案尚未接通'):
            create(case.pk, actor=self.staff, request_id='later-proposal', expected_version=proposal.version,
                   refund_units=1000, provider_terms=[{'account_id':self.b_account.pk, 'retained_units':3000}],
                   explanation='新的协商')
        boss.refresh_from_db()
        self.assertEqual(boss.balance, before)
        self.assertFalse(self.order.refund_records.exists())

    @override_settings(NEGOTIATION_ZERO_EXECUTION_ENABLED=True)
    def test_new_case_for_other_provider_keeps_verified_zero_termination(self):
        from datetime import timedelta
        from orders.negotiation_zero import terminate_zero_internal, verify_zero_termination
        from orders.tests import test_negotiation_settlement as settlement_fixture
        from orders.tests.test_provider_aftersale_status import image
        self.order.requires_boss_selection = True
        self.order.save(update_fields=['requires_boss_selection'])
        case, proposal = self.accepted(amount=0)
        receipt = terminate_zero_internal(case.pk, proposal.pk, operator=self.operator_account,
                                          legacy_operator=self.operator, request_id='zero-before-new-case')
        payload = {'order_id':self.order.pk, 'reason':'老板请求处理继续履约的陪玩',
                   'affected_provider_account_ids':[self.b_account.pk], 'client_request_id':'after-zero-new-case'}
        result = self.client_for(self.staff).post('/api/admin/aftersales/open-negotiation/', payload, format='json')
        self.assertEqual(result.data.get('code'), 0, result.data)
        self.assertIn('退款暂未接通', result.data['msg'])
        case.refresh_from_db()
        self.assertEqual((case.status, case.affected_provider_account_ids), ('OPEN', [self.b_account.pk]))
        receipt.refresh_from_db()
        with transaction.atomic():
            verify_zero_execution(receipt)
            verify_zero_termination(receipt)
        completed = self.client_for(self.b_account).post(f'/api/orders/orders/{self.order.pk}/complete/',
            {'completion_image':image()}, format='multipart')
        self.assertEqual(completed.data.get('code'), 0, completed.data)
        self.order.refresh_from_db()
        submitted = self.order.completed_at
        settlement_fixture.NegotiationSettlementTest.report(self, self.a_account, submitted)
        settlement_fixture.NegotiationSettlementTest.report(self, self.b_account, submitted)
        due = submitted + timedelta(hours=73)
        self.assertEqual(settlement_fixture.NegotiationSettlementTest.settle(self, receipt, due), [self.a.pk])
        self.assertEqual(get_wallet(user=self.a, account=self.a_account).balance, 4000)
        self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 0)
        role = self.operator.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'order:settle']; role.save(update_fields=['permissions'])
        resolved = self.client_for(self.operator_account).post(f'/api/admin/aftersales/{case.pk}/resolve/',
            {'resolution':'已核实处理'}, format='json')
        self.assertEqual(resolved.data.get('code'), 0, resolved.data)
        self.assertEqual(settlement_fixture.NegotiationSettlementTest.settle(self, receipt, due), [self.b.pk])
        self.assertEqual(settlement_fixture.NegotiationSettlementTest.settle(self, receipt, due), [])
        self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 4000)
        self.assertFalse(self.order.refund_records.exists())

    @override_settings(NEGOTIATION_ZERO_EXECUTION_ENABLED=True)
    def test_corrupt_zero_termination_cannot_open_new_case(self):
        from orders.negotiation_zero import terminate_zero_internal
        self.order.requires_boss_selection = True
        self.order.save(update_fields=['requires_boss_selection'])
        case, proposal = self.accepted(amount=0)
        terminate_zero_internal(case.pk, proposal.pk, operator=self.operator_account,
                                legacy_operator=self.operator, request_id='zero-bad-new-case')
        case.refresh_from_db()
        case.history[-1]['resolution'] = '已被修改'; case.save(update_fields=['history'])
        result = self.client_for(self.staff).post('/api/admin/aftersales/open-negotiation/', {
            'order_id':self.order.pk, 'reason':'老板请求处理继续履约的陪玩',
            'affected_provider_account_ids':[self.b_account.pk], 'client_request_id':'after-bad-zero'}, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        case.refresh_from_db()
        self.assertEqual(case.status, 'RESOLVED')
        self.assertFalse(self.order.refund_records.exists())

    def test_nonzero_proposal_and_incomplete_money_evidence_are_rejected(self):
        case, proposal = self.accepted(amount=1000)
        with self.assertRaises(ValidationError):
            self.apply(case, proposal)
        with self.assertRaises(IntegrityError), transaction.atomic():
            NegotiationExecution.objects.create(proposal=proposal, operator=self.operator_account,
                zero_order=self.order, zero_request_id='broken', terms_hash=proposal.terms_hash,
                allocation_hash='', accepted_at=proposal.accepted_at,
                customer_transaction=Transaction.objects.get(order=self.order, tx_type='PAY'))
        self.assertFalse(NegotiationExecution.objects.exists())

    @override_settings(NEGOTIATION_ZERO_EXECUTION_ENABLED=True)
    def test_internal_scoped_termination_is_atomic_and_does_not_refund(self):
        from orders.negotiation_zero import MARKER, terminate_zero_internal
        from console.models import AdminAuditLog
        case, proposal = self.accepted(amount=0)
        before = Transaction.objects.count()
        receipt = terminate_zero_internal(case.pk, proposal.pk, operator=self.operator_account,
                                          legacy_operator=self.operator, request_id='zero-scope')
        replay = terminate_zero_internal(case.pk, proposal.pk, operator=self.operator_account,
                                         legacy_operator=self.operator, request_id='zero-scope')
        self.assertEqual(replay.pk, receipt.pk)
        self.order.refresh_from_db(); case.refresh_from_db()
        a = self.order.providers.get(provider_account=self.a_account)
        b = self.order.providers.get(provider_account=self.b_account)
        self.assertEqual((self.order.status, self.order.payment_status, case.status), ('IN_SERVICE', 'PAID', 'RESOLVED'))
        self.assertEqual(a.released_at, proposal.accepted_at); self.assertIsNone(b.released_at)
        self.assertEqual(self.order.provider_account_id, self.b_account.pk)
        self.assertEqual(self.order.funding_terms[MARKER], {'execution_id':receipt.pk})
        self.assertEqual(Transaction.objects.count(), before)
        self.assertFalse(self.order.refund_records.exists())
        self.assertEqual(self.order.status_logs.filter(action='NEGOTIATED_STOP').count(), 1)
        self.assertEqual(AdminAuditLog.objects.filter(path=f'/api/admin/aftersales/{case.pk}/negotiation-execution/').count(), 1)
        from django.utils import timezone
        from orders.grab_original_facts import original_amount_facts
        facts = original_amount_facts([self.a_account.pk, self.b_account.pk], as_of=timezone.now())
        self.assertEqual((facts[self.a_account.pk].amount, facts[self.b_account.pk].amount), (5000, 5000))
        with transaction.atomic():
            receipt.zero_allocation[0]['retained'] -= 1
            receipt.save(update_fields=['zero_allocation'])
            damaged = original_amount_facts([self.a_account.pk], as_of=timezone.now())[self.a_account.pk]
            self.assertIsNone(damaged.amount)
            self.assertIn(self.order.pk, damaged.review_order_ids)
            transaction.set_rollback(True)

    @override_settings(NEGOTIATION_ZERO_EXECUTION_ENABLED=True)
    def test_unaffected_teammate_can_complete_after_zero_termination(self):
        from orders.negotiation_zero import terminate_zero_internal
        from orders.tests.test_provider_aftersale_status import image
        self.order.requires_boss_selection = True; self.order.save(update_fields=['requires_boss_selection'])
        case, proposal = self.accepted(amount=0)
        terminate_zero_internal(case.pk, proposal.pk, operator=self.operator_account,
                                legacy_operator=self.operator, request_id='zero-continue')
        denied = self.client_for(self.a_account).post(f'/api/orders/orders/{self.order.pk}/complete/',
            {'completion_image':image()}, format='multipart')
        self.assertEqual(denied.data['code'], 403)
        remaining = self.client_for(self.b_account)
        detail = remaining.get(f'/api/orders/orders/{self.order.pk}/')
        self.assertEqual(detail.data['code'], 0, detail.data)
        self.assertEqual(detail.data['data']['team_progress']['total_count'], 1)
        self.assertIn('COMPLETE', detail.data['data']['allowed_actions'])
        result = remaining.post(f'/api/orders/orders/{self.order.pk}/complete/',
            {'completion_image':image()}, format='multipart')
        self.assertEqual(result.data['code'], 0, result.data)
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status), ('COMPLETED', 'PAID'))
        self.assertFalse(self.order.refund_records.exists())

    @override_settings(NEGOTIATION_ZERO_EXECUTION_ENABLED=True)
    def test_internal_full_scope_completes_at_agreement_without_money_flow(self):
        from orders.negotiation_zero import terminate_zero_internal
        scope = sorted([self.a_account.pk, self.b_account.pk])
        case, proposal = self.accepted(scope=scope, amount=0)
        before = Transaction.objects.count()
        receipt = terminate_zero_internal(case.pk, proposal.pk, operator=self.operator_account,
                                          legacy_operator=self.operator, request_id='zero-all')
        self.order.refresh_from_db(); case.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status, self.order.completed_at, case.status),
                         ('COMPLETED', 'PAID', proposal.accepted_at, 'RESOLVED'))
        self.assertTrue(receipt.completed_by_negotiation)
        self.assertEqual(self.order.providers.filter(released_at=proposal.accepted_at).count(), 2)
        self.assertEqual(Transaction.objects.count(), before)
        self.assertFalse(self.order.refund_records.exists())

    @override_settings(NEGOTIATION_ZERO_EXECUTION_ENABLED=True)
    def test_internal_zero_settlement_waits_for_whole_order_and_each_report_clock(self):
        from datetime import timedelta
        from orders.negotiation_zero import terminate_zero_internal
        from orders.tests import test_negotiation_settlement as settlement_fixture
        case, proposal = self.accepted(amount=0)
        receipt = terminate_zero_internal(case.pk, proposal.pk, operator=self.operator_account,
                                          legacy_operator=self.operator, request_id='zero-settle')
        settle = lambda when: settlement_fixture.NegotiationSettlementTest.settle(self, receipt, when)
        self.assertEqual(settle(proposal.accepted_at + timedelta(days=10)), [])
        completed = proposal.accepted_at + timedelta(hours=73)
        self.order.status = 'COMPLETED'; self.order.completed_at = completed
        self.order.save(update_fields=['status', 'completed_at'])
        settlement_fixture.NegotiationSettlementTest.report(self, self.a_account, proposal.accepted_at)
        settlement_fixture.NegotiationSettlementTest.report(self, self.b_account, completed)
        self.assertEqual(settle(completed + timedelta(hours=1)), [self.a.pk])
        self.assertEqual(get_wallet(user=self.a, account=self.a_account).balance, 4000)
        self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 0)
        self.assertEqual(settle(completed + timedelta(hours=73)), [self.b.pk])
        self.assertEqual(settle(completed + timedelta(hours=74)), [])
        receipt.refresh_from_db(); self.order.refresh_from_db()
        self.assertEqual(receipt.finalized_at, self.order.settled_at)
        self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 4000)
        self.assertEqual(self.order.transactions.filter(tx_type='SHOP_INCOME').count(), 1)
        self.assertFalse(self.order.refund_records.exists())

    @override_settings(NEGOTIATION_ZERO_EXECUTION_ENABLED=True)
    def test_disabling_new_zero_terminations_does_not_block_existing_earnings(self):
        from datetime import timedelta
        from orders.negotiation_zero import terminate_zero_internal
        from orders.tests import test_negotiation_settlement as settlement_fixture
        case, proposal = self.accepted(amount=0)
        receipt = terminate_zero_internal(case.pk, proposal.pk, operator=self.operator_account,
                                          legacy_operator=self.operator, request_id='zero-then-disable')
        self.order.status = 'COMPLETED'; self.order.completed_at = proposal.accepted_at
        self.order.save(update_fields=['status', 'completed_at'])
        settlement_fixture.NegotiationSettlementTest.report(self, self.a_account, proposal.accepted_at)
        settlement_fixture.NegotiationSettlementTest.report(self, self.b_account, proposal.accepted_at)
        with override_settings(NEGOTIATION_ZERO_EXECUTION_ENABLED=False):
            users = settlement_fixture.NegotiationSettlementTest.settle(
                self, receipt, proposal.accepted_at + timedelta(hours=73))
        self.assertEqual(set(users), {self.a.pk, self.b.pk})
        self.assertEqual(get_wallet(user=self.a, account=self.a_account).balance, 4000)
        self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 4000)

    @override_settings(NEGOTIATION_ZERO_EXECUTION_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True)
    def test_zero_termination_preserves_unaffected_already_paid_share(self):
        from datetime import timedelta
        from orders.tests import test_negotiation_settlement as settlement_fixture
        self.report = lambda account, at: settlement_fixture.NegotiationSettlementTest.report(self, account, at)
        case, proposal, report, paid_at = settlement_fixture.NegotiationSettlementTest.prior_unaffected_payment(self, amount=0)
        with patch('django.utils.timezone.now', return_value=paid_at + timedelta(minutes=1)):
            result = self.client_for(self.operator_account).post(
                f'/api/admin/aftersales/{case.pk}/negotiation-execution/',
                {'proposal_id':proposal.pk, 'client_request_id':'zero-after-other-paid'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        receipt = NegotiationExecution.objects.get(pk=result.data['data']['execution_id'])
        assignment = self.order.providers.get(provider_account=self.b_account)
        self.assertEqual(receipt.unaffected_report_evidence[str(assignment.pk)]['report_id'], report.pk)
        self.assertEqual(settlement_fixture.NegotiationSettlementTest.settle(self, receipt, paid_at + timedelta(minutes=2)), [self.a.pk])
        self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 4000)
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='INCOME', wallet__account=self.b_account).count(), 1)
        self.assertFalse(self.order.refund_records.exists())

    @override_settings(NEGOTIATION_ZERO_EXECUTION_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
    def test_zero_income_recovery_order_closes_its_permit_without_fabricated_credit(self):
        from datetime import timedelta
        from django.utils import timezone
        from orders.negotiation_proposals import create, decide
        from orders.negotiation_zero import terminate_zero_internal
        from orders.tests import test_negotiation_settlement as settlement_fixture
        from orders.tests.factories import make_completed_order
        from wallet.models import RefundDepositRecoveryPermit, get_platform_wallet
        from wallet.test_refund_deposit_deficit import RefundDepositDeficitTest
        current = self.order
        self.account = self.a_account; self.profile = self.a.escort_profile
        self.profile.deposit_mode = 'EARNINGS'; self.profile.deposit_paid = 10000
        self.profile.save(update_fields=['deposit_mode', 'deposit_paid'])
        self.wallet = get_wallet(user=self.a, account=self.a_account)
        self.platform = get_platform_wallet(); self.platform.balance = 10000; self.platform.save(update_fields=['balance'])
        self.order = make_completed_order(self.boss, self.a, amount=10000,
                                          provider_account=self.a_account, payment_status='PAID')
        deficit = RefundDepositDeficitTest.deficit(self, 200)
        self.order = current
        permit = RefundDepositRecoveryPermit.objects.create(profile=self.profile, account=self.a_account, order=current)
        current.providers.filter(provider_account=self.a_account).update(provider_income=0, commission_rate=100)
        current.provider_income = 4000; current.shop_income = 6000
        current.save(update_fields=['provider_income', 'shop_income'])
        case = self.case()
        proposal = create(case.pk, actor=self.staff, request_id='zero-no-income', expected_version=0,
            refund_units=0, provider_terms=[{'account_id':self.a_account.pk, 'retained_units':0}], explanation='双方同意终止')
        for actor in (self.boss_account, self.a_account):
            decide(case.pk, actor=actor, proposal_id=proposal.pk, accepted=True)
        receipt = terminate_zero_internal(case.pk, proposal.pk, operator=self.operator_account,
                                          legacy_operator=self.operator, request_id='zero-no-income-execute')
        completed = timezone.now(); current.status = 'COMPLETED'; current.completed_at = completed
        current.save(update_fields=['status', 'completed_at'])
        settlement_fixture.NegotiationSettlementTest.report(self, self.a_account, completed)
        settlement_fixture.NegotiationSettlementTest.report(self, self.b_account, completed)
        users = settlement_fixture.NegotiationSettlementTest.settle(self, receipt, completed + timedelta(hours=73))
        self.assertEqual(set(users), {self.a.pk, self.b.pk})
        permit.refresh_from_db(); deficit.refresh_from_db()
        self.assertEqual((permit.close_reason, deficit.remaining), ('ZERO_NEGOTIATED_SETTLED', 200))
        self.assertEqual(permit.zero_negotiated_share_id, receipt.shares.get(account=self.a_account).pk)
        self.assertFalse(Transaction.objects.filter(order=current, wallet__account=self.a_account, tx_type='INCOME').exists())

    @override_settings(NEGOTIATION_ZERO_EXECUTION_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True)
    def test_confirmation_worker_and_report_status_follow_each_zero_termination_share(self):
        from datetime import timedelta
        from engagement.confirmation import process_order
        from engagement.models import ConsumptionConfirmation
        from orders.tests import test_negotiation_settlement as settlement_fixture
        from orders.tests.test_provider_aftersale_status import image
        self.order.requires_boss_selection = True; self.order.save(update_fields=['requires_boss_selection'])
        case, proposal = self.accepted(amount=0)
        staff = self.client_for(self.operator_account)
        execution = staff.post(f'/api/admin/aftersales/{case.pk}/negotiation-execution/',
            {'proposal_id':proposal.pk, 'client_request_id':'zero-worker'}, format='json')
        self.assertEqual(execution.status_code, 200, execution.data)
        self.assertIsNone(execution.data['data']['refund_id'])
        response = self.client_for(self.b_account).post(f'/api/orders/orders/{self.order.pk}/complete/',
            {'completion_image':image()}, format='multipart')
        self.assertEqual(response.data['code'], 0, response.data)
        a_report = settlement_fixture.NegotiationSettlementTest.report(self, self.a_account, proposal.accepted_at)
        b_report = settlement_fixture.NegotiationSettlementTest.report(self, self.b_account, proposal.accepted_at + timedelta(hours=3))
        with patch('django.utils.timezone.now', return_value=proposal.accepted_at + timedelta(hours=73)):
            self.assertFalse(process_order(self.order.pk))
        from engagement.models import PointLedger
        self.assertFalse(PointLedger.objects.filter(order=self.order, kind='CONSUMPTION').exists())
        self.assertEqual(get_wallet(user=self.a, account=self.a_account).balance, 4000)
        self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 0)
        for account, report, expected in ((self.a_account, a_report, 'SETTLED'), (self.b_account, b_report, 'WAITING')):
            result = self.client_for(account).get('/api/wallet/reports/')
            self.assertEqual(result.data['code'], 0, result.data)
            own = next(row for row in result.data['data'] if row['id'] == report.pk)
            self.assertEqual(own['settlement_status'], expected)
            self.assertIsNone(own['refund_summary'])
        with patch('django.utils.timezone.now', return_value=proposal.accepted_at + timedelta(hours=76)):
            self.assertTrue(process_order(self.order.pk))
        self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 4000)
        self.assertEqual(ConsumptionConfirmation.objects.get(order=self.order).amount, self.order.amount)
        self.assertEqual(PointLedger.objects.get(order=self.order, kind='CONSUMPTION').amount, self.order.amount)
        self.assertFalse(self.order.refund_records.exists())

    @override_settings(NEGOTIATION_ZERO_EXECUTION_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True)
    def test_staff_zero_execution_retries_and_recovers_without_claiming_refund(self):
        from console.models import AdminAuditLog
        case, proposal = self.accepted(amount=0)
        staff = self.client_for(self.operator_account)
        url = f'/api/admin/aftersales/{case.pk}/negotiation-execution/'
        payload = {'proposal_id':proposal.pk, 'client_request_id':'zero-api-retry'}
        self.assertTrue(staff.get(url).data['data']['zero_execution_enabled'])
        result = staff.post(url, payload, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual((result.data['data']['refund_id'], result.data['data']['refund_units']), (None, 0))
        self.assertEqual(staff.post(url, payload, format='json').data['data']['execution_id'], result.data['data']['execution_id'])
        with override_settings(NEGOTIATION_PROPOSAL_ENABLED=False, NEGOTIATION_REFUND_EXECUTION_ENABLED=False,
                               NEGOTIATION_ZERO_EXECUTION_ENABLED=False):
            recovery = staff.get(url, payload)
            self.assertEqual(recovery.status_code, 200, recovery.data)
            self.assertEqual(recovery.data['data']['execution_id'], result.data['data']['execution_id'])
        self.assertEqual(AdminAuditLog.objects.filter(path=url).count(), 1)
        self.assertFalse(self.order.refund_records.exists())

    @override_settings(NEGOTIATION_ZERO_EXECUTION_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True)
    def test_zero_termination_notices_only_reach_boss_and_affected_provider(self):
        from site_messages.models import Message
        case, proposal = self.accepted(amount=0)
        url = f'/api/admin/aftersales/{case.pk}/negotiation-execution/'
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client_for(self.operator_account).post(url,
                {'proposal_id':proposal.pk, 'client_request_id':'zero-private-notice'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        rows = Message.objects.filter(related_order_id=self.order.pk,
                                      title__in=['售后已处理', '订单售后已处理'])
        self.assertEqual(set(rows.values_list('recipient_account_id', flat=True)), {self.boss_account.pk, self.a_account.pk})
        self.assertFalse(rows.filter(recipient_account=self.b_account).exists())
        self.assertNotIn(case.reason, str(list(rows.values('preview', 'detail'))))

    @override_settings(NEGOTIATION_ZERO_EXECUTION_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True)
    def test_tampered_zero_aftersale_history_blocks_recovery(self):
        case, proposal = self.accepted(amount=0)
        staff = self.client_for(self.operator_account)
        url = f'/api/admin/aftersales/{case.pk}/negotiation-execution/'
        payload = {'proposal_id':proposal.pk, 'client_request_id':'zero-history-check'}
        self.assertEqual(staff.post(url, payload, format='json').status_code, 200)
        case.refresh_from_db()
        case.history[-1]['resolution'] = '被修改的处理结果'; case.save(update_fields=['history'])
        self.assertEqual(staff.get(url, payload).status_code, 400)
        self.assertEqual(staff.post(url, payload, format='json').status_code, 400)
        self.assertFalse(self.order.refund_records.exists())

    @override_settings(NEGOTIATION_ZERO_EXECUTION_ENABLED=True)
    def test_internal_audit_failure_rolls_back_termination_and_receipt(self):
        from orders.negotiation_zero import MARKER, terminate_zero_internal
        case, proposal = self.accepted(amount=0)
        with patch('console.models.AdminAuditLog.objects.create', side_effect=RuntimeError('audit failure')):
            with self.assertRaisesMessage(RuntimeError, 'audit failure'):
                terminate_zero_internal(case.pk, proposal.pk, operator=self.operator_account,
                                        legacy_operator=self.operator, request_id='zero-audit')
        self.order.refresh_from_db(); case.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status, case.status), ('IN_SERVICE', 'PAID', 'OPEN'))
        self.assertNotIn(MARKER, self.order.funding_terms)
        self.assertFalse(NegotiationExecution.objects.exists())
        self.assertFalse(self.order.providers.filter(released_at__isnull=False).exists())

    def test_internal_termination_is_closed_by_default(self):
        from orders.negotiation_zero import terminate_zero_internal
        case, proposal = self.accepted(amount=0)
        with self.assertRaisesMessage(ValidationError, '尚未开放'):
            terminate_zero_internal(case.pk, proposal.pk, operator=self.operator_account,
                                    legacy_operator=self.operator, request_id='zero-closed')
        self.assertFalse(NegotiationExecution.objects.exists())

    @override_settings(NEGOTIATION_REFUND_EXECUTION_ENABLED=True, NEGOTIATION_ZERO_EXECUTION_ENABLED=False)
    def test_staff_api_cannot_start_zero_termination_when_switch_is_off(self):
        case, proposal = self.accepted(amount=0)
        staff = self.client_for(self.operator_account)
        url = f'/api/admin/aftersales/{case.pk}/negotiation-execution/'
        self.assertFalse(staff.get(url).data['data']['zero_execution_enabled'])
        response = staff.post(url, {'proposal_id':proposal.pk, 'client_request_id':'zero-switch-off'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(NegotiationExecution.objects.exists())
        self.assertFalse(self.order.refund_records.exists())

    @override_settings(NEGOTIATION_REFUND_EXECUTION_ENABLED=True, NEGOTIATION_ZERO_EXECUTION_ENABLED=True)
    def test_invalid_deposit_percent_rejects_zero_termination(self):
        case, proposal = self.accepted(amount=0)
        self.order.service_deposit_percent = 101; self.order.save(update_fields=['service_deposit_percent'])
        response = self.client_for(self.operator_account).post(
            f'/api/admin/aftersales/{case.pk}/negotiation-execution/',
            {'proposal_id':proposal.pk, 'client_request_id':'zero-invalid-deposit'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(NegotiationExecution.objects.exists())
        self.assertFalse(self.order.refund_records.exists())

    @override_settings(NEGOTIATION_REFUND_EXECUTION_ENABLED=True, NEGOTIATION_ZERO_EXECUTION_ENABLED=True)
    def test_affected_provider_already_settled_blocks_zero_termination(self):
        from django.utils import timezone
        case, proposal = self.accepted(amount=0)
        self.order.status = 'COMPLETED'; self.order.completed_at = timezone.now()
        self.order.save(update_fields=['status', 'completed_at'])
        self.order.providers.filter(provider_account=self.a_account).update(settled_at=timezone.now())
        response = self.client_for(self.operator_account).post(
            f'/api/admin/aftersales/{case.pk}/negotiation-execution/',
            {'proposal_id':proposal.pk, 'client_request_id':'zero-affected-settled'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        case.refresh_from_db()
        self.assertEqual(case.status, 'OPEN')
        self.assertFalse(NegotiationExecution.objects.exists())
        self.assertFalse(self.order.refund_records.exists())

    @override_settings(NEGOTIATION_REFUND_EXECUTION_ENABLED=False)
    def test_old_schema_can_still_recover_existing_refund_with_switch_off(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from orders.negotiation_termination import execute_and_terminate
        case, proposal = self.accepted(amount=1000)
        receipt = execute_and_terminate(case.pk, proposal.pk, operator=self.operator_account,
            legacy_operator=self.operator, request_id='pre-zero-schema-refund')
        original = connection.introspection.get_table_description

        def without_zero_columns(cursor, table):
            return [column for column in original(cursor, table)
                    if column.name not in {'zero_order_id', 'zero_request_id', 'zero_allocation'}]

        with patch.object(connection.introspection, 'get_table_description', side_effect=without_zero_columns), \
                CaptureQueriesContext(connection) as queries:
            response = self.client_for(self.operator_account).get(
                f'/api/admin/aftersales/{case.pk}/negotiation-execution/',
                {'proposal_id':proposal.pk, 'client_request_id':'pre-zero-schema-refund'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['data']['execution_id'], receipt.pk)
        receipt_queries = [row['sql'].lower() for row in queries.captured_queries
                           if 'from "orders_negotiationexecution"' in row['sql'].lower()]
        self.assertTrue(receipt_queries)
        self.assertTrue(all('zero_order_id' not in sql and 'zero_request_id' not in sql
                            and 'zero_allocation' not in sql for sql in receipt_queries))


@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
                   NEGOTIATION_ZERO_EXECUTION_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class ZeroNegotiationStoreTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_new_case_after_zero_termination_does_not_touch_other_store(self):
        from datetime import timedelta
        from django.utils import timezone
        from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
        from club_accounts.tokens import issue_account_tokens
        from engagement.models import OrderFundingAllocation, PointFundingLot
        from orders.models import AfterSale, Order, OrderProvider
        from orders.negotiation_proposals import create, decide
        from orders.negotiation_zero import terminate_zero_internal
        from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        from rest_framework.test import APIClient

        fixtures = []
        for store in (resolve_store(), xinan_store()):
            with store_context(store):
                boss, first, second = make_user(), make_provider(), make_provider()
                staff_user = make_console_user(['support:edit', 'order:refund', 'order:view'])
                users = (boss, first, second, staff_user)
                accounts = [get_or_create_account_for_legacy_user(user) for user in users]
                for user, account in zip(users, accounts):
                    link_legacy_relations(user, account)
                for user, account in zip((first, second), accounts[1:3]):
                    user.escort_profile.account = account; user.escort_profile.save(update_fields=['account'])
                staff_user.admin_membership.account = accounts[3]
                staff_user.admin_membership.save(update_fields=['account'])
                order = make_order(boss, service=make_service(price=1000), provider=first, status='IN_SERVICE',
                    customer_account=accounts[0], provider_account=accounts[1], payment_status='PAID',
                    escort_mode='DOUBLE', requires_boss_selection=True, amount=1000, original_amount=1000,
                    provider_income=800, shop_income=200, in_service_at=timezone.now()-timedelta(hours=1),
                    settlement_mode='REPORT_APPROVAL', service_clock_version=1, service_aftersale_hours=72,
                    service_freeze_hours=72, service_deposit_percent=60,
                    funding_terms={'vip_enabled':True, 'points_enabled':True, 'points_rate':100, 'birthday_multiplier':1})
                for user, account in zip((first, second), accounts[1:3]):
                    OrderProvider.objects.create(order=order, provider=user, provider_account=account,
                        settlement_base=500, provider_income=400, commission_rate=20)
                    get_wallet(user=user, account=account)
                wallet = get_wallet(user=boss, account=accounts[0]); wallet.balance = 2000
                wallet.save(update_fields=['balance'])
                Transaction.objects.create(wallet=wallet, order=order, tx_type='PAY', amount=-1000,
                                           balance_before=3000, balance_after=2000)
                lot = PointFundingLot.objects.create(account=accounts[0], source_key='zero-team-store',
                                                     kind='PREPAID', remaining=0)
                OrderFundingAllocation.objects.create(order=order, lot=lot, kind='PREPAID', amount=1000)
                case = AfterSale.objects.create(order=order, account=accounts[0], reason='首位陪玩协商终止',
                    client_request_id='same-team-case', affected_provider_account_ids=[accounts[1].pk])
                proposal = create(case.pk, actor=accounts[3], request_id='same-team-proposal', expected_version=0,
                    refund_units=0, provider_terms=[{'account_id':accounts[1].pk, 'retained_units':400}],
                    explanation='双方同意终止')
                for account in (accounts[0], accounts[1]):
                    decide(case.pk, actor=account, proposal_id=proposal.pk, accepted=True)
                fixtures.append((store, order.pk, case.pk, proposal.pk, accounts[2].pk, accounts[3],
                                 staff_user, issue_account_tokens(accounts[3])[0]))
        self.assertEqual(fixtures[0][1:5], fixtures[1][1:5])
        with store_context(fixtures[0][0]):
            terminate_zero_internal(fixtures[0][2], fixtures[0][3], operator=fixtures[0][5],
                                    legacy_operator=fixtures[0][6], request_id='team-zero')
        client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + fixtures[0][7])
        result = client.post('/api/admin/aftersales/open-negotiation/', {'order_id':fixtures[0][1],
            'reason':'继续服务陪玩的售后', 'affected_provider_account_ids':[fixtures[0][4]],
            'client_request_id':'team-new-case'}, format='json')
        self.assertEqual(result.data.get('code'), 0, result.data)
        preview_path = f'/api/admin/aftersales/{fixtures[0][2]}/negotiation-preview/'
        preview = client.get(preview_path, {'refund_units':500})
        self.assertEqual((preview.data.get('code'), preview.data['data']['remaining_refundable_units']), (0, 1000))
        self.assertTrue(preview.data['data']['read_only'])
        self.assertEqual(preview.data['data']['provider_terms'][0]['account_id'], fixtures[0][4])
        funding = client.get(f'/api/admin/aftersales/{fixtures[0][2]}/negotiation-funding/', {'refund_units':500})
        self.assertEqual(funding.data.get('code'), 0, funding.data)
        self.assertEqual(funding.data['data']['providers'][0]['account_id'], fixtures[0][4])
        self.assertTrue(funding.data['data']['read_only_followup'])
        with store_context(fixtures[0][0]):
            self.assertEqual(AfterSale.objects.get(pk=fixtures[0][2]).affected_provider_account_ids, [fixtures[0][4]])
        with store_context(fixtures[1][0]):
            self.assertEqual(AfterSale.objects.get(pk=fixtures[1][2]).client_request_id, 'same-team-case')
            self.assertEqual(Order.objects.get(pk=fixtures[1][1]).payment_status, 'PAID')
            self.assertEqual(NegotiationExecution.objects.count(), 0)

        # Complete B only inside a rolled-back transaction, then verify that
        # identical order/case IDs in the other tenant cannot use its receipt.
        from tempfile import TemporaryDirectory
        from orders.negotiation_completed_followup import inspect_completed_followup
        from orders.tests.test_provider_aftersale_status import image
        with TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            with store_context(fixtures[0][0]), transaction.atomic():
                order = Order.objects.get(pk=fixtures[0][1])
                provider = type(order.customer_account).objects.get(pk=fixtures[0][4])
                player = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(provider)[0])
                completed = player.post(f'/api/orders/orders/{order.pk}/complete/',
                                        {'completion_image':image()}, format='multipart')
                self.assertEqual(completed.data.get('code'), 0, completed.data)
                self.assertEqual(inspect_completed_followup(fixtures[0][2])['provider_account_id'], fixtures[0][4])
                with store_context(fixtures[1][0]), transaction.atomic():
                    with self.assertRaises(ValidationError):
                        inspect_completed_followup(fixtures[1][2])
                with override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True,
                        NEGOTIATION_COMPLETED_FOLLOWUP_PREVIEW_ENABLED=True,
                        NEGOTIATION_COMPLETED_FOLLOWUP_EXECUTION_TESTING=True):
                    from orders.negotiation_termination import execute_and_terminate
                    proposal = create(fixtures[0][2], actor=fixtures[0][5], request_id='completed-store-proposal',
                        expected_version=1, refund_units=300,
                        provider_terms=[{'account_id':fixtures[0][4], 'retained_units':100}],
                        explanation='双方确认完成后的退款')
                    for actor in (order.customer_account, provider):
                        decide(fixtures[0][2], actor=actor, proposal_id=proposal.pk, accepted=True)
                    receipt = execute_and_terminate(fixtures[0][2], proposal.pk, operator=fixtures[0][5],
                        legacy_operator=fixtures[0][6], request_id='completed-store-refund')
                    self.assertEqual(receipt.refund.amount, 300)
                    self.assertEqual(Order.objects.get(pk=order.pk).refund_records.count(), 1)
                    with store_context(fixtures[1][0]):
                        other = Order.objects.get(pk=fixtures[1][1])
                        self.assertEqual((other.status, other.payment_status, other.refund_records.count()),
                                         ('IN_SERVICE', 'PAID', 0))
                        self.assertEqual(get_wallet(account_id=other.customer_account_id).balance, 2000)
                transaction.set_rollback(True)

        # The same order and case IDs exist in both isolated stores. Execute only
        # in the first store and prove the second store's money and case survive.
        from orders.models import NegotiationFollowupLink
        with override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True):
            created = client.post(f'/api/admin/aftersales/{fixtures[0][2]}/negotiation-proposal/', {
                'request_id':'store-followup-proposal', 'expected_version':1, 'refund_units':300,
                'provider_terms':[{'account_id':fixtures[0][4], 'retained_units':100}],
                'explanation':'双方确认继续陪玩的退款方案'}, format='json')
            self.assertEqual(created.data.get('code'), 0, created.data)
            proposal_id = created.data['data']['id']
            with store_context(fixtures[0][0]):
                order = Order.objects.get(pk=fixtures[0][1])
                for account in (order.customer_account, type(order.customer_account).objects.get(pk=fixtures[0][4])):
                    decide(fixtures[0][2], actor=account, proposal_id=proposal_id, accepted=True)
            done = client.post(f'/api/admin/aftersales/{fixtures[0][2]}/negotiation-execution/', {
                'proposal_id':proposal_id, 'client_request_id':'store-followup-execute'}, format='json')
            self.assertEqual(done.data.get('code'), 0, done.data)
        with store_context(fixtures[0][0]):
            self.assertEqual(Order.objects.get(pk=fixtures[0][1]).status, 'COMPLETED')
            self.assertEqual(NegotiationFollowupLink.objects.count(), 1)
            from orders.negotiation_aftersale_scope import closed_provider_accounts
            with transaction.atomic():
                order = Order.objects.select_for_update().get(pk=fixtures[0][1])
                closed = closed_provider_accounts(order)
                self.assertEqual(len(closed), 2)
                self.assertIn(fixtures[0][4], closed)
        with store_context(fixtures[1][0]):
            self.assertEqual(Order.objects.get(pk=fixtures[1][1]).status, 'IN_SERVICE')
            self.assertEqual(Order.objects.get(pk=fixtures[1][1]).payment_status, 'PAID')
            self.assertEqual(AfterSale.objects.get(pk=fixtures[1][2]).status, 'OPEN')
            self.assertFalse(NegotiationFollowupLink.objects.exists())
            with transaction.atomic():
                order = Order.objects.select_for_update().get(pk=fixtures[1][1])
                self.assertEqual(closed_provider_accounts(order), set())

    def test_additional_negotiated_refund_guard_is_scoped_to_store(self):
        from orders.models import OrderRefund
        from orders.refunds import RefundError, reject_additional_negotiation_refund
        from orders.tests.factories import make_order, make_user
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store

        orders = []
        for index, store in enumerate((resolve_store(), xinan_store())):
            with store_context(store):
                order = make_order(make_user())
                OrderRefund.objects.create(order=order, amount=10, reason='隔离测试',
                    benefit_effects={'negotiation_execution_version':1} if index == 0 else {})
                orders.append((store, order.pk))
        self.assertEqual(orders[0][1], orders[1][1])
        with store_context(orders[0][0]):
            from orders.models import Order
            with self.assertRaises(RefundError) as rejected:
                reject_additional_negotiation_refund(Order.objects.get(pk=orders[0][1]))
            self.assertEqual(rejected.exception.code, 'NEGOTIATION_ADDITIONAL_REFUND_UNAVAILABLE')
        with store_context(orders[1][0]):
            reject_additional_negotiation_refund(Order.objects.get(pk=orders[1][1]))

    def test_same_order_and_proposal_numbers_do_not_cross_stores(self):
        from datetime import timedelta
        from django.utils import timezone
        from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
        from club_accounts.tokens import issue_account_tokens
        from engagement.models import OrderFundingAllocation, PointFundingLot
        from orders.models import AfterSale, OrderProvider
        from orders.negotiation_proposals import create, decide
        from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        from rest_framework.test import APIClient

        fixtures = []
        for store in (resolve_store(), xinan_store()):
            with store_context(store):
                boss, provider, staff_user = make_user(), make_provider(), make_console_user(['support:edit', 'order:refund'])
                boss_account, provider_account, staff = [get_or_create_account_for_legacy_user(u)
                                                          for u in (boss, provider, staff_user)]
                for user, account in ((boss, boss_account), (provider, provider_account), (staff_user, staff)):
                    link_legacy_relations(user, account)
                provider.escort_profile.account = provider_account; provider.escort_profile.save(update_fields=['account'])
                staff_user.admin_membership.account = staff; staff_user.admin_membership.save(update_fields=['account'])
                order = make_order(boss, service=make_service(price=1000), provider=provider, status='IN_SERVICE',
                    customer_account=boss_account, provider_account=provider_account, payment_status='PAID',
                    amount=1000, original_amount=1000, provider_income=800, shop_income=200,
                    in_service_at=timezone.now()-timedelta(hours=1), settlement_mode='REPORT_APPROVAL',
                    service_clock_version=1, service_aftersale_hours=72, service_freeze_hours=72,
                    service_deposit_percent=60, funding_terms={'vip_enabled':True, 'points_enabled':True,
                                                               'points_rate':100, 'birthday_multiplier':1})
                OrderProvider.objects.create(order=order, provider=provider, provider_account=provider_account,
                    settlement_base=1000, provider_income=800, commission_rate=20)
                wallet = get_wallet(user=boss, account=boss_account); wallet.balance = 2000; wallet.save(update_fields=['balance'])
                get_wallet(user=provider, account=provider_account)
                Transaction.objects.create(wallet=wallet, order=order, tx_type='PAY', amount=-1000,
                                           balance_before=3000, balance_after=2000)
                lot = PointFundingLot.objects.create(account=boss_account, source_key='zero-store-test', kind='PREPAID', remaining=0)
                OrderFundingAllocation.objects.create(order=order, lot=lot, kind='PREPAID', amount=1000)
                case = AfterSale.objects.create(order=order, account=boss_account, reason='隔离协商',
                    client_request_id='same-case', affected_provider_account_ids=[provider_account.pk])
                proposal = create(case.pk, actor=staff, request_id='same-proposal', expected_version=0,
                    refund_units=0, provider_terms=[{'account_id':provider_account.pk, 'retained_units':800}],
                    explanation='双方同意终止')
                for member in (boss_account, provider_account):
                    decide(case.pk, actor=member, proposal_id=proposal.pk, accepted=True)
                fixtures.append((store, order.pk, case.pk, proposal.pk, staff, staff_user, wallet.pk,
                                 issue_account_tokens(staff)[0]))
        self.assertEqual(fixtures[0][1:4], fixtures[1][1:4])
        with store_context(fixtures[0][0]):
            receipt = apply_zero(fixtures[0][2], fixtures[0][3], operator=fixtures[0][4],
                                 legacy_operator=fixtures[0][5], request_id='same-zero')
            self.assertEqual(receipt.zero_order_id, fixtures[0][1])
            self.assertEqual(NegotiationExecution.objects.count(), 1)
        client_a = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + fixtures[0][7])
        path = f'/api/admin/aftersales/{fixtures[0][2]}/negotiation-execution/'
        result = client_a.post(path, {'proposal_id':fixtures[0][3], 'client_request_id':'same-zero'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        with store_context(fixtures[1][0]):
            self.assertEqual(NegotiationExecution.objects.count(), 0)
            self.assertEqual(type(wallet).objects.get(pk=fixtures[1][6]).balance, 2000)
            self.assertEqual(AfterSale.objects.get(pk=fixtures[1][2]).status, 'OPEN')
        client_b = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + fixtures[1][7])
        result = client_b.post(path, {'proposal_id':fixtures[1][3], 'client_request_id':'same-zero'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        with store_context(fixtures[1][0]):
            self.assertEqual(NegotiationExecution.objects.count(), 1)
            self.assertEqual(AfterSale.objects.get(pk=fixtures[1][2]).status, 'RESOLVED')
