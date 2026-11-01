import unittest

from orders.payment_fee_quote import (
    MAX_FEN, PAYMENT_TOTAL, ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, SERVICE_AMOUNT,
    PaymentFeeQuoteError, quote_payment_fee,
)


class PaymentFeeQuoteTest(unittest.TestCase):
    def quote(self, amount, numerator, denominator, basis, rounding):
        return quote_payment_fee(service_fen=amount, rate_numerator=numerator,
                                 rate_denominator=denominator, fee_basis=basis, rounding=rounding)

    def test_fee_on_service_and_total_are_distinct(self):
        service_basis = self.quote(10000, 6, 1000, SERVICE_AMOUNT, ROUND_UP)
        total_basis = self.quote(10000, 6, 1000, PAYMENT_TOTAL, ROUND_UP)
        self.assertEqual((service_basis.service_fen, service_basis.estimated_fee_fen, service_basis.payment_total_fen),
                         (10000, 60, 10060))
        self.assertEqual((total_basis.service_fen, total_basis.estimated_fee_fen, total_basis.payment_total_fen),
                         (10000, 61, 10061))
        self.assertEqual(self.quote(10000, 6, 1000, PAYMENT_TOTAL, ROUND_HALF_UP).payment_total_fen, 10060)

    def test_explicit_rounding_and_zero_rate(self):
        self.assertEqual(self.quote(1, 1, 2, SERVICE_AMOUNT, ROUND_DOWN).estimated_fee_fen, 0)
        self.assertEqual(self.quote(1, 1, 2, SERVICE_AMOUNT, ROUND_UP).estimated_fee_fen, 1)
        self.assertEqual(self.quote(1, 1, 2, SERVICE_AMOUNT, ROUND_HALF_UP).estimated_fee_fen, 1)
        for basis in (SERVICE_AMOUNT, PAYMENT_TOTAL):
            for rounding in (ROUND_DOWN, ROUND_UP, ROUND_HALF_UP):
                self.assertEqual(self.quote(12345, 0, 7, basis, rounding).payment_total_fen, 12345)

    def test_selects_smallest_fixed_point_when_there_are_several(self):
        # Both 1 and 2 satisfy ceil((1 + fee) / 2) = fee.
        quote = self.quote(1, 1, 2, PAYMENT_TOTAL, ROUND_UP)
        self.assertEqual((quote.estimated_fee_fen, quote.payment_total_fen), (1, 2))

    def test_small_cases_match_independent_brute_force_minimum(self):
        def rounded(product, denominator, rounding):
            quotient, remainder = divmod(product, denominator)
            if rounding == ROUND_UP:
                return quotient + (remainder > 0)
            if rounding == ROUND_HALF_UP:
                return quotient + (2 * remainder >= denominator)
            return quotient
        for service in range(1, 21):
            for denominator in range(1, 11):
                for numerator in range(denominator):
                    for rounding in (ROUND_DOWN, ROUND_UP, ROUND_HALF_UP):
                        expected = next(fee for fee in range(500)
                                        if rounded((service + fee) * numerator, denominator, rounding) == fee)
                        result = self.quote(service, numerator, denominator, PAYMENT_TOTAL, rounding)
                        self.assertEqual(result.estimated_fee_fen, expected,
                                         (service, numerator, denominator, rounding))
                        self.assertEqual(result.payment_total_fen, service + expected)

    def test_overflow_and_100_percent_edges(self):
        self.assertEqual(self.quote(MAX_FEN, 0, 100, PAYMENT_TOTAL, ROUND_UP).payment_total_fen, MAX_FEN)
        self.assertEqual(self.quote(100, 1, 1, SERVICE_AMOUNT, ROUND_HALF_UP).payment_total_fen, 200)
        for args in ((MAX_FEN, 1, 100, SERVICE_AMOUNT, ROUND_UP),
                     (MAX_FEN, 1, 100, PAYMENT_TOTAL, ROUND_UP),
                     (1, 1, 1, PAYMENT_TOTAL, ROUND_DOWN)):
            with self.subTest(args=args), self.assertRaises(PaymentFeeQuoteError):
                self.quote(*args)
        with self.assertRaises(PaymentFeeQuoteError):
            self.quote(MAX_FEN // 2 + 1, 1, 1, SERVICE_AMOUNT, ROUND_DOWN)

    def test_invalid_types_rates_and_no_default_policy(self):
        cases = ((True, 1, 100, SERVICE_AMOUNT, ROUND_UP), (0, 1, 100, SERVICE_AMOUNT, ROUND_UP),
                 (-1, 1, 100, SERVICE_AMOUNT, ROUND_UP), (MAX_FEN+1, 1, 100, SERVICE_AMOUNT, ROUND_UP),
                 (100, True, 100, SERVICE_AMOUNT, ROUND_UP), (100, -1, 100, SERVICE_AMOUNT, ROUND_UP),
                 (100, 1, 0, SERVICE_AMOUNT, ROUND_UP), (100, 101, 100, SERVICE_AMOUNT, ROUND_UP),
                 (100, 1, MAX_FEN+1, SERVICE_AMOUNT, ROUND_UP),
                 (100, 1, 100, 'UNKNOWN', ROUND_UP), (100, 1, 100, SERVICE_AMOUNT, 'UNKNOWN'))
        for args in cases:
            with self.subTest(args=args), self.assertRaises(PaymentFeeQuoteError):
                self.quote(*args)
        with self.assertRaises(TypeError):
            quote_payment_fee(service_fen=100, rate_numerator=1, rate_denominator=100)
