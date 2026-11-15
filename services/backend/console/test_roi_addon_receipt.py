from datetime import datetime, time, timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from orders.tests import test_addon_refund_execution
from orders.tests.test_addon_configuration import staff_client
from wallet.models import Transaction


@override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class AddonRefundRevenueReceiptTest(TestCase):
    def test_addon_revenue_and_cost_use_successful_debit_across_month_boundary(self):
        from console.roi_period import _verified_addon_costs, period_revenue
        flow = test_addon_refund_execution.AddonRefundExecutionTests(
            methodName='test_confirmed_refunds_restore_original_sources_and_deduct_pending_earnings_once')
        flow.setUp()
        addon = flow.fixture.payment
        order = flow.fixture.fixture.order
        main = Transaction.objects.create(wallet=flow.fixture.wallet, order=order, tx_type='PAY',
            amount=-order.amount, balance_before=order.amount, balance_after=0)
        next_month = timezone.localdate().replace(day=1)
        boundary = timezone.make_aware(datetime.combine(next_month, time.min))
        Transaction.objects.filter(pk=main.pk).update(created_at=boundary - timedelta(hours=1))
        addon.paid_at = boundary - timedelta(microseconds=1)
        addon.save(update_fields=['paid_at'])
        Transaction.objects.filter(pk=addon.buyer_transaction_id).update(created_at=boundary + timedelta(microseconds=1))
        addon.refresh_from_db()
        previous = period_revenue(boundary - timedelta(days=1), boundary)
        current = period_revenue(boundary, boundary + timedelta(days=1))
        self.assertEqual(previous['paid_units'], order.amount)
        self.assertEqual(current['paid_units'], addon.intent.amount)
        previous_cost = _verified_addon_costs([addon], boundary - timedelta(days=1), boundary)
        current_cost = _verified_addon_costs([addon], boundary, boundary + timedelta(days=1))
        self.assertEqual(previous_cost, {'provider': 0, 'gift_compensation': 0})
        self.assertEqual(current_cost, {'provider': addon.provider_income,
            'gift_compensation': addon.intent.gift + addon.intent.compensation})
        Transaction.objects.filter(pk=addon.buyer_transaction_id).update(amount=-addon.intent.amount + 1)
        self.assertIsNone(period_revenue(boundary, boundary + timedelta(days=1))['paid_units'])

    def test_refund_period_requires_original_payment_receipt(self):
        flow = test_addon_refund_execution.AddonRefundExecutionTests(
            methodName='test_confirmed_refunds_restore_original_sources_and_deduct_pending_earnings_once')
        flow.setUp()
        addon = flow.fixture.payment
        order = flow.fixture.fixture.order
        main = Transaction.objects.create(wallet=flow.fixture.wallet, order=order, tx_type='PAY',
            amount=-order.amount, balance_before=order.amount, balance_after=0)
        today = timezone.localdate()
        previous = today.replace(day=1) - timedelta(days=1)
        earlier = timezone.make_aware(datetime.combine(previous, time(hour=10)))
        Transaction.objects.filter(pk=main.pk).update(created_at=earlier)
        addon.paid_at = earlier
        addon.save(update_fields=['paid_at'])
        Transaction.objects.filter(pk=addon.buyer_transaction_id).update(created_at=earlier)
        url = flow.approve('roi-addon-receipt-refund', 500)
        response = flow.fixture.staff.post(url, {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        finance = staff_client(['roi:view'])
        params = {'start_date': today.isoformat(), 'end_date': today.isoformat()}
        good = finance.get('/api/admin/operating-roi/', params)
        self.assertEqual(good.status_code, 200, good.data)
        self.assertEqual(good.data['data']['roas']['net_consumption_fen'], -500)

        receipt = addon.buyer_transaction
        Transaction.objects.filter(pk=receipt.pk).update(amount=receipt.amount + 1)
        disputed = finance.get('/api/admin/operating-roi/', params)
        self.assertEqual(disputed.status_code, 200, disputed.data)
        self.assertIsNone(disputed.data['data']['roas']['net_consumption_fen'])
        self.assertIn(order.pk, disputed.data['data']['operating']['revenue_review_order_ids'])

        Transaction.objects.filter(pk=receipt.pk).update(amount=receipt.amount)
        addon.paid_at = timezone.now() + timedelta(days=1)
        addon.save(update_fields=['paid_at'])
        disputed = finance.get('/api/admin/operating-roi/', params)
        self.assertIsNone(disputed.data['data']['roas']['net_consumption_fen'])
        self.assertIn(order.pk, disputed.data['data']['operating']['revenue_review_order_ids'])
