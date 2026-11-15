"""Completed teammate and prior negotiated income are verified without money writes."""
from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase
from stores.db import transaction

from orders.negotiation_completed_followup import inspect_completed_followup
from orders.tests import test_negotiation_followup as fixtures
from orders.tests import test_negotiation_settlement as settlement_fixture
from orders.tests.test_provider_aftersale_status import image
from wallet.models import Transaction
from wallet.services import get_wallet


@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_ZERO_EXECUTION_ENABLED=True,
                   NEGOTIATION_REFUND_EXECUTION_ENABLED=True, REFUND_DEPOSIT_DEFICIT_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class CompletedFollowupEvidenceTests(APITestCase):
    account = fixtures.FollowupPreviewTests.account
    client_for = fixtures.FollowupPreviewTests.client_for
    case = fixtures.FollowupPreviewTests.case
    propose = fixtures.FollowupPreviewTests.propose
    accepted = fixtures.FollowupPreviewTests.accepted
    execute = fixtures.FollowupPreviewTests.execute
    setUp = fixtures.FollowupPreviewTests.setUp
    followup_case = fixtures.FollowupPreviewTests.followup_case

    def complete_without_case(self, amount):
        self.order.requires_boss_selection = True
        self.order.save(update_fields=['requires_boss_selection'])
        case, proposal = self.accepted(amount=amount)
        receipt = self.execute(case, proposal)
        result = self.client_for(self.b_account).post(f'/api/orders/orders/{self.order.pk}/complete/',
            {'completion_image':image()}, format='multipart')
        self.assertEqual(result.data.get('code'), 0, result.data)
        self.order.refresh_from_db()
        case.refresh_from_db()
        return case, receipt

    def completed_case(self, amount):
        case, receipt = self.complete_without_case(amount)
        result = self.client_for(self.boss_account).post(f'/api/orders/orders/{self.order.pk}/aftersale/', {
            'reason':'继续处理 B 的服务', 'affected_provider_account_ids':[self.b_account.pk],
            'client_request_id':'completed-b-case'}, format='json')
        self.assertEqual(result.data.get('code'), 0, result.data)
        case.refresh_from_db()
        return case, receipt

    def evaluate(self, account, score=1):
        return self.client_for(self.boss_account).post('/api/orders/evaluate/', {
            'order_id': self.order.pk, 'provider_account_id': account.pk, 'score': score,
            'skill_score': 5, 'attitude_score': 5, 'communication_score': 5}, format='json')

    def test_b_low_rating_opens_only_b_case_after_a_negotiation(self):
        from orders.models import Evaluation
        from console.payroll_calculation import aftersale_cycles, month_bounds, unassigned_sources
        from types import SimpleNamespace
        for amount in (0, 1000):
            with self.subTest(amount=amount), transaction.atomic():
                case, receipt = self.complete_without_case(amount)
                old_history = list(case.history)
                old_cycles = aftersale_cycles(case)
                self.assertTrue(old_cycles)
                start, end = month_bounds(timezone.localdate().replace(day=1))
                plan = {'dispatch_day_bps': 0, 'dispatch_night_bps': 0, 'target_scope': 'STORE',
                        'performance_fen': 0, 'recharge_bps': 0, 'aftersale_fee_fen': 1000}
                assignment = SimpleNamespace(plan_snapshot=plan)
                before = unassigned_sources([assignment], [], start, end)['aftersale']
                self.assertIn({'id': case.pk, 'cycle_key': old_cycles[0]['cycle_key']}, before)
                attributed = [SimpleNamespace(source_key=f'AFTERSALE:{case.pk}:{cycle["cycle_key"]}')
                              for cycle in old_cycles]
                before_attributed = unassigned_sources([assignment], attributed, start, end)['aftersale']
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.evaluate(self.b_account)
                self.assertEqual(response.data.get('code'), 0, response.data)
                case.refresh_from_db()
                self.assertEqual(aftersale_cycles(case), old_cycles)
                self.assertEqual(unassigned_sources([assignment], [], start, end)['aftersale'], before)
                self.assertEqual(unassigned_sources([assignment], attributed, start, end)['aftersale'],
                                 before_attributed)
                evaluation = Evaluation.objects.get(order=self.order, provider_account=self.b_account)
                self.assertEqual((case.status, case.affected_provider_account_ids), ('OPEN', [self.b_account.pk]))
                self.assertEqual((evaluation.low_rating_aftersale_state, evaluation.low_rating_aftersale_id),
                                 ('OPENED', case.pk))
                self.assertEqual(case.history[:len(old_history)], old_history)
                self.assertEqual(case.history[-1]['evaluation_id'], evaluation.pk)
                from site_messages.models import Message
                self.assertTrue(Message.objects.filter(related_order_id=self.order.pk,
                    recipient_account=self.b_account, title='订单售后处理中').exists())
                self.assertFalse(Message.objects.filter(related_order_id=self.order.pk,
                    recipient_account=self.a_account, title='订单售后处理中').exists())
                self.assertEqual(self.inspect(case)['prior_execution_id'], receipt.pk)
                self.assertNotEqual(self.evaluate(self.b_account).data.get('code'), 0)
                self.assertEqual(Evaluation.objects.filter(order=self.order, provider_account=self.b_account).count(), 1)
                transaction.set_rollback(True)

    def test_a_low_rating_never_reopens_or_attaches_to_b_case(self):
        from orders.models import Evaluation
        from orders.evaluation_aftersale import register_low_rating
        case, _ = self.completed_case(1000)
        before = list(case.history)
        # The current API doesn't offer the released participant as a target;
        # additionally guard the domain function if such an evaluation exists.
        self.assertNotEqual(self.evaluate(self.a_account).data.get('code'), 0)
        evaluation = Evaluation.objects.create(order=self.order, customer=self.boss, provider=self.a,
            customer_account=self.boss_account, provider_account=self.a_account,
            score=1, skill_score=5, attitude_score=5, communication_score=5)
        with transaction.atomic():
            locked = type(self.order).objects.select_for_update().get(pk=self.order.pk)
            register_low_rating(evaluation, locked)
        case.refresh_from_db()
        self.assertEqual((evaluation.low_rating_aftersale_state, evaluation.low_rating_aftersale_id), ('CLOSED', None))
        self.assertEqual((case.status, case.affected_provider_account_ids, case.history),
                         ('OPEN', [self.b_account.pk], before))

    def test_b_low_rating_after_deadline_does_not_open_case(self):
        from orders.models import Evaluation
        case, _ = self.complete_without_case(1000)
        old = (case.status, list(case.history))
        due = self.order.completed_at + timedelta(hours=self.order.service_aftersale_hours)
        with patch('django.utils.timezone.now', return_value=due):
            response = self.evaluate(self.b_account)
        self.assertEqual(response.data.get('code'), 0, response.data)
        case.refresh_from_db()
        evaluation = Evaluation.objects.get(order=self.order, provider_account=self.b_account)
        self.assertEqual((evaluation.low_rating_aftersale_state, evaluation.low_rating_aftersale_id), ('EXPIRED', None))
        self.assertEqual((case.status, case.history), old)

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True, NEGOTIATION_COMPLETED_FOLLOWUP_PREVIEW_ENABLED=True)
    def test_completed_b_preview_and_confirmations_preserve_a_and_never_execute_yet(self):
        from orders.negotiation_followup import preview_followup
        from orders.negotiation_proposals import decide
        case, receipt = self.completed_case(1000)
        staff = self.client_for(self.staff)
        data = staff.get(f'/api/admin/aftersales/{case.pk}/negotiation-proposal/').data['data']
        self.assertTrue(data['followup_preview_only'])
        self.assertFalse(data['execution_available'])
        execution = self.client_for(self.operator_account).get(
            f'/api/admin/aftersales/{case.pk}/negotiation-execution/').data['data']
        self.assertFalse(execution['execution_enabled'])
        before = (self.order.completed_at, Transaction.objects.count(), self.order.refund_records.count(),
                  get_wallet(user=self.boss, account=self.boss_account).balance)
        plan = preview_followup(case.pk, actor=self.staff, refund_units=1000)
        self.assertEqual(plan['provider_terms'][0]['account_id'], self.b_account.pk)
        self.assertEqual(plan['completion_evidence']['completed_at'], self.order.completed_at.isoformat())
        proposal = self.propose(case, version=1, request_id='completed-b-proposal',
                                scope=[self.b_account.pk], refund=1000)
        with self.assertRaises(ValidationError):
            decide(case.pk, actor=self.a_account, proposal_id=proposal.pk, accepted=True)
        decide(case.pk, actor=self.boss_account, proposal_id=proposal.pk, accepted=True)
        decide(case.pk, actor=self.b_account, proposal_id=proposal.pk, accepted=True)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, 'ACCEPTED')
        with self.assertRaisesMessage(ValidationError, '尚未接通'):
            self.execute(case, proposal)
        self.order.refresh_from_db()
        self.assertEqual((self.order.completed_at, Transaction.objects.count(), self.order.refund_records.count(),
                          get_wallet(user=self.boss, account=self.boss_account).balance), before)

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True, NEGOTIATION_COMPLETED_FOLLOWUP_PREVIEW_ENABLED=True)
    def test_completed_preview_accepts_verified_prior_settlement_but_no_unknown_credit(self):
        from orders.negotiation_followup import preview_followup
        case, receipt = self.completed_case(1000)
        submitted = self.order.completed_at
        settlement_fixture.NegotiationSettlementTest.report(self, self.a_account, submitted)
        settlement_fixture.NegotiationSettlementTest.report(self, self.b_account, submitted)
        due = submitted + timedelta(hours=73)
        self.assertEqual(settlement_fixture.NegotiationSettlementTest.settle(self, receipt, due), [self.a.pk])
        with patch('django.utils.timezone.now', return_value=due):
            result = preview_followup(case.pk, actor=self.staff, refund_units=1000)
        self.assertEqual(result['provider_terms'][0]['account_id'], self.b_account.pk)
        self.assertEqual(result['completion_evidence']['completed_at'], submitted.isoformat())
        self.assertEqual(self.order.transactions.filter(tx_type='INCOME', wallet__account=self.a_account).count(), 1)

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True, NEGOTIATION_COMPLETED_FOLLOWUP_PREVIEW_ENABLED=True,
                       NEGOTIATION_COMPLETED_FOLLOWUP_EXECUTION_TESTING=True)
    def test_completed_b_refund_and_settlement_preserve_a_and_completion(self):
        from orders.negotiation_proposals import decide
        from orders.negotiation_termination import execute_and_terminate
        from orders.negotiation_followup_accounting import verify_followup_accounting, verify_followup_termination
        case, prior = self.completed_case(1000)
        completed_at = self.order.completed_at
        settlement_fixture.NegotiationSettlementTest.report(self, self.a_account, completed_at)
        settlement_fixture.NegotiationSettlementTest.report(self, self.b_account, completed_at)
        proposal = self.propose(case, version=1, request_id='completed-b-proposal',
                                scope=[self.b_account.pk], refund=1000)
        for actor in (self.boss_account, self.b_account):
            decide(case.pk, actor=actor, proposal_id=proposal.pk, accepted=True)
        receipt = execute_and_terminate(case.pk, proposal.pk, operator=self.operator_account,
            legacy_operator=self.operator, request_id='completed-b-refund')
        replay = execute_and_terminate(case.pk, proposal.pk, operator=self.operator_account,
            legacy_operator=self.operator, request_id='completed-b-refund')
        self.assertEqual(replay.pk, receipt.pk)
        self.order.refresh_from_db(); case.refresh_from_db()
        self.assertEqual((self.order.status, self.order.completed_at), ('COMPLETED', completed_at))
        self.assertEqual(self.order.providers.get(provider_account=self.b_account).released_at, completed_at)
        self.assertEqual(self.order.refund_records.count(), 2)
        self.assertEqual(get_wallet(user=self.boss, account=self.boss_account).balance, 11000)
        customer = self.client_for(self.boss_account)
        case_url = f'/api/orders/orders/{self.order.pk}/aftersale/'
        response = customer.get(case_url)
        self.assertEqual(response.data['data']['confirmed_termination_provider_ids'],
                         sorted([self.a_account.pk, self.b_account.pk]))
        rejected = customer.post(case_url, {'reason':'不应再次处理', 'client_request_id':'reopen-completed-b',
            'affected_provider_account_ids':[self.b_account.pk]}, format='json')
        self.assertNotEqual(rejected.data.get('code'), 0)
        from orders.negotiation_followup_accounting import marked_followup_id
        from orders.models import NegotiationFollowupLink
        link = NegotiationFollowupLink.objects.get(pk=marked_followup_id(self.order))
        with transaction.atomic():
            verify_followup_accounting(link); verify_followup_termination(link)
        due = completed_at + timedelta(hours=73)
        with override_settings(NEGOTIATION_FOLLOWUP_ENABLED=False,
                               NEGOTIATION_COMPLETED_FOLLOWUP_PREVIEW_ENABLED=False,
                               NEGOTIATION_COMPLETED_FOLLOWUP_EXECUTION_TESTING=False):
            self.assertEqual(settlement_fixture.NegotiationSettlementTest.settle(self, receipt, due), [self.a.pk, self.b.pk])
            self.assertEqual(settlement_fixture.NegotiationSettlementTest.settle(self, receipt, due), [])
        self.assertEqual(get_wallet(user=self.a, account=self.a_account).balance, 3000)
        self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 3000)
        from engagement.confirmation import process_order
        from engagement.models import ConsumptionConfirmation, PointLedger
        with patch('django.utils.timezone.now', return_value=due):
            self.assertTrue(process_order(self.order.pk))
            self.assertFalse(process_order(self.order.pk))
        confirmation = ConsumptionConfirmation.objects.get(order=self.order)
        self.assertEqual((confirmation.amount, confirmation.refunded_amount), (10000, 2000))
        self.assertEqual(PointLedger.objects.get(order=self.order, kind='CONSUMPTION').amount, 8000)

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True, NEGOTIATION_COMPLETED_FOLLOWUP_PREVIEW_ENABLED=True,
                       NEGOTIATION_COMPLETED_FOLLOWUP_EXECUTION_TESTING=True)
    def test_completed_followup_api_preserves_already_spent_a_income(self):
        case, prior = self.complete_without_case(1000)
        self.assertEqual(self.evaluate(self.b_account).data.get('code'), 0)
        case.refresh_from_db()
        self.assertEqual((case.status, case.affected_provider_account_ids), ('OPEN', [self.b_account.pk]))
        start = self.order.completed_at
        settlement_fixture.NegotiationSettlementTest.report(self, self.a_account, start)
        settlement_fixture.NegotiationSettlementTest.report(self, self.b_account, start)
        due = start + timedelta(hours=73)
        self.assertEqual(settlement_fixture.NegotiationSettlementTest.settle(self, prior, due), [self.a.pk])
        wallet = get_wallet(user=self.a, account=self.a_account)
        wallet.balance = 0; wallet.save(update_fields=['balance'])
        Transaction.objects.create(wallet=wallet, tx_type='PAY', amount=-3000,
                                   balance_before=3000, balance_after=0, remark='隔离测试其他消费')
        with patch('django.utils.timezone.now', return_value=due):
            staff = self.client_for(self.staff)
            response = staff.post(f'/api/admin/aftersales/{case.pk}/negotiation-proposal/', {
                'request_id':'completed-api', 'expected_version':1, 'refund_units':1000,
                'provider_terms':[{'account_id':self.b_account.pk, 'retained_units':3000}],
                'explanation':'双方同意处理 B 的服务'}, format='json')
            self.assertEqual(response.data.get('code'), 0, response.data)
            proposal_id = response.data['data']['id']
            url = f'/api/orders/orders/{self.order.pk}/negotiation-proposal/'
            for account in (self.boss_account, self.b_account):
                response = self.client_for(account).post(url, {'proposal_id':proposal_id, 'accepted':True}, format='json')
                self.assertEqual(response.data.get('code'), 0, response.data)
            execution = self.client_for(self.operator_account)
            url = f'/api/admin/aftersales/{case.pk}/negotiation-execution/'
            self.assertTrue(execution.get(url).data['data']['execution_enabled'])
            request = {'proposal_id':proposal_id, 'client_request_id':'completed-api-refund'}
            first = execution.post(url, request, format='json')
            self.assertEqual(first.data.get('code'), 0, first.data)
            check = execution.get(url, request)
            self.assertEqual(check.data.get('code'), 0, check.data)
            self.assertEqual(execution.post(url, request, format='json').data.get('code'), 0)
            self.order.refresh_from_db()
            from orders.negotiation_settlement import execution_for_order
            current = execution_for_order(self.order)
            self.assertEqual(settlement_fixture.NegotiationSettlementTest.settle(self, current, due), [self.b.pk])
        self.order.refresh_from_db()
        self.assertEqual(self.order.completed_at, start)
        self.assertEqual(get_wallet(user=self.a, account=self.a_account).balance, 0)
        self.assertEqual(self.order.transactions.filter(tx_type='INCOME', wallet__account=self.a_account).count(), 1)
        self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 3000)
        self.assertEqual(self.order.refund_records.count(), 2)

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True, NEGOTIATION_COMPLETED_FOLLOWUP_PREVIEW_ENABLED=True,
                       NEGOTIATION_COMPLETED_FOLLOWUP_EXECUTION_TESTING=True)
    def test_completed_followup_audit_failure_rolls_back_everything(self):
        from orders.negotiation_proposals import decide
        from orders.negotiation_termination import execute_and_terminate
        from orders.models import NegotiationFollowupLink
        case, _ = self.completed_case(1000)
        proposal = self.propose(case, version=1, request_id='completed-failure',
                                scope=[self.b_account.pk], refund=1000)
        for actor in (self.boss_account, self.b_account):
            decide(case.pk, actor=actor, proposal_id=proposal.pk, accepted=True)
        before = (self.order.completed_at, Transaction.objects.count(), self.order.refund_records.count(),
                  get_wallet(user=self.boss, account=self.boss_account).balance)
        with patch('console.models.AdminAuditLog.objects.create', side_effect=RuntimeError('audit failure')):
            with self.assertRaises(RuntimeError):
                execute_and_terminate(case.pk, proposal.pk, operator=self.operator_account,
                    legacy_operator=self.operator, request_id='completed-failure-refund')
        self.order.refresh_from_db(); case.refresh_from_db()
        self.assertEqual((self.order.completed_at, Transaction.objects.count(), self.order.refund_records.count(),
                          get_wallet(user=self.boss, account=self.boss_account).balance), before)
        self.assertEqual(case.status, 'OPEN')
        self.assertIsNone(self.order.providers.get(provider_account=self.b_account).released_at)
        self.assertFalse(NegotiationFollowupLink.objects.filter(order=self.order).exists())

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True, NEGOTIATION_COMPLETED_FOLLOWUP_PREVIEW_ENABLED=True,
                       NEGOTIATION_COMPLETED_FOLLOWUP_EXECUTION_TESTING=True)
    def test_completed_b_refund_uses_wallet_then_cash_deposit(self):
        from orders.negotiation_proposals import decide
        from orders.negotiation_termination import execute_and_terminate
        from wallet.models import RefundDepositDeficit
        case, _ = self.completed_case(0)
        wallet = get_wallet(user=self.b, account=self.b_account)
        wallet.balance = 600; wallet.save(update_fields=['balance'])
        profile = fixtures.FollowupPreviewTests.setup_b_cash_deposit(self)
        proposal = self.propose(case, version=1, request_id='completed-b-deposit',
                                scope=[self.b_account.pk], refund=5000)
        for actor in (self.boss_account, self.b_account):
            decide(case.pk, actor=actor, proposal_id=proposal.pk, accepted=True)
        receipt = execute_and_terminate(case.pk, proposal.pk, operator=self.operator_account,
            legacy_operator=self.operator, request_id='completed-b-deposit-refund')
        share = receipt.shares.get(account=self.b_account)
        self.assertEqual((share.pending_deduction, share.wallet_deduction, share.deposit_deduction), (4000, 600, 400))
        wallet.refresh_from_db(); profile.refresh_from_db()
        self.assertEqual((wallet.balance, profile.deposit_paid), (0, 100))
        self.assertEqual(RefundDepositDeficit.objects.get(refund=receipt.refund, account=self.b_account).remaining, 400)

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True, NEGOTIATION_COMPLETED_FOLLOWUP_PREVIEW_ENABLED=True,
                       NEGOTIATION_COMPLETED_FOLLOWUP_EXECUTION_TESTING=True)
    def test_completed_followup_rejects_changed_completion_even_with_recomputed_hash(self):
        from orders.negotiation_proposals import decide
        from orders.negotiation_termination import execute_and_terminate
        from orders.negotiation_followup_accounting import verify_followup_termination
        from orders.negotiation_execution import _allocation_hash
        case, _ = self.completed_case(1000)
        proposal = self.propose(case, version=1, request_id='completed-tamper',
                                scope=[self.b_account.pk], refund=1000)
        for actor in (self.boss_account, self.b_account):
            decide(case.pk, actor=actor, proposal_id=proposal.pk, accepted=True)
        receipt = execute_and_terminate(case.pk, proposal.pk, operator=self.operator_account,
            legacy_operator=self.operator, request_id='completed-tamper-refund')
        link = receipt.previous_followup
        link.projected_ledger['completion_evidence']['completed_at'] = (self.order.completed_at + timedelta(seconds=1)).isoformat()
        link.ledger_hash = _allocation_hash(link.projected_ledger)
        link.save(update_fields=['projected_ledger', 'ledger_hash'])
        with self.assertRaises(ValidationError):
            verify_followup_termination(link)

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True, NEGOTIATION_COMPLETED_FOLLOWUP_PREVIEW_ENABLED=True,
                       NEGOTIATION_COMPLETED_FOLLOWUP_EXECUTION_TESTING=True)
    def test_completed_zero_refund_has_no_fabricated_money_transaction(self):
        from orders.negotiation_proposals import decide
        from orders.negotiation_termination import execute_and_terminate
        from orders.negotiation_followup_accounting import verify_followup_accounting, verify_followup_termination
        case, _ = self.completed_case(1000)
        proposal = self.propose(case, version=1, request_id='completed-zero', scope=[self.b_account.pk], refund=0)
        for actor in (self.boss_account, self.b_account):
            decide(case.pk, actor=actor, proposal_id=proposal.pk, accepted=True)
        before = (Transaction.objects.count(), self.order.refund_records.count())
        receipt = execute_and_terminate(case.pk, proposal.pk, operator=self.operator_account,
            legacy_operator=self.operator, request_id='completed-zero-refund')
        self.assertIsNone(receipt.refund_id)
        self.assertEqual((Transaction.objects.count(), self.order.refund_records.count()), before)
        with transaction.atomic():
            verify_followup_accounting(receipt.previous_followup)
            verify_followup_termination(receipt.previous_followup)

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True, NEGOTIATION_COMPLETED_FOLLOWUP_PREVIEW_ENABLED=True,
                       NEGOTIATION_COMPLETED_FOLLOWUP_EXECUTION_TESTING=True)
    def test_insufficient_b_funds_do_not_refund_or_close_completed_case(self):
        from orders.negotiation_proposals import decide
        from orders.negotiation_termination import execute_and_terminate
        from orders.models import NegotiationFollowupLink
        case, _ = self.completed_case(1000)
        proposal = self.propose(case, version=1, request_id='completed-insufficient',
                                scope=[self.b_account.pk], refund=5000)
        for actor in (self.boss_account, self.b_account):
            decide(case.pk, actor=actor, proposal_id=proposal.pk, accepted=True)
        before = (Transaction.objects.count(), self.order.refund_records.count(),
                  get_wallet(user=self.boss, account=self.boss_account).balance)
        with self.assertRaisesMessage(ValidationError, '不足'):
            execute_and_terminate(case.pk, proposal.pk, operator=self.operator_account,
                legacy_operator=self.operator, request_id='completed-insufficient-refund')
        self.order.refresh_from_db(); case.refresh_from_db()
        self.assertEqual((self.order.status, case.status), ('COMPLETED', 'OPEN'))
        self.assertEqual((Transaction.objects.count(), self.order.refund_records.count(),
                          get_wallet(user=self.boss, account=self.boss_account).balance), before)
        self.assertFalse(NegotiationFollowupLink.objects.filter(order=self.order).exists())

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True, NEGOTIATION_COMPLETED_FOLLOWUP_PREVIEW_ENABLED=True,
                       NEGOTIATION_COMPLETED_FOLLOWUP_EXECUTION_TESTING=True)
    def test_completed_refund_requires_both_boss_and_b_confirmation(self):
        from orders.negotiation_proposals import decide
        from orders.negotiation_termination import execute_and_terminate
        case, _ = self.completed_case(1000)
        proposal = self.propose(case, version=1, request_id='completed-consent',
                                scope=[self.b_account.pk], refund=1000)
        before = (Transaction.objects.count(), self.order.refund_records.count())
        decide(case.pk, actor=self.boss_account, proposal_id=proposal.pk, accepted=True)
        with self.assertRaises(ValidationError):
            execute_and_terminate(case.pk, proposal.pk, operator=self.operator_account,
                legacy_operator=self.operator, request_id='completed-consent-refund')
        self.assertEqual((Transaction.objects.count(), self.order.refund_records.count()), before)
        decide(case.pk, actor=self.b_account, proposal_id=proposal.pk, accepted=True)
        receipt = execute_and_terminate(case.pk, proposal.pk, operator=self.operator_account,
            legacy_operator=self.operator, request_id='completed-consent-refund')
        self.assertEqual(receipt.refund.amount, 1000)

    def inspect(self, case):
        with transaction.atomic():
            return inspect_completed_followup(case.pk)

    def test_normal_completion_and_b_case_are_verified_without_mutation(self):
        for amount in (0, 1000):
            with self.subTest(amount=amount), transaction.atomic():
                case, receipt = self.completed_case(amount)
                before = (Transaction.objects.count(), self.order.refund_records.count(),
                          get_wallet(user=self.boss, account=self.boss_account).balance)
                result = self.inspect(case)
                self.assertEqual((result['prior_execution_id'], result['provider_account_id'],
                                  result['completed_at'], result['previous_already_settled']),
                                 (receipt.pk, self.b_account.pk, self.order.completed_at, False))
                self.assertEqual((Transaction.objects.count(), self.order.refund_records.count(),
                                  get_wallet(user=self.boss, account=self.boss_account).balance), before)
                from orders.negotiation_termination import continuing_team
                self.order.refresh_from_db()
                self.assertIsNone(continuing_team(self.order, verify=True))
                self.assertEqual(continuing_team(self.order, verify=True, allow_completed=True).pk, receipt.pk)
                transaction.set_rollback(True)

    def test_prior_player_can_already_be_settled_but_is_not_paid_twice(self):
        case, receipt = self.completed_case(1000)
        submitted = self.order.completed_at
        settlement_fixture.NegotiationSettlementTest.report(self, self.a_account, submitted)
        settlement_fixture.NegotiationSettlementTest.report(self, self.b_account, submitted)
        due = submitted + timedelta(hours=73)
        self.assertEqual(settlement_fixture.NegotiationSettlementTest.settle(self, receipt, due), [self.a.pk])
        # The previously paid player may have subsequently spent their own
        # funds. The historical payout must still verify without being repaid
        # or requiring their current wallet balance to remain unchanged.
        wallet = get_wallet(user=self.a, account=self.a_account)
        wallet.balance = 0; wallet.save(update_fields=['balance'])
        Transaction.objects.create(wallet=wallet, tx_type='PAY', amount=-3000,
                                   balance_before=3000, balance_after=0, remark='隔离测试其他消费')
        with patch('django.utils.timezone.now', return_value=due):
            self.assertTrue(self.inspect(case)['previous_already_settled'])
        self.assertEqual(get_wallet(user=self.a, account=self.a_account).balance, 0)
        self.assertEqual(self.order.transactions.filter(tx_type='INCOME', wallet__account=self.a_account).count(), 1)
        self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 0)

    def test_timely_case_opened_while_serving_survives_completion(self):
        case, receipt = self.followup_case(1000)
        completed = self.client_for(self.b_account).post(f'/api/orders/orders/{self.order.pk}/complete/',
            {'completion_image':image()}, format='multipart')
        self.assertEqual(completed.data.get('code'), 0, completed.data)
        result = self.inspect(case)
        self.assertEqual((result['prior_execution_id'], result['provider_account_id']),
                         (receipt.pk, self.b_account.pk))

    def test_tampered_previously_settled_income_is_not_accepted_as_evidence(self):
        case, receipt = self.completed_case(1000)
        submitted = self.order.completed_at
        settlement_fixture.NegotiationSettlementTest.report(self, self.a_account, submitted)
        settlement_fixture.NegotiationSettlementTest.report(self, self.b_account, submitted)
        due = submitted + timedelta(hours=73)
        self.assertEqual(settlement_fixture.NegotiationSettlementTest.settle(self, receipt, due), [self.a.pk])
        credit = self.order.transactions.get(tx_type='INCOME', wallet__account=self.a_account)
        credit.amount += 10; credit.save(update_fields=['amount'])
        with patch('django.utils.timezone.now', return_value=due), self.assertRaises(ValidationError):
            self.inspect(case)
        self.assertEqual(self.order.transactions.filter(tx_type='INCOME', wallet__account=self.a_account).count(), 1)

    def test_tampered_completion_case_scope_and_prior_payment_are_rejected(self):
        case, receipt = self.completed_case(1000)
        for fault in ('completion', 'scope', 'late', 'income', 'payment', 'payment_status'):
            with self.subTest(fault=fault), transaction.atomic():
                if fault == 'completion':
                    self.order.status_logs.filter(action='COMPLETE').update(from_status='GRABBED')
                elif fault == 'scope':
                    case.affected_provider_account_ids = [self.a_account.pk]
                    case.save(update_fields=['affected_provider_account_ids'])
                elif fault == 'late':
                    case.created_at = self.order.completed_at + timedelta(hours=72)
                    case.save(update_fields=['created_at'])
                elif fault == 'income':
                    wallet = get_wallet(user=self.a, account=self.a_account)
                    Transaction.objects.create(wallet=wallet, order=self.order, tx_type='INCOME', amount=10,
                                               balance_before=0, balance_after=10)
                elif fault == 'payment':
                    Transaction.objects.filter(order=self.order, tx_type='PAY').update(amount=-9990)
                else:
                    type(self.order).objects.filter(pk=self.order.pk).update(payment_status='PAID')
                with self.assertRaises(ValidationError):
                    self.inspect(case)
                transaction.set_rollback(True)
