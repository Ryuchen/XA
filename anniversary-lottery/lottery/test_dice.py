from collections import Counter
from fractions import Fraction
from itertools import product
from unittest import TestCase
from unittest.mock import call, patch

from .catalog import NAMES
from .dice import ALGORITHM, DENOMINATOR, compute_weights, face_for_ticket, roll_dice


class FairIndependentDiceTests(TestCase):
    def test_each_face_has_one_of_exactly_six_tickets(self):
        self.assertEqual(DENOMINATOR, 6)
        self.assertEqual([face_for_ticket(ticket) for ticket in range(6)], [1, 2, 3, 4, 5, 6])

    def test_cartesian_ticket_space_has_36_equally_likely_independent_outcomes(self):
        for pool in ['small']:
            outcomes = Counter()
            for row_ticket, column_ticket in product(range(6), repeat=2):
                with patch('lottery.dice.secrets.randbelow', side_effect=[row_ticket, column_ticket]) as secure:
                    result = roll_dice(pool, {'total_rolls': 149, 'ordinary_dry_streak': 999})
                self.assertEqual(secure.call_args_list, [call(6), call(6)])
                outcomes[result['row'], result['column']] += 1
                self.assertEqual(result['algorithm'], ALGORITHM)
                self.assertEqual(result['denominator'], 6)
                self.assertEqual(result['row_weights'], [1] * 6)
                self.assertEqual(result['column_weights'], [1] * 6)
            self.assertEqual(set(outcomes), set(product(range(1, 7), repeat=2)))
            self.assertEqual(set(outcomes.values()), {1})
            for face in range(1, 7):
                self.assertEqual(sum(value for (row, _), value in outcomes.items() if row == face), 6)
                self.assertEqual(sum(value for (_, column), value in outcomes.items() if column == face), 6)
            self.assertEqual(Fraction(1, 36), Fraction(1, 6) * Fraction(1, 6))

    def test_two_separate_secure_draws_allow_same_ticket_without_resampling(self):
        for tickets in [[0, 5], [5, 0], [0, 0], [5, 5]]:
            with patch('lottery.dice.secrets.randbelow', side_effect=tickets) as secure:
                result = roll_dice('small')
            self.assertEqual(secure.call_args_list, [call(6), call(6)])
            self.assertEqual((result['row'], result['column']), (tickets[0] + 1, tickets[1] + 1))

    def test_history_and_cash_pity_progress_never_change_weights_or_faces(self):
        for pool in NAMES:
            for counters in [{}, {'total_rolls': 29}, {'total_rolls': 49},
                             {'total_rolls': 150, 'ordinary_dry_streak': 100000}]:
                self.assertEqual(compute_weights(pool, counters), ((1,) * 6, (1,) * 6))
                with patch('lottery.dice.secrets.randbelow', side_effect=[2, 4]):
                    result = roll_dice(pool, counters)
                self.assertEqual((result['row'], result['column']), (3, 5))
                self.assertNotIn('phase', result)
                self.assertNotIn('ordinary_dry_streak', result)

    def test_unknown_pool_fails_before_random_sampling(self):
        with patch('lottery.dice.secrets.randbelow') as secure:
            with self.assertRaises(ValueError):
                roll_dice('unknown')
        secure.assert_not_called()

    def test_invalid_ticket_is_rejected(self):
        for value in [-1, 6, True, 1.5, '1']:
            with self.assertRaises(ValueError):
                face_for_ticket(value)
