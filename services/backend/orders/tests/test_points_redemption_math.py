from django.test import SimpleTestCase

from orders.points_redemption_math import PointsRedemptionError, quote_points_redemption
from orders.refund_math import payment_refund


class PointsRedemptionMathTests(SimpleTestCase):
    def quote(self, **changes):
        values = dict(payable_before_points_units=1000, available_point_hundredths=100000,
                      selected_point_hundredths=15000, group_point_hundredths=10000,
                      group_coin_units=10, max_ratio_basis_points=5000)
        values.update(changes)
        return quote_points_redemption(**values)

    def test_only_whole_configured_groups_are_used(self):
        result = self.quote()
        self.assertEqual((result.points_hundredths, result.discount_units, result.payable_units),
                         (10000, 10, 990))
        result = self.quote(selected_point_hundredths=3400, group_coin_units=30)
        self.assertEqual((result.selected_groups, result.points_hundredths, result.discount_units), (0, 0, 0))
        result = self.quote(selected_point_hundredths=100, group_coin_units=2000,
                            payable_before_points_units=10000)
        self.assertEqual(result.selected_groups, 0)  # no ratio reduction to 1 point -> 2 coins

    def test_balance_and_ratio_independently_limit_groups(self):
        result = self.quote(available_point_hundredths=25000, selected_point_hundredths=100000,
                            group_coin_units=300, max_ratio_basis_points=8000)
        self.assertEqual((result.max_groups, result.selected_groups, result.discount_units), (2, 2, 600))
        result = self.quote(available_point_hundredths=100000, selected_point_hundredths=100000,
                            group_coin_units=300, max_ratio_basis_points=5000)
        self.assertEqual((result.max_groups, result.selected_groups, result.discount_units), (1, 1, 300))

    def test_negative_balance_and_zero_price_cannot_spend_points(self):
        self.assertEqual(self.quote(available_point_hundredths=-1).selected_groups, 0)
        result = self.quote(payable_before_points_units=0)
        self.assertEqual((result.points_hundredths, result.payable_units), (0, 0))
        self.assertEqual(self.quote(payable_before_points_units=10, max_ratio_basis_points=9999).discount_units, 0)

    def test_invalid_values_are_rejected_without_float_or_bool_coercion(self):
        for changes in ({'max_ratio_basis_points': 10000}, {'max_ratio_basis_points': -1},
                        {'group_coin_units': 11}, {'group_point_hundredths': 0},
                        {'selected_point_hundredths': -1}, {'payable_before_points_units': 1},
                        {'available_point_hundredths': True}, {'max_ratio_basis_points': 0.5}):
            with self.subTest(changes=changes), self.assertRaises(PointsRedemptionError):
                self.quote(**changes)

    def test_partial_refund_does_not_require_returned_points_to_be_a_group(self):
        quote = self.quote(payable_before_points_units=2000, group_coin_units=300,
                           selected_point_hundredths=10000)
        refund = payment_refund(paid_coin_units=quote.payable_units, paid_point_hundredths=quote.points_hundredths,
                                point_value_units=quote.discount_units, refund_equivalent_units=50)
        self.assertEqual((refund.coin_units, refund.point_hundredths), (40, 250))

    def test_max_group_boundaries_against_enumeration(self):
        for amount in (0, 10, 20, 90, 100, 250):
            for ratio in (0, 1, 4999, 5000, 9999):
                for group in (10, 30, 100):
                    result = self.quote(payable_before_points_units=amount, max_ratio_basis_points=ratio,
                                        group_coin_units=group, selected_point_hundredths=100000)
                    possible = [n for n in range(11) if n * group <= amount * ratio // 10000
                                and (n == 0 or amount - n * group >= 10)]
                    self.assertEqual(result.max_groups, max(possible))
