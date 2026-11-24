from datetime import datetime, time, timedelta
import os
from unittest import skipUnless

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from console.payroll_refund_performance import monthly_payment_refund_events
from orders.models import OrderAddonRefund
from orders.tests import test_addon_refund_execution
from wallet.models import Transaction


@override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class PayrollAddonPaymentMonthTest(TestCase):
    @skipUnless(os.environ.get('XA_PAYROLL_ADDON_PAYMENT_MONTH_FORMAL') == '1',
                '加购付款月度归属口径待确认：本用例按实际借记账流水时间（buyer_transaction.created_at）断言，'
                '现行 addon_payment_time 返回持久化 paid_at（跨月边界两者不一致）；涉及资金口径，交用户裁定后启用')
    def test_actual_debit_and_refund_are_counted_in_their_own_months(self):
        flow = test_addon_refund_execution.AddonRefundExecutionTests(
            methodName='test_confirmed_refunds_restore_original_sources_and_deduct_pending_earnings_once')
        flow.setUp()
        addon = flow.fixture.payment
        order = flow.fixture.fixture.order
        main = Transaction.objects.create(wallet=flow.fixture.wallet, order=order, tx_type='PAY',
            amount=-order.amount, balance_before=order.amount, balance_after=0)
        month = timezone.localdate().replace(day=1)
        previous = (month - timedelta(days=1)).replace(day=1)
        following = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
        boundary = timezone.make_aware(datetime.combine(month, time.min))
        Transaction.objects.filter(pk=main.pk).update(created_at=boundary - timedelta(hours=1))
        addon.paid_at = boundary - timedelta(microseconds=1)
        addon.save(update_fields=['paid_at'])
        Transaction.objects.filter(pk=addon.buyer_transaction_id).update(created_at=boundary + timedelta(microseconds=1))
        execute_url = flow.approve('payroll-addon-boundary', 500)
        response = flow.fixture.staff.post(execute_url, {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        refund = OrderAddonRefund.objects.get(payment=addon)
        refund_time = timezone.make_aware(datetime.combine(following, time.min))
        OrderAddonRefund.objects.filter(pk=refund.pk).update(created_at=refund_time)
        old = monthly_payment_refund_events(previous)
        current = monthly_payment_refund_events(month)
        later = monthly_payment_refund_events(following)
        self.assertFalse([x for x in old if x['origin'] == 'ADDON'])
        payment_event = [x for x in current if x['origin'] == 'ADDON']
        self.assertEqual(len(payment_event), 1)
        self.assertEqual((payment_event[0]['kind'], payment_event[0]['amount_fen'], payment_event[0]['occurred_at']),
                         ('PAYMENT', addon.intent.amount, boundary + timedelta(microseconds=1)))
        refund_event = [x for x in later if x['origin'] == 'ADDON']
        self.assertEqual([(x['kind'], x['amount_fen'], x['occurred_at']) for x in refund_event],
                         [('REFUND', -500, refund_time)])
        Transaction.objects.filter(pk=addon.buyer_transaction_id).update(amount=-addon.intent.amount + 1)
        with self.assertRaises(ValidationError):
            monthly_payment_refund_events(month)
        with self.assertRaises(ValidationError):
            monthly_payment_refund_events(following)
