from django.test import SimpleTestCase

from orders.refund_math import RefundCalculationError, payment_refund, provider_refund_burden, wallet_source_refund


class RefundPaymentMathTests(SimpleTestCase):
    def test_each_partial_refund_floors_each_payment_asset(self):
        result = payment_refund(paid_coin_units=1700, paid_point_hundredths=30000,
            point_value_units=300, refund_equivalent_units=50)
        self.assertEqual((result.coin_units, result.point_hundredths, result.is_final), (40, 750, False))

    def test_last_full_refund_returns_all_assets_left_after_separate_rounding(self):
        prior_coin = prior_points = 0
        for index in range(3):
            part = payment_refund(paid_coin_units=1700, paid_point_hundredths=30000,
                point_value_units=300, refund_equivalent_units=50,
                previously_refunded_equivalent_units=50 * index,
                previously_refunded_coin_units=prior_coin, previously_refunded_point_hundredths=prior_points)
            prior_coin += part.coin_units
            prior_points += part.point_hundredths
        final = payment_refund(paid_coin_units=1700, paid_point_hundredths=30000, point_value_units=300,
            refund_equivalent_units=1850, previously_refunded_equivalent_units=150,
            previously_refunded_coin_units=prior_coin, previously_refunded_point_hundredths=prior_points)
        self.assertTrue(final.is_final)
        self.assertEqual((prior_coin + final.coin_units, prior_points + final.point_hundredths), (1700, 30000))

    def test_invalid_or_unreconcilable_history_is_rejected(self):
        with self.assertRaises(RefundCalculationError):
            payment_refund(paid_coin_units=1700, paid_point_hundredths=30000, point_value_units=300,
                refund_equivalent_units=50, previously_refunded_coin_units=1710)
        with self.assertRaises(RefundCalculationError):
            payment_refund(paid_coin_units=1700, paid_point_hundredths=0, point_value_units=300,
                           refund_equivalent_units=50)
        with self.assertRaises(RefundCalculationError):
            payment_refund(paid_coin_units=1700, paid_point_hundredths=30000, point_value_units=300,
                           refund_equivalent_units=5)
        with self.assertRaises(RefundCalculationError):
            payment_refund(paid_coin_units=1700, paid_point_hundredths=30000, point_value_units=300,
                refund_equivalent_units=50, previously_refunded_equivalent_units=50,
                previously_refunded_coin_units=50)


class RefundProviderBurdenMathTests(SimpleTestCase):
    def test_two_equal_providers_each_round_up_and_store_receives_difference(self):
        result = provider_refund_burden(refund_equivalent_units=510, original_price_weights={1: 500, 2: 500})
        self.assertEqual(result.amounts, {1: 260, 2: 260})
        self.assertEqual(result.rounding_to_store_units, 10)

    def test_each_refund_rounds_independently(self):
        first = provider_refund_burden(refund_equivalent_units=10, original_price_weights={1: 10, 2: 10})
        second = provider_refund_burden(refund_equivalent_units=10, original_price_weights={1: 10, 2: 10})
        self.assertEqual((first.amounts, second.amounts), ({1: 10, 2: 10}, {1: 10, 2: 10}))
        self.assertEqual(first.rounding_to_store_units + second.rounding_to_store_units, 20)

    def test_specific_provider_and_unequal_original_shares(self):
        target = provider_refund_burden(refund_equivalent_units=510, original_price_weights={8: 200})
        self.assertEqual((target.amounts, target.rounding_to_store_units), ({8: 510}, 0))
        result = provider_refund_burden(refund_equivalent_units=100, original_price_weights={1: 1, 2: 2})
        self.assertEqual((result.amounts, result.rounding_to_store_units), ({1: 40, 2: 70}, 10))

    def test_invalid_scopes_and_fractional_customer_coin_are_rejected(self):
        for weights in ({}, {1: 0}, {1: -2}, {True: 2}):
            with self.subTest(weights=weights), self.assertRaises(RefundCalculationError):
                provider_refund_burden(refund_equivalent_units=10, original_price_weights=weights)
        with self.assertRaises(RefundCalculationError):
            provider_refund_burden(refund_equivalent_units=1, original_price_weights={1: 10})


class RefundWalletSourceMathTests(SimpleTestCase):
    def test_prepaid_then_gift_then_compensation(self):
        first = wallet_source_refund(coin_units=700, paid_prepaid_units=500, paid_gift_units=300,
                                     paid_compensation_units=200)
        self.assertEqual((first.prepaid_units, first.gift_units, first.compensation_units), (500, 200, 0))
        final = wallet_source_refund(coin_units=300, paid_prepaid_units=500, paid_gift_units=300,
            paid_compensation_units=200, refunded_prepaid_units=500, refunded_gift_units=200)
        self.assertEqual((final.prepaid_units, final.gift_units, final.compensation_units), (0, 100, 200))

    def test_rejects_invalid_or_excessive_refund_components(self):
        with self.assertRaises(RefundCalculationError):
            wallet_source_refund(coin_units=10, paid_prepaid_units=100, paid_gift_units=0,
                                 paid_compensation_units=0, refunded_prepaid_units=110)
        with self.assertRaises(RefundCalculationError):
            wallet_source_refund(coin_units=20, paid_prepaid_units=10, paid_gift_units=0,
                                 paid_compensation_units=0)
