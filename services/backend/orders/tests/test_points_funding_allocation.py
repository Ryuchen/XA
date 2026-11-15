from django.test import SimpleTestCase

from orders.points_funding_allocation import (PointsFundingAllocationError, plan_allocation,
                                              plan_shared_receipt_allocation, order_funding_is_complete, plan_release,
                                              plan_rebalance)


class PointsFundingAllocationTests(SimpleTestCase):
    def test_rebalance_keeps_prior_releases_and_returns_only_actual_excess(self):
        result = plan_rebalance(allocations=[(1, 150, 0), (2, 350, 50)], required=200)
        self.assertEqual((result.allocated_before, result.allocated_after, result.remaining), (450, 200, 0))
        self.assertEqual([(part.allocation_id, part.amount) for part in result.release_parts], [(2, 250)])
        next_revision = plan_rebalance(allocations=[(1, 150, 0), (2, 350, 300)], required=300)
        self.assertEqual((next_revision.allocated_before, next_revision.allocated_after, next_revision.remaining),
                         (200, 200, 100))
        self.assertEqual(next_revision.release_parts, ())
        for rows, required in (([(1, 20, 21)], 0), ([(1, 10, 0), (1, 1, 0)], 0), ([], True), ([], -1)):
            with self.subTest(rows=rows, required=required), self.assertRaises(PointsFundingAllocationError):
                plan_rebalance(allocations=rows, required=required)

    def test_release_uses_recorded_allocations_and_never_exceeds_unreleased_amounts(self):
        rows = [(1, 150, 0), (2, 350, 50)]
        parts = plan_release(allocations=rows, amount=400)
        self.assertEqual([(item.allocation_id, item.amount) for item in parts], [(2, 300), (1, 100)])
        self.assertEqual(plan_release(allocations=rows, amount=0), [])
        for invalid, amount in ((rows, 451), ([(1, 150, 151)], 1),
                                ([(1, 150, 0), (1, 100, 0)], 1), (rows, True)):
            with self.subTest(invalid=invalid, amount=amount), self.assertRaises(PointsFundingAllocationError):
                plan_release(allocations=invalid, amount=amount)

    def test_no_provider_is_funded_until_the_whole_order_shortfall_is_covered(self):
        self.assertFalse(order_funding_is_complete(order_required=1000, order_allocated=500))
        self.assertTrue(order_funding_is_complete(order_required=1000, order_allocated=1000))
        self.assertTrue(order_funding_is_complete(order_required=0, order_allocated=0))
        for allocated in (1001, -1, True):
            with self.subTest(allocated=allocated), self.assertRaises(PointsFundingAllocationError):
                order_funding_is_complete(order_required=1000, order_allocated=allocated)

    def test_both_purposes_consume_the_same_receipt_without_inventing_money(self):
        args = dict(receipt_amount=1000, pass_allocated=400, points_allocated=200,
                    order_required=500, order_allocated=200, amount=300)
        result = plan_shared_receipt_allocation(**args)
        self.assertEqual((result.receipt_remaining, result.order_remaining), (100, 0))
        with self.assertRaises(PointsFundingAllocationError):
            plan_shared_receipt_allocation(**{**args, 'amount': 401, 'order_required': 1000})
        with self.assertRaises(PointsFundingAllocationError):
            plan_shared_receipt_allocation(**{**args, 'pass_allocated': True})

    def test_one_receipt_partially_funds_two_orders_with_remaining_balance(self):
        first = plan_allocation(receipt_amount=1000, receipt_allocated=0,
                                order_required=600, order_allocated=0, amount=300)
        self.assertEqual((first.receipt_remaining, first.order_remaining), (700, 300))
        second = plan_allocation(receipt_amount=1000, receipt_allocated=300,
                                 order_required=500, order_allocated=0, amount=500)
        self.assertEqual((second.receipt_remaining, second.order_remaining), (200, 0))
        third = plan_allocation(receipt_amount=1000, receipt_allocated=800,
                                order_required=600, order_allocated=300, amount=200)
        self.assertEqual((third.receipt_remaining, third.order_remaining), (0, 100))

    def test_allocation_cannot_exceed_receipt_or_order_and_rejects_bad_snapshots(self):
        base = dict(receipt_amount=100, receipt_allocated=50, order_required=40, order_allocated=10, amount=1)
        invalid = ({'amount': 51}, {'amount': 31}, {'amount': 0}, {'amount': True},
                   {'receipt_allocated': 101}, {'order_allocated': 41}, {'receipt_amount': 0},
                   {'order_required': -1}, {'amount': 1.0}, {'receipt_amount': 2147483648})
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(PointsFundingAllocationError):
                plan_allocation(**{**base, **change})

    def test_every_valid_allocation_conserves_receipt_and_order_amounts(self):
        for receipt in (1, 10, 101):
            for required in (1, 10, 101):
                for used in (0, receipt // 2):
                    for funded in (0, required // 2):
                        amount = min(receipt - used, required - funded)
                        result = plan_allocation(receipt_amount=receipt, receipt_allocated=used,
                            order_required=required, order_allocated=funded, amount=amount)
                        self.assertEqual(used + amount + result.receipt_remaining, receipt)
                        self.assertEqual(funded + amount + result.order_remaining, required)
