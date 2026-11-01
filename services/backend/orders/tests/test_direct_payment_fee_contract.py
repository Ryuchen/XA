from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier, Event, current_thread
from types import SimpleNamespace
from unittest.mock import patch

from django.db import close_old_connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.direct_payment import DirectPaymentError, VerifiedPayment, confirm_verified_payment, prepare_direct_order
from orders.direct_payment_quote import quote_direct_payment, service_units_to_fen
from orders.direct_payment_quote_models import DirectPaymentQuote
from orders.direct_refund import DirectRefundError, VerifiedRefund, apply_verified_refund, reserve_full_refund
from orders.models import DirectPaymentIntent, Order, ServiceItem
from orders.services import settle_order
from orders.settlement import SettlementError
from orders.tests.factories import make_service, make_user, make_wallet
from orders.timeout import expired_order_candidates, reserve_expired_direct_refund
from wallet.models import Transaction

POLICY = {'policy_id': 'contract-v1', 'rate_numerator': 6, 'rate_denominator': 1000,
          'fee_basis': 'PAYMENT_TOTAL', 'rounding': 'HALF_UP'}


class FeeFixture:
    def setUp(self):
        self.user = make_user()
        self.account = get_or_create_account_for_legacy_user(self.user)
        self.account.openid, self.account.wechat_appid, self.account.is_openid_bound = 'test-openid', 'wx-test', True
        self.account.save(update_fields=['openid', 'wechat_appid', 'is_openid_bound'])
        make_wallet(self.user, balance=50000)
        self.service = make_service(price=10000)

    def request(self, key='fee-quote-1', **extra):
        data = {'service_id': self.service.pk, 'expected_amount': 10000,
                'client_request_id': key, 'dispatch_mode': 'AUTO_GRAB', **extra}
        return SimpleNamespace(account=self.account, legacy_user=self.user, data=data)

    def quote(self):
        return quote_direct_payment(service_units=10000, appid='wx-test', mchid='mch-test')

    def prepare(self, key='fee-quote-1', quote=None, **extra):
        if quote is not None:
            extra.update(expected_payment_total=quote.total_fen, expected_fee_quote_hash=quote.quote_hash)
        return prepare_direct_order(self.request(key, **extra), appid='wx-test', mchid='mch-test')

    def evidence(self, intent):
        return VerifiedPayment(intent.merchant_order_no, 'tx-fee-1', intent.appid, intent.mchid, intent.openid,
                               intent.amount_fen, 'CNY', timezone.now(), payer_total_fen=intent.amount_fen,
                               payer_currency='CNY')


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True)
class DirectPaymentFeeContractTest(FeeFixture, TestCase):
    def test_default_zero_and_unknown_historical_contract_is_quarantined(self):
        self.assertEqual(service_units_to_fen(100), 100)  # 100 internal units = 1 yuan = 100 fen.
        with override_settings(WECHAT_DIRECT_TEST_FEE_POLICY=None):
            intent = self.prepare()
            self.assertEqual((intent.order.amount, intent.amount_fen, intent.fee_quote.fee_fen), (10000, 10000, 0))
            DirectPaymentQuote.objects.filter(intent=intent).delete()  # Existing pre-migration intent.
            paid = confirm_verified_payment(intent.order_id, self.evidence(intent))
            self.assertEqual((paid.status, paid.review_reason, paid.transaction_id),
                             ('REVIEW', 'QUOTE_CONVERSION_UNVERIFIED', 'tx-fee-1'))
            self.assertEqual(Order.objects.get(pk=intent.order_id).payment_status, 'UNPAID')

    @override_settings(WECHAT_DIRECT_TEST_FEE_POLICY=POLICY)
    def test_configured_fee_requires_confirmation_and_replay_keeps_snapshot(self):
        quote = self.quote()
        self.assertEqual((quote.service_fen, quote.fee_fen, quote.total_fen), (10000, 60, 10060))
        with self.assertRaises(DirectPaymentError):
            self.prepare()
        with self.assertRaises(DirectPaymentError):
            self.prepare(expected_payment_total=10060, expected_fee_quote_hash='bad')
        intent = self.prepare(quote=quote)
        self.assertEqual((intent.order.amount, intent.amount_fen), (10000, 10060))
        self.assertEqual((intent.fee_quote.policy_id, intent.fee_quote.fee_fen), ('contract-v1', 60))
        changed = {**POLICY, 'policy_id': 'contract-v2', 'rate_numerator': 7}
        with override_settings(WECHAT_DIRECT_TEST_FEE_POLICY=changed):
            self.assertEqual(self.prepare(quote=quote).pk, intent.pk)
            with self.assertRaises(DirectPaymentError):
                self.prepare('fresh-request-1', quote=quote)
        self.assertEqual(DirectPaymentQuote.objects.count(), 1)

    @override_settings(WECHAT_DIRECT_TEST_FEE_POLICY=POLICY)
    def test_policy_change_rejected_even_when_total_is_unchanged(self):
        quote = self.quote()
        with override_settings(WECHAT_DIRECT_TEST_FEE_POLICY={**POLICY, 'policy_id': 'new-contract'}):
            self.assertEqual(self.quote().total_fen, quote.total_fen)
            with self.assertRaises(DirectPaymentError):
                self.prepare(quote=quote)
        self.assertFalse(DirectPaymentIntent.objects.exists())

    @override_settings(WECHAT_DIRECT_TEST_FEE_POLICY=POLICY)
    def test_conversion_version_is_frozen_and_stale_quote_rejected(self):
        old = self.quote()
        intent = self.prepare(quote=old)
        self.assertEqual((intent.fee_quote.service_units, intent.fee_quote.units_per_yuan,
                          intent.fee_quote.fen_per_yuan, intent.fee_quote.conversion_rounding),
                         (10000, 100, 100, 'EXACT'))
        with override_settings(WECHAT_DIRECT_TEST_UNIT_CONVERSION={'version': 'TEST_200_V2', 'units_per_yuan': 200}):
            current = self.quote()
            self.assertEqual(current.service_fen, 5000)
            self.assertNotEqual(old.quote_hash, current.quote_hash)
            with self.assertRaises(DirectPaymentError):
                self.prepare('fresh-conversion-1', quote=old)
            self.assertEqual(self.prepare(quote=old).pk, intent.pk)
            self.assertEqual(confirm_verified_payment(intent.order_id, self.evidence(intent)).status, 'PAID')

    def test_inexact_sub_fen_conversion_is_rejected(self):
        with override_settings(WECHAT_DIRECT_TEST_UNIT_CONVERSION={'version': 'TEST_200_V2', 'units_per_yuan': 200}):
            self.assertEqual(service_units_to_fen(2), 1)
            with self.assertRaises(DirectPaymentError):
                service_units_to_fen(1)
            with self.assertRaises(DirectPaymentError):
                self.prepare()

    def test_unverified_v1_awaiting_records_receipt_without_dispatch(self):
        intent = self.prepare()
        DirectPaymentQuote.objects.filter(intent=intent).update(contract_version='FEE_QUOTE_V1',
            service_units=None, units_per_yuan=None, fen_per_yuan=None, conversion_version='', conversion_rounding='')
        evidence = self.evidence(intent)
        first = confirm_verified_payment(intent.order_id, evidence)
        self.assertEqual((first.status, first.review_reason, first.transaction_id),
                         ('REVIEW', 'QUOTE_CONVERSION_UNVERIFIED', evidence.transaction_id))
        self.assertEqual(confirm_verified_payment(intent.order_id, evidence).status, 'REVIEW')
        self.assertEqual(Order.objects.get(pk=intent.order_id).payment_status, 'UNPAID')

    @override_settings(WECHAT_DIRECT_TEST_UNIT_CONVERSION={'version': 'TEST_200_V2', 'units_per_yuan': 200})
    def test_zero_fee_refund_uses_channel_fen_not_internal_units(self):
        quote = self.quote()
        intent = self.prepare(quote=quote)
        self.assertEqual((intent.order.amount, intent.amount_fen), (10000, 5000))
        confirm_verified_payment(intent.order_id, self.evidence(intent))
        refund = reserve_full_refund(intent.order_id, request_id='refund-zero-fee', reason='test')
        self.assertEqual((refund.amount_fen, refund.total_fen), (5000, 5000))
        receipt = VerifiedRefund(intent.mchid, intent.merchant_order_no, 'tx-fee-1', refund.merchant_refund_no,
            'wx-refund-zero', 5000, 5000, 'CNY', 5000, 5000, 5000, 5000, 0, 'SUCCESS', timezone.now())
        self.assertEqual(apply_verified_refund(intent.order_id, refund.pk, receipt).status, 'SUCCESS')
        self.assertEqual(Order.objects.get(pk=intent.order_id).payment_status, 'REFUNDED')

    @override_settings(WECHAT_DIRECT_TEST_FEE_POLICY=POLICY)
    def test_client_cannot_supply_rate_and_payment_requires_total(self):
        quote = self.quote()
        with self.assertRaises(DirectPaymentError):
            self.prepare(quote=quote, rate_numerator=0)
        intent = self.prepare(quote=quote)
        evidence = self.evidence(intent)
        with self.assertRaises(DirectPaymentError):
            confirm_verified_payment(intent.order_id, replace(evidence, amount_fen=10000, payer_total_fen=10000))
        self.assertEqual(confirm_verified_payment(intent.order_id, evidence).status, 'PAID')
        self.assertFalse(Transaction.objects.filter(order_id=intent.order_id).exists())

    @override_settings(WECHAT_DIRECT_TEST_FEE_POLICY=POLICY)
    def test_snapshot_tampering_blocks_payment(self):
        intent = self.prepare(quote=self.quote())
        DirectPaymentQuote.objects.filter(intent=intent).update(fee_basis='SERVICE_AMOUNT')
        result = confirm_verified_payment(intent.order_id, self.evidence(intent))
        self.assertEqual((result.status, result.review_reason), ('REVIEW', 'QUOTE_CONVERSION_UNVERIFIED'))
        intent.order.refresh_from_db()
        self.assertEqual(intent.order.payment_status, 'UNPAID')

    @override_settings(WECHAT_DIRECT_TEST_FEE_POLICY=POLICY)
    def test_fee_order_refund_and_settlement_blocked_expiry_marks_review(self):
        intent = self.prepare(quote=self.quote())
        confirm_verified_payment(intent.order_id, self.evidence(intent))
        with self.assertRaisesRegex(DirectRefundError, '手续费'):
            reserve_full_refund(intent.order_id, request_id='refund-fee-1', reason='test')
        with self.assertRaises(SettlementError):
            settle_order(Order.objects.get(pk=intent.order_id))
        Order.objects.filter(pk=intent.order_id).update(auto_cancel_at=timezone.now() - timedelta(seconds=1))
        self.assertIsNone(reserve_expired_direct_refund(intent.order_id))
        intent.refresh_from_db()
        order = Order.objects.get(pk=intent.order_id)
        self.assertEqual((intent.status, intent.review_reason), ('REVIEW', 'FEE_REFUND_POLICY_PENDING'))
        self.assertEqual((order.status, order.payment_status), ('PENDING', 'PAID'))
        self.assertFalse(expired_order_candidates().filter(pk=order.pk).exists())
        self.assertFalse(intent.channel_refunds.exists())
        self.assertFalse(Transaction.objects.filter(order=order).exists())


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True, WECHAT_DIRECT_TEST_FEE_POLICY=POLICY)
@skipUnlessDBFeature('has_select_for_update')
class DirectPaymentFeeConcurrencyTest(FeeFixture, TransactionTestCase):
    def test_simultaneous_retries_create_one_frozen_quote(self):
        quote = self.quote()
        barrier = Barrier(2)
        def create():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return self.prepare(quote=quote).pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(create), workers.submit(create)]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(results[0], results[1])
        self.assertEqual(DirectPaymentIntent.objects.count(), 1)
        self.assertEqual(DirectPaymentQuote.objects.count(), 1)
        self.assertEqual(DirectPaymentQuote.objects.get().total_fen, 10060)

    def test_price_update_committed_before_service_lock_rejects_stale_quote(self):
        quote = self.quote()
        locked, attempted, release = Event(), Event(), Event()
        original = ServiceItem.objects.select_for_update
        def select(*args, **kwargs):
            if current_thread().name == 'fee-creator':
                attempted.set()
            return original(*args, **kwargs)
        def change_price():
            close_old_connections()
            try:
                with transaction.atomic():
                    service = ServiceItem.objects.select_for_update().get(pk=self.service.pk)
                    service.price = 11000
                    service.save(update_fields=['price'])
                    locked.set()
                    if not release.wait(10):
                        raise AssertionError('price change was not released')
            finally:
                close_old_connections()
        def create():
            current_thread().name = 'fee-creator'
            close_old_connections()
            try:
                return self.prepare(quote=quote)
            finally:
                close_old_connections()
        with patch.object(ServiceItem.objects, 'select_for_update', side_effect=select), ThreadPoolExecutor(max_workers=2) as workers:
            changer = workers.submit(change_price)
            try:
                self.assertTrue(locked.wait(10))
                creator = workers.submit(create)
                self.assertTrue(attempted.wait(10))
                self.assertFalse(creator.done())
            finally:
                release.set()
            changer.result(timeout=20)
            with self.assertRaises(ValidationError):
                creator.result(timeout=20)
        self.assertFalse(DirectPaymentIntent.objects.exists())
