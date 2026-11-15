"""Ranking uses the real recorded responsibility of each refunded team member."""
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from orders.grab_original_facts import original_amount_facts
from orders.grab_ranking import SubmittedCandidate, SubmittedMember, build_rankable_candidates, decide as rank_decide
from orders.negotiation_execution import apply_refund
from orders.negotiation_proposals import decide
from orders.tests import test_negotiation_proposals as fixture
from orders.tests.factories import make_console_user
from wallet.services import get_wallet


@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class TeamRefundRankingFactsTests(APITestCase):
    def setUp(self):
        self.fixture = fixture.NegotiationProposalTests(
            methodName='test_scoped_boss_and_affected_provider_confirm_without_money_or_order_changes')
        self.fixture.setUp()
        self.operator_user = make_console_user(['order:view', 'order:refund'])
        self.operator = self.fixture.account(self.operator_user)
        for user, account in ((self.fixture.a, self.fixture.a_account),
                              (self.fixture.b, self.fixture.b_account)):
            get_wallet(user=user, account=account)

    def execute(self, scope, amount):
        case = self.fixture.case(scope)
        proposal = self.fixture.propose(case, scope=scope, refund=amount)
        for actor in [self.fixture.boss_account, *[self.fixture.a_account if pk == self.fixture.a_account.pk
                                                    else self.fixture.b_account for pk in scope]]:
            decide(case.pk, actor=actor, proposal_id=proposal.pk, accepted=True)
        return apply_refund(case.pk, proposal.pk, operator=self.operator, legacy_operator=self.operator_user,
                            request_id='team-ranking-refund')

    def test_refund_targeted_to_one_player_changes_only_their_ranking_amount(self):
        a, b = self.fixture.a_account.pk, self.fixture.b_account.pk
        execution = self.execute([a], 1000)
        self.assertEqual(execution.refund.amount, 1000)
        facts = original_amount_facts([a, b], as_of=timezone.now())
        self.assertEqual((facts[a].amount, facts[b].amount), (4000, 5000))
        self.assertEqual((facts[a].review_order_ids, facts[b].review_order_ids), ((), ()))
        now = timezone.now()
        candidates = [SubmittedCandidate(1, (SubmittedMember(a, 'GOLD'),), now),
                      SubmittedCandidate(2, (SubmittedMember(b, 'GOLD'),), now)]
        self.assertEqual(rank_decide(build_rankable_candidates(candidates, as_of=now)).winner_id, 2)

    def test_equal_team_split_rounds_each_person_up_and_keeps_the_difference_separate(self):
        a, b = self.fixture.a_account.pk, self.fixture.b_account.pk
        execution = self.execute(sorted([a, b]), 1010)
        self.assertEqual(execution.rounding_to_store_units, 10)
        self.assertEqual(sorted(execution.shares.values_list('burden', flat=True)), [510, 510])
        facts = original_amount_facts([a, b], as_of=timezone.now())
        self.assertEqual((facts[a].amount, facts[b].amount), (4490, 4490))
        self.assertEqual((facts[a].review_order_ids, facts[b].review_order_ids), ((), ()))
