from unittest import TestCase

from orders.original_share_rules import agreed_transfer, initial_shares


class OriginalShareRulesTest(TestCase):
    def test_single_and_even_team(self):
        self.assertEqual(initial_shares(10000, (1,)).shares, ((1, 10000),))
        result = initial_shares(10000, (1, 2))
        self.assertEqual((result.shares, result.rounding_surplus), (((1, 5000), (2, 5000)), 0))

    def test_odd_team_rounds_each_up_and_reports_extra_separately(self):
        result = initial_shares(1010, (2, 1))
        self.assertEqual(result.shares, ((2, 510), (1, 510)))
        self.assertEqual(result.rounding_surplus, 10)

    def test_invalid_original_or_team_is_rejected(self):
        for amount, members in ((1001, (1, 2)), (-10, (1,)), (True, (1,)), (1000, (1, 1)),
                                (1000, ()), (1000, (1, 2, 3)), (1000, (True,))):
            with self.subTest(amount=amount, members=members), self.assertRaises(ValueError):
                initial_shares(amount, members)

    def test_agreed_transfer_keeps_slot_total_and_separate_segments(self):
        self.assertEqual(agreed_transfer(10000, ((10, 4000), (20, 6000))), ((10, 4000), (20, 6000)))
        # A provider may later re-enter, but each participation segment is distinct.
        self.assertEqual(agreed_transfer(10000, ((10, 2000), (20, 4000), (30, 4000))),
                         ((10, 2000), (20, 4000), (30, 4000)))
        with self.assertRaises(ValueError): agreed_transfer(10000, ((10, 4000), (20, 5990)))
        with self.assertRaises(ValueError): agreed_transfer(10000, ((10, 4000), (10, 6000)))
        with self.assertRaises(ValueError): agreed_transfer(10000, ((10, 4000), (20, -1)))
