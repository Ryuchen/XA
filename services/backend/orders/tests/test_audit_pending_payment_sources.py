import json
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.direct_payment_models import DirectPaymentIntent
from orders.models import Order, OrderRefund
from orders.tests.factories import make_order, make_service, make_service_category, make_user, make_wallet
from wallet.models import Transaction


class AuditPendingPaymentSourcesTest(TestCase):
    def setUp(self):
        self.boss = make_user()
        self.account = get_or_create_account_for_legacy_user(self.boss)
        self.wallet = make_wallet(self.boss, balance=1000)
        self.wallet.account = self.account
        self.wallet.save(update_fields=['account'])
        self.service = make_service(price=100)

    def order(self, amount=100, service=None):
        return make_order(self.boss, service or self.service, status='COMPLETED',
                          payment_status='PAID', customer_account=self.account, amount=amount)

    def pay(self, order, amount=None, wallet=None, **kw):
        amount = -order.amount if amount is None else amount
        return Transaction.objects.create(order=order, wallet=wallet or self.wallet, tx_type='PAY',
                                          amount=amount, balance_before=1000,
                                          balance_after=1000 + amount, **kw)

    def run_audit(self, **kw):
        out = StringIO()
        call_command('audit_pending_payment_sources', stdout=out, **kw)
        return json.loads(out.getvalue())

    def test_categories_and_read_only_output(self):
        good, zero, missing = self.order(), self.order(0), self.order()
        self.pay(good)
        self.pay(zero)
        duplicate = self.order()
        self.pay(duplicate)
        self.pay(duplicate)
        wrong_amount = self.order()
        self.pay(wrong_amount, -1)
        wrong_user = self.order()
        self.pay(wrong_user, wallet=make_wallet(make_user()))
        reversed_order = self.order()
        original = self.pay(reversed_order)
        Transaction.objects.create(order=reversed_order, wallet=self.wallet, tx_type='PAY', amount=100,
                                   balance_before=900, balance_after=1000, reversal_of=original)
        direct = self.order()
        DirectPaymentIntent.objects.create(order=direct, account=self.account, client_request_id='audit',
            request_fingerprint='x', merchant_order_no='audit', appid='app', mchid='merchant',
            openid='open', amount_fen=100)
        gift = self.order(service=make_service(service_category=make_service_category(is_gift=True)))
        self.assertTrue(gift.is_gift_snapshot)
        with CaptureQueriesContext(connection) as captured:
            result = self.run_audit()
        self.assertTrue(all(q['sql'].lstrip().upper().startswith('SELECT') for q in captured.captured_queries))
        self.assertEqual(result['scanned'], 9)
        self.assertEqual(result['counts']['balance_candidates'], 7)
        self.assertEqual(result['counts']['zero_amount'], 1)
        self.assertEqual(result['counts']['no_issue_detected'], 2)
        self.assertEqual(result['counts']['needs_review'], 5)
        self.assertEqual(result['counts']['direct_excluded'], 1)
        self.assertEqual(result['counts']['gift_excluded'], 1)
        for reason in ('missing_pay', 'multiple_pay', 'amount_mismatch', 'wallet_user_mismatch', 'pay_reversal'):
            self.assertGreaterEqual(result['review_reasons'].get(reason, 0), 1)
        self.assertNotIn(self.boss.username, json.dumps(result))
        self.assertNotIn(missing.order_no, json.dumps(result))
        self.assertIn('非一致性快照', result['notice'])
        self.assertNotIn('review_orders', result)

    def test_batches_and_invalid_arguments(self):
        for _ in range(3):
            self.order()
        first = self.run_audit(limit=2)
        second = self.run_audit(limit=2, after_id=first['next_after_id'])
        self.assertEqual((first['scanned'], first['has_more']), (2, True))
        self.assertEqual((second['scanned'], second['has_more']), (1, False))
        with self.assertRaises(CommandError):
            self.run_audit(limit=5001)
        with self.assertRaises(CommandError):
            self.run_audit(after_id=-1)

    def test_opt_in_review_ids_are_limited_to_one_batch(self):
        good = self.order()
        self.pay(good)
        missing = self.order()
        self.order()
        result = self.run_audit(limit=2, include_review_ids=True)
        self.assertEqual(result['review_orders'], [{'order_id': missing.pk, 'reasons': ['missing_pay']}])
        self.assertTrue(result['has_more'])
        self.assertIn('权限受控', result['review_orders_notice'])
        self.assertNotIn(self.boss.username, json.dumps(result))
        self.assertNotIn(missing.order_no, json.dumps(result))

    def test_additional_review_reasons_and_historical_gift(self):
        account_mismatch = self.order()
        other_account = get_or_create_account_for_legacy_user(make_user())
        Order.objects.filter(pk=account_mismatch.pk).update(customer_account=other_account)
        self.pay(account_mismatch)
        unsuccessful = self.order()
        self.pay(unsuccessful, status='FAILED')
        arithmetic = self.order()
        bad = self.pay(arithmetic)
        Transaction.objects.filter(pk=bad.pk).update(balance_after=123)
        refunded = self.order()
        self.pay(refunded)
        OrderRefund.objects.create(order=refunded, amount=10, reason='test')
        gift = self.order(service=make_service(service_category=make_service_category(is_gift=True)))
        Order.objects.filter(pk=gift.pk).update(is_gift_snapshot=False)
        result = self.run_audit()
        self.assertEqual(result['counts']['gift_excluded'], 1)
        self.assertEqual(result['counts']['needs_review'], 4)
        for reason in ('wallet_account_mismatch', 'pay_not_success', 'balance_arithmetic_mismatch', 'refund_record_present'):
            self.assertEqual(result['review_reasons'][reason], 1)
