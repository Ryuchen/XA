from datetime import datetime, timedelta, timezone
from unittest import TestCase

from orders.grab_ranking import CandidateFacts, MemberFacts
from orders.grab_window import finalize, first_round


T0 = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)


def member(identifier, tier='GOLD'):
    return MemberFacts(identifier, tier, 100, 1, 1)


def candidate(identifier, *, at, tier='GOLD', historical=False):
    return CandidateFacts(identifier, (member(identifier, tier),), at, historical)


class GrabWindowTest(TestCase):
    def round(self, total=60, duration=30):
        return first_round(opened_at=T0, duration_seconds=duration, original_total_deadline=T0+timedelta(seconds=total))

    def evaluate(self, window, candidates=(), *, at=None, watermark=None, cancel=None, eligibility=None):
        now = at or window.deadline+timedelta(microseconds=1)
        return finalize(window, candidates, now=now, processed_through=watermark or window.deadline,
                        cancel_received_at=cancel, winner_eligibility=eligibility)

    def test_first_round_starts_immediately_and_extends_short_total_wait(self):
        window = self.round(total=20, duration=30)
        self.assertEqual(window.deadline, T0+timedelta(seconds=30))
        self.assertEqual(window.total_deadline, window.deadline)
        self.assertFalse(window.simultaneous_visibility)
        self.assertEqual(self.evaluate(window).action, 'CANCEL')
        self.assertEqual(self.evaluate(self.round(total=60)).action, 'PARK')

    def test_wait_until_after_cutoff_and_durable_watermark(self):
        window = self.round()
        self.assertEqual(self.evaluate(window, at=window.deadline).action, 'WAIT')
        self.assertEqual(self.evaluate(window, watermark=window.deadline-timedelta(microseconds=1)).action, 'WAIT')

    def test_cutoff_cancel_wins_and_late_candidate_is_excluded(self):
        window = self.round(total=30)
        before = candidate(1, at=window.deadline-timedelta(microseconds=1))
        self.assertEqual(self.evaluate(window, [before], cancel=window.deadline, eligibility={1:True}).action, 'CANCEL')
        at_cutoff = candidate(2, at=window.deadline)
        self.assertEqual(self.evaluate(window, [at_cutoff]).action, 'CANCEL')
        self.assertEqual(self.evaluate(window, [before], eligibility={1:True}).action, 'ASSIGN')

    def test_top_candidate_revalidated_no_silent_fallback(self):
        window = self.round(total=60)
        high = candidate(1, at=T0, tier='BLACK')
        second = candidate(2, at=T0, tier='SILVER')
        result = self.evaluate(window, [high, second])
        self.assertEqual((result.action, result.winner_id), ('CHECK_WINNER', 1))
        failed = self.evaluate(window, [high, second], eligibility={1:False})
        self.assertEqual(failed.action, 'REOPEN')
        self.assertTrue(failed.next_round.simultaneous_visibility)
        self.assertEqual(failed.next_round.deadline, window.deadline+timedelta(seconds=30, microseconds=1))
        expired = self.evaluate(self.round(total=30), [high, second], eligibility={1:False})
        self.assertEqual(expired.action, 'CANCEL')
        twenty_left = self.round(total=50)
        extended = self.evaluate(twenty_left, [high, second], eligibility={1:False})
        self.assertEqual(extended.action, 'REOPEN')
        self.assertEqual(extended.next_round.total_deadline, extended.next_round.deadline)
        self.assertGreater(extended.next_round.total_deadline, twenty_left.total_deadline)

    def test_exact_tie_always_reopens_even_after_total_deadline(self):
        window = self.round(total=30)
        candidates = [candidate(1, at=T0), candidate(2, at=T0)]
        result = self.evaluate(window, candidates)
        self.assertEqual((result.action, result.tied_ids), ('REOPEN', (1, 2)))
        self.assertTrue(result.next_round.simultaneous_visibility)
        self.assertEqual(result.next_round.total_deadline, result.next_round.deadline)
        # Old candidates do not automatically carry into a restarted round.
        reopened = result.next_round
        after = self.evaluate(reopened, candidates, at=reopened.deadline+timedelta(microseconds=1))
        self.assertEqual(after.action, 'CANCEL')

    def test_old_missing_time_loses_tie_but_can_win_without_comparison(self):
        window = self.round()
        old = candidate(1, at=None, historical=True)
        other = candidate(2, at=T0)
        result = self.evaluate(window, [old, other], eligibility={2:True})
        self.assertEqual((result.action, result.winner_id, result.discarded_missing_time), ('ASSIGN', 2, (1,)))
        self.assertEqual(self.evaluate(window, [old], eligibility={1:True}).winner_id, 1)

    def test_invalid_times_and_nonboolean_eligibility_are_rejected(self):
        with self.assertRaises(ValueError): first_round(opened_at=T0, duration_seconds=0, original_total_deadline=T0)
        with self.assertRaises(ValueError): first_round(opened_at=T0.replace(tzinfo=None), duration_seconds=30, original_total_deadline=T0)
        window = self.round()
        with self.assertRaises(ValueError): self.evaluate(window, [candidate(1, at=T0)], eligibility={1:1})
        with self.assertRaises(ValueError): self.evaluate(window, watermark=window.deadline+timedelta(seconds=1))
