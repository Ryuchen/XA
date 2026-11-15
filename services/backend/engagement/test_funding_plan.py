from django.test import SimpleTestCase, TestCase

from .funding_plan import AvailablePart, FundingPlanError, RefundablePart, plan_payment, plan_refund


class FundingPlanTest(SimpleTestCase):
    def setUp(self):
        # A mixed historic recharge can expose both kinds under the same ID.
        self.parts = [AvailablePart(1, 'PREPAID', 700), AvailablePart(1, 'GIFT', 300),
                      AvailablePart(2, 'PREPAID', 400), AvailablePart(3, 'COMPENSATION', 200)]

    def test_default_only_uses_principal_even_when_gifts_exist(self):
        plan = plan_payment(self.parts, 800)
        self.assertEqual([(x.lot_id, x.kind, x.amount) for x in plan],
                         [(1, 'PREPAID', 700), (2, 'PREPAID', 100)])
        with self.assertRaisesRegex(FundingPlanError, '本金不足'):
            plan_payment(self.parts, 1200)

    def test_selected_sources_and_multiple_lots_keep_exact_origin(self):
        plan = plan_payment(self.parts, 1200, gift=200, compensation=100)
        self.assertEqual([(x.lot_id, x.kind, x.amount) for x in plan],
                         [(1, 'PREPAID', 700), (2, 'PREPAID', 200),
                          (1, 'GIFT', 200), (3, 'COMPENSATION', 100)])

    def test_rejects_invalid_selection_and_unknown_source(self):
        for kwargs in ({'gift': 310}, {'compensation': 210}, {'gift': 101},
                       {'gift': True}, {'gift': 800, 'compensation': 410}):
            with self.subTest(kwargs=kwargs), self.assertRaises(FundingPlanError):
                plan_payment(self.parts, 1000, **kwargs)
        with self.assertRaises(FundingPlanError):
            plan_payment(self.parts + [AvailablePart(1, 'GIFT', 1)], 100)
        with self.assertRaises(FundingPlanError):
            plan_payment([AvailablePart(1, 'UNKNOWN', 100)], 100)

    def test_refunds_prioritize_principal_across_partial_requests(self):
        rows = [RefundablePart(1, 'PREPAID', 500), RefundablePart(2, 'GIFT', 300),
                RefundablePart(3, 'COMPENSATION', 200)]
        self.assertEqual(plan_refund(rows, 700), {1: 500, 2: 200, 3: 0})
        next_rows = [RefundablePart(1, 'PREPAID', 500, 500),
                     RefundablePart(2, 'GIFT', 300, 200), RefundablePart(3, 'COMPENSATION', 200)]
        self.assertEqual(plan_refund(next_rows, 300), {1: 0, 2: 100, 3: 200})
        with self.assertRaises(FundingPlanError):
            plan_refund(next_rows, 310)

    def test_historical_internal_unit_boundary_and_invalid_history(self):
        rows = [RefundablePart(1, 'PREPAID', 495), RefundablePart(2, 'GIFT', 505)]
        self.assertEqual(plan_refund(rows, 500), {1: 495, 2: 5})
        with self.assertRaisesRegex(FundingPlanError, '顺序'):
            plan_refund([RefundablePart(1, 'PREPAID', 500), RefundablePart(2, 'GIFT', 500, 10)], 10)


class FundingAllocationConstraintTest(TestCase):
    def test_mixed_lot_can_record_two_sources_but_cannot_over_refund(self):
        from django.db import IntegrityError, transaction
        from club_accounts.services import get_or_create_account_for_legacy_user
        from engagement.models import OrderFundingAllocation, PointFundingLot
        from orders.tests.factories import make_order, make_service, make_user
        boss = make_user()
        account = get_or_create_account_for_legacy_user(boss)
        order = make_order(boss, make_service(), customer_account=account)
        lot = PointFundingLot.objects.create(account=account, source_key='funding-constraint-mixed',
                                              kind='RECHARGE', remaining=1000)
        principal = OrderFundingAllocation.objects.create(order=order, lot=lot, kind='PREPAID', amount=700)
        OrderFundingAllocation.objects.create(order=order, lot=lot, kind='GIFT', amount=300)
        with self.assertRaises(IntegrityError), transaction.atomic():
            OrderFundingAllocation.objects.create(order=order, lot=lot, kind='GIFT', amount=100)
        with self.assertRaises(IntegrityError), transaction.atomic():
            principal.refunded_amount = 701
            principal.save(update_fields=['refunded_amount'])
        with self.assertRaises(IntegrityError), transaction.atomic():
            OrderFundingAllocation.objects.create(order=order, lot=lot, kind='UNKNOWN', amount=100)
