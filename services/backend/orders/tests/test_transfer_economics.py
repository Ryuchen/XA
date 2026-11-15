from django.test import SimpleTestCase

from orders.settlement import compute_provider_income
from orders.transfer_economics import (allocate_transfer_basis, allocate_two_party_fixed_commission,
                                       allocate_same_rule_fixed_seat)


class TransferEconomicsTest(SimpleTestCase):
    def test_unchanged_rule_keeps_original_seat_fixed_amount(self):
        self.assertEqual(allocate_same_rule_fixed_seat(600, 3, 2, 100, 200), (0, 2, 0))
        self.assertEqual(allocate_same_rule_fixed_seat(9000, 2000, 2000, 3000, 6000), (666, 1334, 0))
        self.assertEqual(allocate_same_rule_fixed_seat(600, 3, 2, 0, 300), (0, 2, 0))

    def test_unchanged_rule_caps_incoming_and_records_store_shortfall(self):
        self.assertEqual(allocate_same_rule_fixed_seat(10, 7, 4, 4, 1), (2, 1, 1))

    def test_two_party_fixed_commission_uses_each_rule_and_confirmed_rounding(self):
        same = allocate_two_party_fixed_commission(9000, 3000, 6000, 2000, 2000)
        self.assertEqual((same.outgoing, same.incoming), (666, 1334))
        changed = allocate_two_party_fixed_commission(9000, 3000, 6000, 2000, 2030)
        self.assertEqual((changed.outgoing, changed.incoming), (666, 1354))
        self.assertEqual((changed.outgoing_remainder, changed.incoming_remainder), (6000, 3000))
        exact = allocate_two_party_fixed_commission(10000, 4000, 6000, 2000, 3000)
        self.assertEqual((exact.outgoing, exact.incoming), (800, 1800))

    def test_fixed_commission_plan_rejects_unknown_or_non_conserving_basis(self):
        for values in ((0, 0, 0, 1, 1), (100, 40, 59, 20, 20),
                       (100, 40, 60, -1, 20), (True, 1, 0, 1, 1)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                allocate_two_party_fixed_commission(*values)

    def test_confirmed_deal_and_commission_example(self):
        allocation = allocate_transfer_basis(8000, 10000, 4000, 6000)
        self.assertEqual((allocation.retained, allocation.transferred), (3200, 4800))
        self.assertEqual(compute_provider_income(allocation.retained, commission_rate=20), 2560)
        self.assertEqual(compute_provider_income(allocation.transferred, commission_rate=30), 3360)

    def test_fractional_basis_is_floored_for_outgoing_and_remainder_goes_incoming(self):
        allocation = allocate_transfer_basis(1010, 9000, 3000, 6000)
        self.assertEqual((allocation.retained, allocation.transferred), (336, 674))
        self.assertEqual(allocation.remainder_numerator, 6000)
        self.assertEqual(allocation.retained + allocation.transferred, 1010)

    def test_repeat_transfer_splits_only_incoming_basis(self):
        first = allocate_transfer_basis(8000, 10000, 4000, 6000)
        second = allocate_transfer_basis(first.transferred, 6000, 4000, 2000)
        self.assertEqual((first.retained, second.retained, second.transferred), (3200, 3200, 1600))
        self.assertEqual(first.retained + second.retained + second.transferred, 8000)

    def test_invalid_or_unknowable_basis_is_never_guessed(self):
        for values in ((1, 0, 0, 0), (100, 1000, 400, 590), (100, 1001, 400, 601),
                       (True, 1000, 400, 600), (-1, 1000, 400, 600)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                allocate_transfer_basis(*values)
        self.assertEqual(allocate_transfer_basis(0, 0, 0, 0).transferred, 0)

    def test_conservation_across_small_and_large_amounts(self):
        for total in (0, 1, 9, 10, 1010, 999999999):
            for old in range(0, 1010, 10):
                plan = allocate_transfer_basis(total, 1000, old, 1000 - old)
                self.assertEqual(plan.retained + plan.transferred, total)
                self.assertEqual(plan.retained, total * old // 1000)
