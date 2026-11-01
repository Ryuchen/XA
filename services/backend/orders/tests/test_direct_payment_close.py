from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from orders.direct_payment import DirectPaymentError, confirm_verified_payment
from orders.direct_payment_close import FakeCloseGateway, recover_internal_fake_close, request_internal_fake_close
from orders.direct_payment_delivery_models import DirectPaymentOutbox
from orders.direct_payment_query import FakeQueryGateway, FakeQueryResult, request_internal_fake_query
from orders.direct_payment_session import FakePrepayGateway, request_fake_prepay, request_test_cancellation
from orders.direct_payment_session_models import DirectPaymentTestClose, DirectPaymentTestQuery
from orders.models import Order
from orders.tests import test_direct_payment_session as session_tests


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True)
class DirectPaymentCloseTest(session_tests.SessionFixture, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.create()

    def notpay(self, request_id='query-req-1'):
        result = FakeQueryResult('wx-test', 'mch-test', self.intent.merchant_order_no, 'NOTPAY', 'JSAPI')
        request_internal_fake_query(self.intent.pk, request_id=request_id, gateway=FakeQueryGateway(lambda _: result))
        return DirectPaymentTestQuery.objects.get(request_id=request_id)

    def close(self, query, handler=lambda _: None, request_id='close-req-1'):
        return request_internal_fake_close(self.intent.pk, source_query_id=query.pk, request_id=request_id,
                                           gateway=FakeCloseGateway(handler))

    def test_pre_cancel_query_cannot_authorize_close_even_if_it_completes_after_cancel(self):
        def query_handler(_):
            request_test_cancellation(self.intent.pk, self.account.pk)
            return FakeQueryResult('wx-test', 'mch-test', self.intent.merchant_order_no, 'NOTPAY', 'JSAPI')
        request_internal_fake_query(self.intent.pk, request_id='before-cancel', gateway=FakeQueryGateway(query_handler))
        query = DirectPaymentTestQuery.objects.get(request_id='before-cancel')
        with self.assertRaises(DirectPaymentError):
            self.close(query)
        self.assertFalse(DirectPaymentTestClose.objects.exists())

    def test_ack_is_not_closed_and_original_identity_is_used_outside_transaction(self):
        request_test_cancellation(self.intent.pk, self.account.pk)
        query = self.notpay()
        calls = []
        def fake(snapshot):
            self.assertFalse(connection.in_atomic_block)
            calls.append(snapshot)
        result = self.close(query, fake)
        self.assertEqual((result.status, result.outcome), ('ACK', 'SIMULATED_ACK_ONLY'))
        self.assertEqual((calls[0].appid, calls[0].mchid, calls[0].merchant_order_no),
                         (self.intent.appid, self.intent.mchid, self.intent.merchant_order_no))
        self.assertEqual(self.close(query, fake, request_id='another-close'), result)
        self.assertEqual(len(calls), 1)
        order = Order.objects.get(pk=self.intent.order_id)
        self.assertEqual((order.status, order.payment_status), ('PENDING', 'UNPAID'))
        self.assertFalse(DirectPaymentOutbox.objects.exists())

    def test_timeout_never_retries_and_late_ack_is_preserved_without_overwriting_unknown(self):
        request_test_cancellation(self.intent.pk, self.account.pk)
        query = self.notpay()
        calls = []
        def fake(_):
            calls.append(1)
            DirectPaymentTestClose.objects.filter(session__intent=self.intent).update(lease_until=timezone.now()-timedelta(seconds=1))
            self.assertEqual(recover_internal_fake_close(self.intent.pk).status, 'UNKNOWN')
        result = self.close(query, fake)
        row = DirectPaymentTestClose.objects.get()
        self.assertEqual((result.status, row.late_outcome), ('UNKNOWN', 'SIMULATED_ACK'))
        self.assertEqual(self.close(query, fake, 'another-close'), result)
        self.assertEqual(len(calls), 1)

    def test_worker_crash_leaves_single_unknown_attempt_without_resend(self):
        request_test_cancellation(self.intent.pk, self.account.pk)
        query = self.notpay()
        def crash(_):
            raise SystemExit('simulated worker crash')
        with self.assertRaises(SystemExit):
            self.close(query, crash)
        DirectPaymentTestClose.objects.filter(session__intent=self.intent).update(lease_until=timezone.now()-timedelta(seconds=1))
        self.assertEqual(recover_internal_fake_close(self.intent.pk).status, 'UNKNOWN')
        self.assertEqual(self.close(query, lambda _: self.fail('must not resend')).status, 'UNKNOWN')
        self.assertEqual(DirectPaymentTestClose.objects.count(), 1)

    def test_notpay_then_payment_prevents_sending(self):
        request_test_cancellation(self.intent.pk, self.account.pk)
        query = self.notpay()
        with patch('orders.direct_payment._notify_paid'):
            confirm_verified_payment(self.intent.order_id, self.evidence)
        with self.assertRaises(DirectPaymentError):
            self.close(query)
        self.assertFalse(DirectPaymentTestClose.objects.exists())

    def test_payment_during_close_is_reviewed_and_payment_fact_is_preserved(self):
        request_test_cancellation(self.intent.pk, self.account.pk)
        query = self.notpay()
        def fake(_):
            with patch('orders.direct_payment._notify_paid'):
                confirm_verified_payment(self.intent.order_id, self.evidence)
        result = self.close(query, fake)
        self.intent.refresh_from_db()
        self.assertEqual((result.status, self.intent.status, self.intent.transaction_id), ('REVIEW', 'REVIEW', 'wx-transaction'))
        self.assertEqual(DirectPaymentTestClose.objects.get().late_outcome, 'SIMULATED_ACK')
        self.assertFalse(DirectPaymentOutbox.objects.exists())

    def test_ack_then_query_closed_then_payment_is_reviewed(self):
        request_test_cancellation(self.intent.pk, self.account.pk)
        query = self.notpay()
        self.assertEqual(self.close(query).status, 'ACK')
        closed = FakeQueryResult('wx-test', 'mch-test', self.intent.merchant_order_no, 'CLOSED', 'JSAPI')
        request_internal_fake_query(self.intent.pk, request_id='query-closed', gateway=FakeQueryGateway(lambda _: closed))
        with patch('orders.direct_payment._notify_paid'):
            paid = confirm_verified_payment(self.intent.order_id, self.evidence)
        self.assertEqual((paid.status, paid.transaction_id), ('REVIEW', 'wx-transaction'))
        self.assertFalse(DirectPaymentOutbox.objects.exists())

    def test_old_prepay_may_arrive_after_fake_close_ack_without_new_payment_parameters(self):
        def prepay(_):
            request_test_cancellation(self.intent.pk, self.account.pk)
            self.assertEqual(self.close(self.notpay()).status, 'ACK')
            return 'fake-late-prepay'
        result = request_fake_prepay(self.intent.pk, self.account.pk, gateway=FakePrepayGateway(prepay))
        self.assertEqual((result.state, result.fake_prepay_id), ('CANCEL_REQUESTED', None))
        self.assertEqual(DirectPaymentTestClose.objects.get().outcome, 'SIMULATED_ACK_ONLY')


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True)
@skipUnlessDBFeature('has_select_for_update')
class DirectPaymentCloseConcurrencyTest(DirectPaymentCloseTest):
    def test_two_workers_different_requests_send_once(self):
        request_test_cancellation(self.intent.pk, self.account.pk)
        query = self.notpay()
        entered, release = Event(), Event()
        calls = []
        def fake(_):
            calls.append(1)
            entered.set()
            if not release.wait(10):
                raise TimeoutError()
        def worker(request_id):
            close_old_connections()
            try:
                return self.close(query, fake, request_id)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(worker, 'close-req-1')
            try:
                self.assertTrue(entered.wait(10))
                second = pool.submit(worker, 'close-req-2').result(timeout=10)
                self.assertEqual(second.status, 'SENDING')
            finally:
                release.set()
            self.assertEqual(first.result(timeout=10).status, 'ACK')
        self.assertEqual(len(calls), 1)

    def test_payment_during_fake_close_call_preserves_fact_and_blocks_dispatch(self):
        request_test_cancellation(self.intent.pk, self.account.pk)
        query = self.notpay()
        entered, release = Event(), Event()
        def fake(_):
            entered.set()
            if not release.wait(10):
                raise TimeoutError()
        def worker():
            close_old_connections()
            try:
                return self.close(query, fake)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(worker)
            try:
                self.assertTrue(entered.wait(10))
                with patch('orders.direct_payment._notify_paid'):
                    confirmed = confirm_verified_payment(self.intent.order_id, self.evidence)
                self.assertEqual(confirmed.status, 'REVIEW')
            finally:
                release.set()
            self.assertEqual(future.result(timeout=10).status, 'REVIEW')
        self.assertEqual(DirectPaymentTestClose.objects.get().late_outcome, 'SIMULATED_ACK')
        self.assertFalse(DirectPaymentOutbox.objects.exists())

    def test_old_prepay_worker_returns_after_close_ack_without_releasing_new_order(self):
        entered, release = Event(), Event()
        def fake_prepay(_):
            entered.set()
            if not release.wait(10):
                raise TimeoutError()
            return 'fake-late-prepay'
        def worker():
            close_old_connections()
            try:
                return request_fake_prepay(self.intent.pk, self.account.pk, gateway=FakePrepayGateway(fake_prepay))
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(worker)
            try:
                self.assertTrue(entered.wait(10))
                request_test_cancellation(self.intent.pk, self.account.pk)
                query = self.notpay()
                self.assertEqual(self.close(query).status, 'ACK')
            finally:
                release.set()
            self.assertIsNone(future.result(timeout=10).fake_prepay_id)
        self.assertEqual(DirectPaymentTestClose.objects.get().outcome, 'SIMULATED_ACK_ONLY')
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'UNPAID')
