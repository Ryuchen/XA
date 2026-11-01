"""Rating summaries follow real evaluation rows, including legacy identities."""
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.ratings import refresh_escort_rating
from orders.tests.factories import make_completed_order, make_evaluation, make_provider, make_user


class RatingSummaryTest(TestCase):
    def setUp(self):
        self.provider = make_provider()
        self.profile = self.provider.escort_profile

    def add(self, score, **kwargs):
        order = make_completed_order(make_user(), self.provider)
        return make_evaluation(order, score=score, **kwargs)

    def test_rebuild_uses_original_scores_after_multiple_changes(self):
        reviews = [self.add(score) for score in (5, 4, 4, 1, 2, 3, 5)]
        refresh_escort_rating(self.provider.pk)
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.rating_count, self.profile.rating_avg), (7, Decimal('3.43')))
        refresh_escort_rating(self.provider.pk, exclude_evaluation_id=reviews[0].pk)
        reviews[0].delete()
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.rating_count, self.profile.rating_avg), (6, Decimal('3.17')))

    def test_explicit_account_and_mapped_legacy_reviews_are_both_counted(self):
        account = get_or_create_account_for_legacy_user(self.provider)
        self.profile.account = account
        self.profile.save(update_fields=['account'])
        self.add(5, provider_account=account)
        self.add(3)
        refresh_escort_rating(self.provider.pk, provider_account_id=account.pk)
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.rating_count, self.profile.rating_avg), (2, Decimal('4.00')))

    def test_other_explicit_account_is_not_counted_by_legacy_user_id(self):
        account = get_or_create_account_for_legacy_user(self.provider)
        other = get_or_create_account_for_legacy_user(make_provider())
        self.profile.account = account
        self.profile.save(update_fields=['account'])
        self.add(5, provider_account=account)
        self.add(1, provider_account=other)
        refresh_escort_rating(self.provider.pk, provider_account_id=account.pk)
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.rating_count, self.profile.rating_avg), (1, Decimal('5.00')))

    def test_no_profile_is_a_noop(self):
        refresh_escort_rating(None)
        refresh_escort_rating(make_user().pk)

    def test_audit_reports_existing_drift_without_changing_it(self):
        self.add(5)
        self.profile.rating_avg = Decimal('3.00')
        self.profile.rating_count = 1
        self.profile.save(update_fields=['rating_avg', 'rating_count'])
        output = StringIO()
        call_command('audit_escort_ratings', '--details', stdout=output)
        self.assertIn(f'profile={self.profile.pk} stored=1/3.00 actual=1/5.00', output.getvalue())
        self.assertIn('mismatched=1', output.getvalue())
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.rating_count, self.profile.rating_avg), (1, Decimal('3.00')))

    def test_repair_corrects_average_but_leaves_unmatched_review_counts(self):
        self.add(5)
        self.profile.rating_avg = Decimal('3.00')
        self.profile.rating_count = 1
        self.profile.save(update_fields=['rating_avg', 'rating_count'])
        other = make_provider().escort_profile
        other.rating_avg = Decimal('4.00')
        other.rating_count = 3
        other.save(update_fields=['rating_avg', 'rating_count'])
        output = StringIO()
        call_command('audit_escort_ratings', '--repair-averages', '--details', stdout=output)
        self.profile.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual((self.profile.rating_count, self.profile.rating_avg), (1, Decimal('5.00')))
        self.assertEqual((other.rating_count, other.rating_avg), (3, Decimal('4.00')))
        self.assertIn('average_repairable=1 repaired=1', output.getvalue())
        output = StringIO()
        call_command('audit_escort_ratings', '--repair-averages', stdout=output)
        self.assertIn('average_repairable=0 repaired=0', output.getvalue())
