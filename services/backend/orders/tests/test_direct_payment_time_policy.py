"""Tests for merchant-clock tolerance; no live WeChat guarantees are implied."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user
from integrations.wechatpay_v3 import VerifiedNotification
from orders.direct_payment import (DirectPaymentError, PAYMENT_TIME_SKEW, VerifiedPayment,
                                   confirm_verified_payment, prepare_direct_order)
from orders.direct_payment_delivery_models import DirectPaymentOutbox
from orders.direct_payment_inbox import ingest_verified_payment_notice, process_payment_inbox
from orders.models import Order
from orders.tests.factories import make_service, make_user, make_wallet
from wallet.models import Transaction, Wallet


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True)
class DirectPaymentTimePolicyTest(TestCase):
    def setUp(self):
        self.user = make_user()
        self.account = get_or_create_account_for_legacy_user(self.user)
        self.account.openid, self.account.wechat_appid, self.account.is_openid_bound = 'openid-time', 'wx-test', True
        self.account.save(update_fields=['openid', 'wechat_appid', 'is_openid_bound'])
        self.wallet = make_wallet(self.user, balance=50000)
        service = make_service(price=10000)
        request = SimpleNamespace(account=self.account, legacy_user=self.user,
            data={'service_id': service.pk, 'expected_amount': 10000, 'client_request_id': 'time-policy'})
        self.intent = prepare_direct_order(request, appid='wx-test', mchid='mch-test')
        self.received = self.intent.created_at + timedelta(minutes=10)

    def evidence(self, paid_at):
        return VerifiedPayment(self.intent.merchant_order_no, 'tx-time-policy', self.intent.appid,
            self.intent.mchid, self.intent.openid, self.intent.amount_fen, 'CNY', paid_at,
            payer_total_fen=self.intent.amount_fen, payer_currency='CNY')

    def test_exact_skew_boundaries_are_allowed(self):
        # Each boundary uses a fresh transaction; a repeated transaction ID
        # with a different success time is correctly a conflict.
        with patch('orders.direct_payment._notify_paid'):
            payment = self.evidence(self.intent.created_at - PAYMENT_TIME_SKEW)
            result = confirm_verified_payment(self.intent.order_id, payment, received_at=self.received)
        self.assertEqual(result.status, 'PAID')
        self.assertEqual(confirm_verified_payment(self.intent.order_id, payment,
                         received_at=self.received + timedelta(days=3)).status, 'PAID')

    def test_future_boundary_is_allowed(self):
        payment = self.evidence(self.received + PAYMENT_TIME_SKEW)
        with patch('orders.direct_payment._notify_paid'):
            result = confirm_verified_payment(self.intent.order_id, payment, received_at=self.received)
        self.assertEqual(result.status, 'PAID')

    def test_outside_either_boundary_preserves_receipt_and_stops_dispatch(self):
        payment = self.evidence(self.received + PAYMENT_TIME_SKEW + timedelta(microseconds=1))
        with patch('orders.direct_payment._notify_paid') as notification:
            result = confirm_verified_payment(self.intent.order_id, payment, received_at=self.received)
            self.assertEqual(result.status, 'REVIEW')
            self.assertEqual(result.review_reason, 'PAYMENT_TIME_REVIEW')
            self.assertEqual((result.transaction_id, result.paid_at), (payment.transaction_id, payment.paid_at))
            # Time passing or redelivery cannot silently release REVIEW.
            replay = confirm_verified_payment(self.intent.order_id, payment,
                received_at=self.received + timedelta(days=3))
            self.assertEqual(replay.status, 'REVIEW')
            notification.assert_not_called()
        order = Order.objects.get(pk=self.intent.order_id)
        self.assertEqual((order.payment_status, order.pool_entered_at, order.auto_cancel_at), ('UNPAID', None, None))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 50000)
        self.assertFalse(Transaction.objects.filter(order=order).exists())
        self.assertFalse(DirectPaymentOutbox.objects.filter(intent=self.intent).exists())

    def test_before_creation_beyond_skew_is_review(self):
        payment = self.evidence(self.intent.created_at - PAYMENT_TIME_SKEW - timedelta(microseconds=1))
        result = confirm_verified_payment(self.intent.order_id, payment, received_at=self.received)
        self.assertEqual((result.status, result.review_reason, result.transaction_id),
                         ('REVIEW', 'PAYMENT_TIME_REVIEW', payment.transaction_id))
        self.assertFalse(DirectPaymentOutbox.objects.filter(intent=self.intent).exists())

    def test_unmatched_identity_never_records_transaction(self):
        payment = replace(self.evidence(self.received + timedelta(days=3)), openid='another-account')
        with self.assertRaises(DirectPaymentError):
            confirm_verified_payment(self.intent.order_id, payment, received_at=self.received)
        self.intent.refresh_from_db()
        self.assertEqual((self.intent.status, self.intent.transaction_id), ('AWAITING', None))

    def notice(self, paid_at):
        payload = {'appid': self.intent.appid, 'mchid': self.intent.mchid,
            'out_trade_no': self.intent.merchant_order_no, 'transaction_id': 'tx-time-policy',
            'trade_type': 'JSAPI', 'trade_state': 'SUCCESS', 'success_time': paid_at.isoformat(),
            'payer': {'openid': self.intent.openid}, 'amount': {'total': self.intent.amount_fen,
                'payer_total': self.intent.amount_fen, 'currency': 'CNY', 'payer_currency': 'CNY'}}
        return VerifiedNotification('event-time-policy', 'TRANSACTION.SUCCESS', 'encrypt-resource',
                                    'transaction', payload, 'a' * 64)

    def test_worker_delay_does_not_erase_future_at_receipt(self):
        notice = self.notice(timezone.now() + timedelta(minutes=10))
        inbox = ingest_verified_payment_notice(self.intent.mchid, notice)
        with patch('orders.direct_payment.timezone.now', return_value=inbox.created_at + timedelta(days=1)):
            result = process_payment_inbox(inbox.pk)
        self.intent.refresh_from_db()
        self.assertEqual((result.status, self.intent.status, self.intent.review_reason),
                         ('REVIEW', 'REVIEW', 'PAYMENT_TIME_REVIEW'))
        self.assertEqual(self.intent.transaction_id, 'tx-time-policy')
        self.assertFalse(DirectPaymentOutbox.objects.filter(intent=self.intent).exists())

    def test_late_notification_for_normal_payment_is_not_a_failure(self):
        earlier = timezone.now() - timedelta(days=2)
        type(self.intent).objects.filter(pk=self.intent.pk).update(created_at=earlier)
        self.intent.refresh_from_db()
        notice = self.notice(earlier + timedelta(hours=1))
        inbox = ingest_verified_payment_notice(self.intent.mchid, notice)
        with patch('orders.direct_payment._notify_paid'):
            result = process_payment_inbox(inbox.pk)
        self.intent.refresh_from_db()
        self.assertEqual((result.status, self.intent.status), ('DONE', 'PAID'))
        self.assertTrue(DirectPaymentOutbox.objects.filter(intent=self.intent).exists())
