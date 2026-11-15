from datetime import datetime, timedelta, timezone
from unittest import TestCase

from orders.grab_ranking import CandidateFacts, MemberFacts, decide


NOW = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)


def member(account=1, tier='', amount=0, good=0, total=0):
    return MemberFacts(account, tier, amount, good, total)


def candidate(identifier, *members, at=NOW, historical=False):
    return CandidateFacts(identifier, tuple(members), at, historical)


class GrabRankingTest(TestCase):
    def test_priority_is_pass_then_original_amount_then_exact_good_rate(self):
        rows = [candidate(1, member(1, 'SILVER', 999999, 100, 100)),
                candidate(2, member(2, 'GOLD', 100, 9, 10)),
                candidate(3, member(3, 'GOLD', 200, 1, 2)),
                candidate(4, member(4, 'GOLD', 200, 2, 3))]
        self.assertEqual(decide(rows).winner_id, 4)

    def test_team_takes_each_dimension_independently(self):
        team = candidate(1, member(1, 'BLACK', 100, 8, 10), member(2, 'GOLD', 1000, 1, 1))
        competitor = candidate(2, member(3, 'BLACK', 900, 99, 100))
        self.assertEqual(decide([competitor, team]).winner_id, 1)
        self.assertEqual(team.comparison_key[:2], (4, 1000))

    def test_unrated_is_zero_and_rational_comparison_never_rounds(self):
        unrated = candidate(1, member(1, 'SILVER', 100))
        rated = candidate(2, member(2, 'SILVER', 100, 1, 1000))
        self.assertEqual(decide([unrated, rated]).winner_id, 2)
        a = candidate(3, member(3, 'GOLD', 1, 999999999999999999, 1000000000000000000))
        b = candidate(4, member(4, 'GOLD', 1, 999999999999999998, 1000000000000000000))
        self.assertEqual(decide([b, a]).winner_id, 3)

    def test_same_metrics_use_completion_time_then_reopen_exact_ties(self):
        first = candidate(1, member(1, 'GOLD', 5), at=NOW)
        later = candidate(2, member(2, 'GOLD', 5), at=NOW+timedelta(microseconds=1))
        self.assertEqual(decide([later, first]).winner_id, 1)
        tie = candidate(3, member(3, 'GOLD', 5), at=NOW)
        result = decide([later, tie, first])
        self.assertIsNone(result.winner_id)
        self.assertEqual(result.tied_ids, (1, 3))

    def test_missing_historical_time_discards_only_when_needed_to_compare(self):
        unknown = candidate(1, member(1, 'GOLD', 5), at=None, historical=True)
        self.assertEqual(decide([unknown, candidate(2, member(2, 'SILVER', 500))]).winner_id, 1)
        tied = candidate(3, member(3, 'GOLD', 5), at=NOW)
        decision = decide([unknown, tied])
        self.assertEqual((decision.winner_id, decision.discarded_missing_time), (3, (1,)))
        other_unknown = candidate(4, member(4, 'GOLD', 5), at=None, historical=True)
        result = decide([unknown, other_unknown, candidate(2, member(2, 'SILVER', 500))])
        self.assertEqual((result.winner_id, result.discarded_missing_time), (2, (1, 4)))

    def test_invalid_or_incomplete_evidence_is_rejected(self):
        for args in [(1, 'OTHER', 0, 0, 0), (1, '', -1, 0, 0), (1, '', 0, 2, 1), (True, '', 0, 0, 0)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                MemberFacts(*args)
        with self.assertRaises(ValueError): candidate(1, member(), at=None)
        with self.assertRaises(ValueError): candidate(1, member(), member())
        with self.assertRaises(ValueError): decide([candidate(1, member()), candidate(1, member(2))])
        self.assertIsNone(decide([]).winner_id)


from django.test import TestCase as DjangoTestCase
from django.utils import timezone as django_timezone


class GrabEvaluationFactsTest(DjangoTestCase):
    def test_counts_valid_recent_evaluations_using_business_account_first(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from orders.grab_ranking import evaluation_facts
        from orders.models import Evaluation
        from orders.tests.factories import make_order, make_provider, make_user
        now = django_timezone.now()
        boss = make_user()
        first, second = [make_provider(deposit_mode='EARNINGS') for _ in range(2)]
        accounts = [get_or_create_account_for_legacy_user(user) for user in (first, second)]
        for user, account in zip((first, second), accounts):
            user.escort_profile.account = account; user.escort_profile.save(update_fields=['account'])
        def evaluation(provider, account, score, *, created, voided=None):
            order = make_order(boss)
            item = Evaluation.objects.create(order=order, customer=boss, provider=provider,
                                              provider_account=account, score=score, voided_at=voided)
            Evaluation.objects.filter(pk=item.pk).update(created_at=created)
        evaluation(first, accounts[0], 5, created=now-timedelta(days=30))
        evaluation(first, accounts[0], 3, created=now-timedelta(days=1))
        evaluation(first, accounts[0], 5, created=now-timedelta(days=30, microseconds=1))
        evaluation(first, accounts[0], 5, created=now+timedelta(microseconds=1))
        evaluation(first, accounts[0], 5, created=now-timedelta(days=2), voided=now)
        evaluation(first, accounts[0], 4, created=now-timedelta(days=2), voided=now+timedelta(seconds=1))
        evaluation(first, None, 4, created=now-timedelta(days=1))
        # A present business account is authoritative even if legacy IDs conflict.
        evaluation(first, accounts[1], 5, created=now-timedelta(days=1))
        result = evaluation_facts([account.pk for account in accounts], as_of=now)
        self.assertEqual(result[accounts[0].pk], {'good_evaluations':3, 'valid_evaluations':4})
        self.assertEqual(result[accounts[1].pk], {'good_evaluations':1, 'valid_evaluations':1})
        first_facts = MemberFacts(accounts[0].pk, 'GOLD', 100, **result[accounts[0].pk])
        second_facts = MemberFacts(accounts[1].pk, 'GOLD', 100, **result[accounts[1].pk])
        self.assertEqual(decide([candidate(1, first_facts), candidate(2, second_facts)]).winner_id, 2)
        with self.assertRaises(ValueError):
            evaluation_facts([999999], as_of=now)
        with self.assertRaises(ValueError):
            evaluation_facts([accounts[0].pk], as_of=now.replace(tzinfo=None))


from unittest import skipUnless
from django.conf import settings


@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class GrabEvaluationStoreTest(DjangoTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_same_account_number_uses_only_current_store_evaluations(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from orders.grab_ranking import evaluation_facts
        from orders.models import Evaluation
        from orders.tests.factories import make_order, make_provider, make_user
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        now = django_timezone.now()
        facts = []
        for store, score in ((resolve_store(), 5), (xinan_store(), 1)):
            with store_context(store):
                boss = make_user(); provider = make_provider(deposit_mode='EARNINGS')
                account = get_or_create_account_for_legacy_user(provider)
                provider.escort_profile.account = account; provider.escort_profile.save(update_fields=['account'])
                Evaluation.objects.create(order=make_order(boss), customer=boss, provider=provider,
                                          provider_account=account, score=score)
                facts.append((account.pk, evaluation_facts([account.pk], as_of=django_timezone.now())[account.pk]))
        self.assertEqual(facts[0][0], facts[1][0])
        self.assertEqual([row[1]['good_evaluations'] for row in facts], [1, 0])
        self.assertEqual([row[1]['valid_evaluations'] for row in facts], [1, 1])
