from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

from django.db import IntegrityError, close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.direct_payment import VerifiedPayment, confirm_verified_payment, prepare_direct_order
from orders.direct_refund import DirectRefundError, VerifiedRefund, apply_verified_refund, mark_refund_unknown, reserve_full_refund
from orders.direct_refund_models import DirectPaymentRefund
from orders.models import Order
from orders.state_machine import transition
from orders.tests.factories import make_service, make_user, make_wallet
from wallet.models import Transaction, Wallet


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True)
class DirectRefundDomainTest(TestCase):
    def setUp(self):
        self.user = make_user()
        self.account = get_or_create_account_for_legacy_user(self.user)
        self.account.openid = 'test-openid'
        self.account.wechat_appid = 'wx-test'
        self.account.is_openid_bound = True
        self.account.save(update_fields=['openid', 'wechat_appid', 'is_openid_bound'])
        self.wallet = make_wallet(self.user, balance=50000)
        service = make_service(price=10000)
        request = SimpleNamespace(account=self.account, legacy_user=self.user, data={
            'service_id': service.pk, 'expected_amount': 10000,
            'client_request_id': 'direct-refund-payment', 'dispatch_mode': 'AUTO_GRAB',
        })
        self.intent = prepare_direct_order(request, appid='wx-test', mchid='mch-test')

    def pay(self):
        evidence = VerifiedPayment(self.intent.merchant_order_no, 'wx-transaction-1', self.intent.appid,
                                   self.intent.mchid, self.intent.openid, self.intent.amount_fen, 'CNY', timezone.now(),
                                   payer_total_fen=self.intent.amount_fen, payer_currency='CNY')
        self.intent = confirm_verified_payment(self.intent.order_id, evidence)

    def reserve(self, request_id='refund-request-1'):
        return reserve_full_refund(self.intent.order_id, request_id=request_id, reason='老板取消')

    def evidence(self, refund, status='SUCCESS', **overrides):
        evidence = VerifiedRefund(self.intent.mchid, self.intent.merchant_order_no, self.intent.transaction_id,
            refund.merchant_refund_no, 'wx-refund-1', refund.amount_fen, self.intent.amount_fen,
            'CNY', 10000, 9000, 10000, 9500, 1000, status, timezone.now() if status == 'SUCCESS' else None)
        return replace(evidence, **overrides)

    def test_before_verified_payment_cannot_reserve_refund(self):
        with self.assertRaises(DirectRefundError):
            self.reserve()
        self.assertFalse(DirectPaymentRefund.objects.exists())

    def test_accepted_is_not_refunded_and_success_is_idempotent(self):
        self.pay()
        refund = self.reserve()
        self.assertEqual(self.reserve().pk, refund.pk)
        with self.assertRaises(DirectRefundError):
            self.reserve('refund-request-2')
        order = Order.objects.get(pk=self.intent.order_id)
        self.assertEqual((order.status, order.payment_status), ('CANCELLED', 'PAID'))
        self.assertIsNone(order.auto_cancel_at)
        processing = self.evidence(refund, 'PROCESSING')
        self.assertEqual(apply_verified_refund(order.pk, refund.pk, processing).status, 'PROCESSING')
        order.refresh_from_db()
        self.assertEqual(order.payment_status, 'PAID')
        success = self.evidence(refund)
        self.assertEqual(apply_verified_refund(order.pk, refund.pk, success).status, 'SUCCESS')
        self.assertEqual(apply_verified_refund(order.pk, refund.pk, success).status, 'SUCCESS')
        self.assertEqual(apply_verified_refund(order.pk, refund.pk, processing).status, 'SUCCESS')
        order.refresh_from_db()
        self.assertEqual((order.payment_status, order.refunded_at), ('REFUNDED', success.success_at))
        refund.refresh_from_db()
        self.assertEqual((refund.amount_fen, refund.payer_total_fen, refund.payer_refund_fen), (10000, 10000, 9000))
        self.assertEqual((refund.settlement_total_fen, refund.settlement_refund_fen, refund.discount_refund_fen), (10000, 9500, 1000))
        self.assertEqual(Wallet.objects.get(pk=self.wallet.pk).balance, 50000)
        self.assertFalse(Transaction.objects.filter(order=order).exists())

    def test_unknown_keeps_number_and_mismatched_evidence_cannot_advance(self):
        self.pay()
        refund = self.reserve()
        self.assertEqual(mark_refund_unknown(self.intent.order_id, refund.pk).status, 'UNKNOWN')
        self.assertEqual(self.reserve().merchant_refund_no, refund.merchant_refund_no)
        with self.assertRaises(DirectRefundError):
            self.reserve('refund-request-2')
        evidence = self.evidence(refund)
        for field, value in [('mchid', 'other'), ('merchant_order_no', 'other'),
                             ('transaction_id', 'other'), ('merchant_refund_no', 'other'),
                             ('amount_fen', 9999), ('total_fen', 9999), ('currency', 'USD'),
                             ('payer_total_fen', 9000)]:
            with self.subTest(field=field), self.assertRaises(DirectRefundError):
                apply_verified_refund(self.intent.order_id, refund.pk, replace(evidence, **{field: value}))
        refund.refresh_from_db()
        self.assertEqual(refund.status, 'UNKNOWN')
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'PAID')

    def test_verified_closed_allows_a_new_number_but_abnormal_does_not(self):
        self.pay()
        first = self.reserve()
        apply_verified_refund(self.intent.order_id, first.pk, self.evidence(first, 'CLOSED'))
        # Once the order was cancelled for the first refund, a new attempt must
        # still refer to the same paid transaction and a definitively closed one.
        second = self.reserve('refund-request-2')
        self.assertNotEqual(first.merchant_refund_no, second.merchant_refund_no)
        apply_verified_refund(self.intent.order_id, second.pk, self.evidence(second, 'ABNORMAL', channel_refund_id='wx-refund-2'))
        with self.assertRaises(DirectRefundError):
            self.reserve('refund-request-3')

    def test_late_payment_after_cancel_can_be_reserved_without_wallet_credit(self):
        transition(self.intent.order_id, Order.Status.CANCELLED)
        self.pay()
        self.assertEqual(self.intent.status, 'REVIEW')
        refund = self.reserve()
        apply_verified_refund(self.intent.order_id, refund.pk, self.evidence(refund))
        order = Order.objects.get(pk=self.intent.order_id)
        self.assertEqual((order.status, order.payment_status), ('CANCELLED', 'REFUNDED'))
        self.assertEqual(Wallet.objects.get(pk=self.wallet.pk).balance, 50000)

    def test_conflicting_terminal_evidence_is_persisted_and_blocks_new_refund(self):
        self.pay()
        first = self.reserve()
        apply_verified_refund(self.intent.order_id, first.pk, self.evidence(first, 'CLOSED'))
        second = self.reserve('refund-request-2')
        conflicting = self.evidence(first, 'SUCCESS')
        result = apply_verified_refund(self.intent.order_id, first.pk, conflicting)
        self.assertEqual((result.status, result.conflict_previous_status, result.conflict_status),
                         ('REVIEW', 'CLOSED', 'SUCCESS'))
        self.assertEqual(result.conflict_success_at, conflicting.success_at)
        self.assertEqual(result.conflict_payer_refund_fen, 9000)
        self.assertIsNotNone(result.conflict_observed_at)
        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, 'REVIEW')
        with self.assertRaises(DirectRefundError):
            self.reserve('refund-request-3')
        second.refresh_from_db()
        self.assertEqual(second.status, 'RESERVED')
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'PAID')
        self.assertEqual(Wallet.objects.get(pk=self.wallet.pk).balance, 50000)

    def test_unrelated_integrity_failure_is_not_misreported_as_duplicate(self):
        self.pay()
        refund = self.reserve()
        with patch.object(DirectPaymentRefund, 'save', side_effect=IntegrityError('database fault')):
            with self.assertRaisesRegex(IntegrityError, 'database fault'):
                apply_verified_refund(self.intent.order_id, refund.pk, self.evidence(refund))

    def test_closed_refund_with_different_channel_id_is_reviewed_before_new_reservation(self):
        self.pay()
        first = self.reserve()
        apply_verified_refund(self.intent.order_id, first.pk, self.evidence(first, 'CLOSED'))
        conflict = self.evidence(first, 'SUCCESS', channel_refund_id='different-channel-id')
        result = apply_verified_refund(self.intent.order_id, first.pk, conflict)
        self.assertEqual((result.status, result.conflict_previous_status, result.conflict_channel_refund_id),
                         ('REVIEW', 'CLOSED', 'different-channel-id'))
        with self.assertRaises(DirectRefundError):
            self.reserve('refund-request-2')
        self.assertEqual(DirectPaymentRefund.objects.filter(intent=self.intent).count(), 1)


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True)
@skipUnlessDBFeature('has_select_for_update')
class DirectRefundConcurrencyTest(TransactionTestCase):
    def setUp(self):
        user = make_user()
        account = get_or_create_account_for_legacy_user(user)
        account.openid, account.wechat_appid, account.is_openid_bound = 'test-openid', 'wx-test', True
        account.save(update_fields=['openid', 'wechat_appid', 'is_openid_bound'])
        self.wallet = make_wallet(user, balance=50000)
        service = make_service(price=10000)
        request = SimpleNamespace(account=account, legacy_user=user, data={
            'service_id': service.pk, 'expected_amount': 10000, 'client_request_id': 'refund-race-payment',
        })
        self.intent = prepare_direct_order(request, appid='wx-test', mchid='mch-test')
        evidence = VerifiedPayment(self.intent.merchant_order_no, 'wx-refund-race', 'wx-test', 'mch-test',
                                   'test-openid', 10000, 'CNY', timezone.now(),
                                   payer_total_fen=10000, payer_currency='CNY')
        with patch('orders.direct_payment._notify_paid'):
            confirm_verified_payment(self.intent.order_id, evidence)

    def race(self, request_ids):
        barrier = Barrier(2)
        def reserve(request_id):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    return ('ok', reserve_full_refund(self.intent.order_id, request_id=request_id, reason='老板取消').pk)
                except DirectRefundError:
                    return ('rejected', None)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(reserve, request_id) for request_id in request_ids]
            return [future.result(timeout=20) for future in futures]

    def test_distinct_requests_cannot_reserve_two_full_refunds(self):
        results = self.race(['refund-race-one', 'refund-race-two'])
        self.assertEqual(sorted(status for status, _ in results), ['ok', 'rejected'])
        self.assertEqual(DirectPaymentRefund.objects.filter(intent=self.intent).count(), 1)
        order = Order.objects.get(pk=self.intent.order_id)
        self.assertEqual((order.status, order.payment_status), ('CANCELLED', 'PAID'))
        self.assertEqual(Wallet.objects.get(pk=self.wallet.pk).balance, 50000)

    def test_same_request_returns_one_reserved_refund(self):
        results = self.race(['refund-race-same', 'refund-race-same'])
        self.assertEqual([status for status, _ in results], ['ok', 'ok'])
        self.assertEqual(results[0][1], results[1][1])
        self.assertEqual(DirectPaymentRefund.objects.filter(intent=self.intent).count(), 1)
