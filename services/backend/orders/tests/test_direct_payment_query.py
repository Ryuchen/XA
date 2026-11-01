from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier, Event
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from club_accounts.models import ClubAccount
from integrations.wechatpay_v3 import VerifiedNotification
from orders.direct_payment import DirectPaymentError, confirm_verified_payment
from orders.direct_payment_delivery_models import DirectPaymentOutbox
from orders.direct_payment_inbox import ingest_verified_payment_notice, process_payment_inbox
from orders.direct_payment_query import FakeQueryGateway, FakeQueryResult, recover_test_query, request_fake_query, request_internal_fake_query
from orders.direct_payment_session import FakePrepayGateway, request_fake_prepay, request_test_cancellation, resume_test_session
from orders.direct_payment_session_models import DirectPaymentTestQuery
from orders.models import Order
from orders.tests import test_direct_payment_session as session_tests


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True)
class DirectPaymentQueryTest(session_tests.SessionFixture, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.create()

    def result(self, state, *, payment=None, appid='wx-test', mchid='mch-test'):
        return FakeQueryResult(appid, mchid, self.intent.merchant_order_no, state, 'JSAPI', payment)

    def query(self, result, request_id='query-req-1'):
        return request_fake_query(self.intent.pk, self.account.pk, request_id=request_id,
                                  gateway=FakeQueryGateway(lambda _: result))

    def notification(self, transaction_id):
        payload = {'appid': 'wx-test', 'mchid': 'mch-test', 'out_trade_no': self.intent.merchant_order_no,
                   'transaction_id': transaction_id, 'trade_type': 'JSAPI', 'trade_state': 'SUCCESS',
                   'success_time': self.evidence.paid_at.isoformat(), 'payer': {'openid': 'openid-test'},
                   'amount': {'total': 10000, 'currency': 'CNY', 'payer_total': 10000, 'payer_currency': 'CNY'}}
        notice = VerifiedNotification('query-test-event', 'TRANSACTION.SUCCESS', 'encrypt-resource',
                                      'transaction', payload, 'a' * 64)
        return ingest_verified_payment_notice('mch-test', notice)

    def test_notpay_is_only_simulated_fact_and_replay_never_resends(self):
        calls = []
        def fake(snapshot):
            self.assertFalse(connection.in_atomic_block)
            calls.append(snapshot)
            return self.result('NOTPAY')
        gateway = FakeQueryGateway(fake)
        first = request_fake_query(self.intent.pk, self.account.pk, request_id='query-req-1', gateway=gateway)
        self.assertEqual((first.status, first.outcome), ('DONE', 'SIMULATED_NOTPAY'))
        self.assertEqual(request_fake_query(self.intent.pk, self.account.pk, request_id='query-req-1', gateway=gateway), first)
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0].mchid, calls[0].appid, calls[0].merchant_order_no),
                         (self.intent.mchid, self.intent.appid, self.intent.merchant_order_no))
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'UNPAID')
        self.assertFalse(DirectPaymentOutbox.objects.exists())

    def test_timeout_same_request_is_unknown_and_explicit_new_query_uses_original_number(self):
        calls = []
        def fail(snapshot):
            calls.append(snapshot)
            raise TimeoutError('sensitive-internal-message')
        gateway = FakeQueryGateway(fail)
        result = request_fake_query(self.intent.pk, self.account.pk, request_id='query-req-1', gateway=gateway)
        self.assertEqual((result.status, result.outcome), ('UNKNOWN', 'QUERY_RESULT_UNKNOWN'))
        self.assertEqual(request_fake_query(self.intent.pk, self.account.pk, request_id='query-req-1', gateway=gateway), result)
        self.assertEqual(len(calls), 1)
        next_result = self.query(self.result('NOTPAY'), request_id='query-req-2')
        self.assertEqual(next_result.outcome, 'SIMULATED_NOTPAY')
        self.assertEqual(self.intent.merchant_order_no, calls[0].merchant_order_no)
        self.assertNotIn('sensitive-internal-message', str(DirectPaymentTestQuery.objects.get(request_id='query-req-1').fake_evidence))

    def test_success_after_cancel_records_payment_for_review_without_dispatch(self):
        request_test_cancellation(self.intent.pk, self.account.pk)
        with patch('orders.direct_payment._notify_paid'):
            result = self.query(self.result('SUCCESS', payment=self.evidence))
        self.intent.refresh_from_db()
        self.assertEqual((result.status, self.intent.status, self.intent.transaction_id), ('REVIEW', 'REVIEW', 'wx-transaction'))
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'UNPAID')
        self.assertFalse(DirectPaymentOutbox.objects.filter(intent=self.intent).exists())
        self.assertNotIn('openid', str(DirectPaymentTestQuery.objects.get().fake_evidence))

    def test_closed_then_success_is_reviewed_never_dispatched_or_claimed_closed(self):
        self.assertEqual(self.query(self.result('CLOSED')).outcome, 'SIMULATED_CLOSED_REVIEW')
        self.assertEqual(resume_test_session(self.intent.pk, self.account.pk).state, 'SIMULATED_CLOSE_REVIEW')
        with patch('orders.direct_payment._notify_paid'):
            confirmed = confirm_verified_payment(self.intent.order_id, self.evidence)
        self.assertEqual((confirmed.status, confirmed.review_reason, confirmed.transaction_id),
                         ('REVIEW', 'QUERY_CLOSE_PAYMENT_CONFLICT', 'wx-transaction'))
        order = Order.objects.get(pk=self.intent.order_id)
        self.assertEqual((order.status, order.payment_status), ('PENDING', 'UNPAID'))
        self.assertFalse(DirectPaymentOutbox.objects.filter(intent=confirmed).exists())

    def test_success_then_closed_preserves_payment_and_existing_service(self):
        with patch('orders.direct_payment._notify_paid'):
            self.assertEqual(confirm_verified_payment(self.intent.order_id, self.evidence).status, 'PAID')
        Order.objects.filter(pk=self.intent.order_id).update(status='IN_SERVICE')
        result = self.query(self.result('CLOSED'))
        self.intent.refresh_from_db()
        order = Order.objects.get(pk=self.intent.order_id)
        self.assertEqual((result.status, self.intent.status, order.status, order.payment_status),
                         ('REVIEW', 'REVIEW', 'IN_SERVICE', 'PAID'))
        self.assertEqual(DirectPaymentOutbox.objects.get(intent=self.intent).status, 'REVIEW')

    def test_closed_then_inbox_success_keeps_both_and_blocks_dispatch(self):
        self.query(self.result('CLOSED'))
        notice = self.notification('wx-transaction')
        with patch('orders.direct_payment._notify_paid'):
            self.assertEqual(process_payment_inbox(notice.pk).status, 'REVIEW')
        self.intent.refresh_from_db()
        self.assertEqual((self.intent.status, self.intent.transaction_id), ('REVIEW', 'wx-transaction'))
        self.assertEqual(DirectPaymentTestQuery.objects.get().fake_evidence['trade_state'], 'CLOSED')
        self.assertFalse(DirectPaymentOutbox.objects.filter(intent=self.intent).exists())

    def test_query_success_then_conflicting_inbox_blocks_existing_dispatch(self):
        with patch('orders.direct_payment._notify_paid'):
            self.query(self.result('SUCCESS', payment=self.evidence))
            notice = self.notification('other-transaction')
            self.assertEqual(process_payment_inbox(notice.pk).status, 'REVIEW')
        self.intent.refresh_from_db()
        self.assertEqual((self.intent.status, self.intent.transaction_id), ('REVIEW', 'wx-transaction'))
        self.assertEqual(DirectPaymentTestQuery.objects.get().fake_evidence['transaction_id'], 'wx-transaction')
        self.assertEqual(DirectPaymentOutbox.objects.get(intent=self.intent).status, 'REVIEW')

    def test_late_prepay_response_after_closed_is_not_exposed(self):
        def fake(_):
            self.query(self.result('CLOSED'))
            return 'fake-private-result'
        result = request_fake_prepay(self.intent.pk, self.account.pk, gateway=FakePrepayGateway(fake))
        self.assertEqual((result.state, result.fake_prepay_id), ('SIMULATED_CLOSE_REVIEW', None))
        self.assertIsNone(resume_test_session(self.intent.pk, self.account.pk).fake_prepay_id)

    def test_owner_disabled_during_query_keeps_success_but_stops_dispatch(self):
        def fake(_):
            ClubAccount.objects.filter(pk=self.account.pk).update(is_active=False)
            return self.result('SUCCESS', payment=self.evidence)
        with patch('orders.direct_payment._notify_paid'):
            result = request_fake_query(self.intent.pk, self.account.pk, request_id='query-req-1', gateway=FakeQueryGateway(fake))
        self.intent.refresh_from_db()
        self.assertEqual((result.status, self.intent.status, self.intent.transaction_id), ('REVIEW', 'REVIEW', 'wx-transaction'))
        self.assertEqual(DirectPaymentTestQuery.objects.get().fake_evidence['transaction_id'], 'wx-transaction')
        self.assertFalse(DirectPaymentOutbox.objects.filter(intent=self.intent).exists())

    def test_internal_reconciliation_after_owner_disabled_preserves_success(self):
        ClubAccount.objects.filter(pk=self.account.pk).update(is_active=False)
        gateway = FakeQueryGateway(lambda _: self.result('SUCCESS', payment=self.evidence))
        with self.assertRaises(DirectPaymentError):
            request_fake_query(self.intent.pk, self.account.pk, request_id='query-req-1', gateway=gateway)
        with patch('orders.direct_payment._notify_paid'):
            result = request_internal_fake_query(self.intent.pk, request_id='query-req-1', gateway=gateway)
        self.intent.refresh_from_db()
        self.assertEqual((result.status, self.intent.status, self.intent.transaction_id), ('REVIEW', 'REVIEW', 'wx-transaction'))
        self.assertFalse(DirectPaymentOutbox.objects.filter(intent=self.intent).exists())

    def test_internal_reconciliation_after_cancel_does_not_lose_cancel(self):
        request_test_cancellation(self.intent.pk, self.account.pk)
        ClubAccount.objects.filter(pk=self.account.pk).update(is_active=False)
        gateway = FakeQueryGateway(lambda _: self.result('SUCCESS', payment=self.evidence))
        with patch('orders.direct_payment._notify_paid'):
            result = request_internal_fake_query(self.intent.pk, request_id='query-req-1', gateway=gateway)
        self.intent.refresh_from_db()
        self.assertEqual((result.status, self.intent.review_reason), ('REVIEW', 'CANCEL_PAYMENT_CONFLICT'))
        self.assertFalse(DirectPaymentOutbox.objects.filter(intent=self.intent).exists())

    def test_internal_closed_after_paid_preserves_payment_with_disabled_owner(self):
        with patch('orders.direct_payment._notify_paid'):
            confirm_verified_payment(self.intent.order_id, self.evidence)
        ClubAccount.objects.filter(pk=self.account.pk).update(is_active=False)
        result = request_internal_fake_query(self.intent.pk, request_id='query-req-1',
                                             gateway=FakeQueryGateway(lambda _: self.result('CLOSED')))
        self.intent.refresh_from_db()
        self.assertEqual((result.status, self.intent.status), ('REVIEW', 'REVIEW'))
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'PAID')
        self.assertEqual(DirectPaymentOutbox.objects.get(intent=self.intent).status, 'REVIEW')

    def test_bad_amount_before_notification_stops_later_payment_dispatch(self):
        bad = replace(self.evidence, amount_fen=9999)
        result = self.query(self.result('SUCCESS', payment=bad))
        self.intent.refresh_from_db()
        self.assertEqual((result.outcome, self.intent.status, self.intent.transaction_id),
                         ('QUERY_PAYMENT_AMOUNT_INVALID', 'AWAITING', None))
        with patch('orders.direct_payment._notify_paid'):
            notice = self.notification('wx-transaction')
            self.assertEqual(process_payment_inbox(notice.pk).status, 'REVIEW')
        self.intent.refresh_from_db()
        self.assertEqual((self.intent.status, self.intent.review_reason, self.intent.transaction_id),
                         ('REVIEW', 'QUERY_PAYMENT_EVIDENCE_RISK', 'wx-transaction'))
        self.assertFalse(DirectPaymentOutbox.objects.filter(intent=self.intent).exists())

    def test_bad_transaction_id_query_blocks_later_notice_without_fabricating_payment(self):
        bad = replace(self.evidence, transaction_id=' malformed ')
        self.assertEqual(self.query(self.result('SUCCESS', payment=bad)).status, 'REVIEW')
        self.intent.refresh_from_db()
        self.assertEqual((self.intent.status, self.intent.transaction_id), ('AWAITING', None))
        self.assertIsNotNone(self.intent.evidence_risk_at)
        with patch('orders.direct_payment._notify_paid'):
            notice = self.notification('wx-transaction')
            self.assertEqual(process_payment_inbox(notice.pk).status, 'REVIEW')
        self.intent.refresh_from_db()
        self.assertEqual((self.intent.status, self.intent.transaction_id), ('REVIEW', 'wx-transaction'))
        self.assertFalse(DirectPaymentOutbox.objects.filter(intent=self.intent).exists())

    def test_wrong_payment_type_flags_only_matching_payer(self):
        other = replace(self.evidence, openid='other')
        bad = FakeQueryResult('wx-test', 'mch-test', self.intent.merchant_order_no, 'SUCCESS', 'NATIVE', other)
        self.query(bad)
        self.intent.refresh_from_db()
        self.assertIsNone(self.intent.evidence_risk_at)
        same = replace(bad, payment=self.evidence)
        self.query(same, request_id='query-req-2')
        self.intent.refresh_from_db()
        self.assertIsNotNone(self.intent.evidence_risk_at)

    def test_promotion_conflict_retains_semantic_hash_not_sensitive_details(self):
        with patch('orders.direct_payment._notify_paid'):
            confirm_verified_payment(self.intent.order_id, self.evidence)
        altered = replace(self.evidence, promotion_detail=[{'coupon_id': 'private-coupon', 'amount': 1}])
        self.assertEqual(self.query(self.result('SUCCESS', payment=altered)).outcome, 'QUERY_PAYMENT_CONFLICT')
        saved = DirectPaymentTestQuery.objects.get().fake_evidence
        self.assertEqual(len(saved['promotion_sha256']), 64)
        self.assertNotIn('private-coupon', str(saved))
        self.assertEqual(DirectPaymentOutbox.objects.get(intent=self.intent).status, 'REVIEW')

    def test_query_conflict_stops_existing_paid_order_but_unrelated_identity_cannot(self):
        with patch('orders.direct_payment._notify_paid'):
            confirm_verified_payment(self.intent.order_id, self.evidence)
        unrelated = self.result('SUCCESS', payment=self.evidence, appid='other')
        self.assertEqual(self.query(unrelated).outcome, 'QUERY_IDENTITY_OR_STATE_INVALID')
        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, 'PAID')
        bad = replace(self.evidence, transaction_id='other-transaction')
        self.assertEqual(self.query(self.result('SUCCESS', payment=bad), request_id='query-req-2').outcome,
                         'QUERY_PAYMENT_CONFLICT')
        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, 'REVIEW')
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'PAID')
        self.assertEqual(DirectPaymentOutbox.objects.get(intent=self.intent).status, 'REVIEW')

    def test_stale_notpay_never_rolls_back_paid_order(self):
        with patch('orders.direct_payment._notify_paid'):
            confirm_verified_payment(self.intent.order_id, self.evidence)
        self.assertEqual(self.query(self.result('NOTPAY')).outcome, 'SIMULATED_NOTPAY')
        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, 'PAID')
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'PAID')

    def test_late_query_result_is_recorded_after_recovery_without_second_send(self):
        calls = []
        def fake(snapshot):
            calls.append(snapshot)
            DirectPaymentTestQuery.objects.filter(request_id='query-req-1').update(lease_until=timezone.now()-timedelta(seconds=1))
            self.assertEqual(recover_test_query(self.intent.pk, 'query-req-1').status, 'UNKNOWN')
            return self.result('SUCCESS', payment=self.evidence)
        with patch('orders.direct_payment._notify_paid'):
            result = request_fake_query(self.intent.pk, self.account.pk, request_id='query-req-1', gateway=FakeQueryGateway(fake))
        self.assertEqual((result.status, result.outcome), ('DONE', 'SIMULATED_PAYMENT_APPLIED'))
        self.assertEqual(len(calls), 1)
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'PAID')

    def test_disabled_owner_and_invalid_query_cannot_produce_payment_parameters(self):
        with self.assertRaises(DirectPaymentError):
            request_fake_query(self.intent.pk, self.account.pk, request_id='query-req-1', gateway=object())
        with override_settings(WECHAT_DIRECT_DOMAIN_TESTING=False), self.assertRaises(DirectPaymentError):
            self.query(self.result('NOTPAY'))
        self.assertFalse(DirectPaymentTestQuery.objects.exists())


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True)
@skipUnlessDBFeature('has_select_for_update')
class DirectPaymentQueryConcurrencyTest(session_tests.SessionFixture, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.create()

    def make_notice(self):
        payload = {'appid': 'wx-test', 'mchid': 'mch-test', 'out_trade_no': self.intent.merchant_order_no,
                   'transaction_id': 'wx-transaction', 'trade_type': 'JSAPI', 'trade_state': 'SUCCESS',
                   'success_time': self.evidence.paid_at.isoformat(), 'payer': {'openid': 'openid-test'},
                   'amount': {'total': 10000, 'currency': 'CNY', 'payer_total': 10000, 'payer_currency': 'CNY'}}
        return ingest_verified_payment_notice('mch-test', VerifiedNotification(
            'amount-race-event', 'TRANSACTION.SUCCESS', 'encrypt-resource', 'transaction', payload, 'b' * 64))

    def test_bad_query_result_holds_order_lock_before_valid_notice(self):
        from orders import direct_payment_query
        notice = self.make_notice()
        entered, release, notice_started = Event(), Event(), Event()
        original = direct_payment_query._fake_payment_payload
        bad = replace(self.evidence, amount_fen=9999)
        def blocked_payload(payment):
            entered.set()
            if not release.wait(10):
                raise TimeoutError()
            return original(payment)
        def query_worker():
            close_old_connections()
            try:
                result = FakeQueryResult('wx-test', 'mch-test', self.intent.merchant_order_no, 'SUCCESS', 'JSAPI', bad)
                return request_internal_fake_query(self.intent.pk, request_id='amount-race-1', gateway=FakeQueryGateway(lambda _: result))
            finally:
                close_old_connections()
        def notice_worker():
            close_old_connections()
            try:
                notice_started.set()
                return process_payment_inbox(notice.pk)
            finally:
                close_old_connections()
        with patch.object(direct_payment_query, '_fake_payment_payload', side_effect=blocked_payload), patch('orders.direct_payment._notify_paid'), ThreadPoolExecutor(max_workers=2) as pool:
            query = pool.submit(query_worker)
            try:
                self.assertTrue(entered.wait(10))
                inbox = pool.submit(notice_worker)
                self.assertTrue(notice_started.wait(10))
            finally:
                release.set()
            self.assertEqual(query.result(timeout=10).outcome, 'QUERY_PAYMENT_AMOUNT_INVALID')
            self.assertEqual(inbox.result(timeout=10).status, 'REVIEW')
        self.intent.refresh_from_db()
        self.assertEqual((self.intent.status, self.intent.transaction_id), ('REVIEW', 'wx-transaction'))
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'UNPAID')
        self.assertFalse(DirectPaymentOutbox.objects.filter(intent=self.intent).exists())

    def test_notice_completes_while_bad_query_waits_outside_transaction(self):
        notice = self.make_notice()
        entered, release = Event(), Event()
        bad = replace(self.evidence, amount_fen=9999)
        def fake(_):
            entered.set()
            if not release.wait(10):
                raise TimeoutError()
            return FakeQueryResult('wx-test', 'mch-test', self.intent.merchant_order_no, 'SUCCESS', 'JSAPI', bad)
        def query_worker():
            close_old_connections()
            try:
                return request_internal_fake_query(self.intent.pk, request_id='amount-race-1', gateway=FakeQueryGateway(fake))
            finally:
                close_old_connections()
        with patch('orders.direct_payment._notify_paid'), ThreadPoolExecutor(max_workers=2) as pool:
            query = pool.submit(query_worker)
            try:
                self.assertTrue(entered.wait(10))
                self.assertEqual(process_payment_inbox(notice.pk).status, 'DONE')
            finally:
                release.set()
            self.assertEqual(query.result(timeout=10).outcome, 'QUERY_PAYMENT_CONFLICT')
        self.intent.refresh_from_db()
        self.assertEqual((self.intent.status, self.intent.transaction_id), ('REVIEW', 'wx-transaction'))
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'PAID')
        self.assertEqual(DirectPaymentOutbox.objects.get(intent=self.intent).status, 'REVIEW')

    def test_closed_and_payment_notification_serialize_and_end_in_review(self):
        barrier = Barrier(2)
        def worker(kind):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                if kind == 'payment':
                    return confirm_verified_payment(self.intent.order_id, self.evidence).status
                result = FakeQueryResult('wx-test', 'mch-test', self.intent.merchant_order_no, 'CLOSED', 'JSAPI')
                return request_fake_query(self.intent.pk, self.account.pk, request_id='query-race-1',
                                          gateway=FakeQueryGateway(lambda _: result)).status
            finally:
                close_old_connections()
        with patch('orders.direct_payment._notify_paid'), ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(worker, ('payment', 'closed')))
        self.intent.refresh_from_db()
        self.assertEqual((self.intent.status, self.intent.transaction_id), ('REVIEW', 'wx-transaction'))
        order = Order.objects.get(pk=self.intent.order_id)
        self.assertEqual(order.status, 'PENDING')
        self.assertIn(order.payment_status, ('UNPAID', 'PAID'))
        self.assertEqual(DirectPaymentTestQuery.objects.get().outcome, 'SIMULATED_CLOSED_REVIEW')
        event = DirectPaymentOutbox.objects.filter(intent=self.intent).first()
        self.assertTrue(event is None or event.status == 'REVIEW')

    def test_closed_and_inbox_processing_race_preserve_both(self):
        payload = {'appid': 'wx-test', 'mchid': 'mch-test', 'out_trade_no': self.intent.merchant_order_no,
                   'transaction_id': 'wx-transaction', 'trade_type': 'JSAPI', 'trade_state': 'SUCCESS',
                   'success_time': self.evidence.paid_at.isoformat(), 'payer': {'openid': 'openid-test'},
                   'amount': {'total': 10000, 'currency': 'CNY', 'payer_total': 10000, 'payer_currency': 'CNY'}}
        notice = ingest_verified_payment_notice('mch-test', VerifiedNotification(
            'race-event', 'TRANSACTION.SUCCESS', 'encrypt-resource', 'transaction', payload, 'a' * 64))
        barrier = Barrier(2)
        def worker(kind):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                if kind == 'notice':
                    return process_payment_inbox(notice.pk).status
                result = FakeQueryResult('wx-test', 'mch-test', self.intent.merchant_order_no, 'CLOSED', 'JSAPI')
                return request_fake_query(self.intent.pk, self.account.pk, request_id='query-race-1',
                                          gateway=FakeQueryGateway(lambda _: result)).status
            finally:
                close_old_connections()
        with patch('orders.direct_payment._notify_paid'), ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(worker, ('notice', 'closed')))
        self.intent.refresh_from_db()
        notice.refresh_from_db()
        self.assertEqual((self.intent.status, self.intent.transaction_id), ('REVIEW', 'wx-transaction'))
        self.assertIn(notice.status, ('DONE', 'REVIEW'))
        self.assertEqual(DirectPaymentTestQuery.objects.get().fake_evidence['trade_state'], 'CLOSED')
        outbox = DirectPaymentOutbox.objects.filter(intent=self.intent).first()
        self.assertTrue(outbox is None or outbox.status == 'REVIEW')

    def test_two_workers_same_request_send_only_once(self):
        entered, release = Event(), Event()
        calls = []
        def handler(snapshot):
            calls.append(snapshot)
            entered.set()
            if not release.wait(10):
                raise TimeoutError()
            return FakeQueryResult('wx-test', 'mch-test', self.intent.merchant_order_no, 'NOTPAY', 'JSAPI')
        gateway = FakeQueryGateway(handler)
        def worker():
            close_old_connections()
            try:
                return request_fake_query(self.intent.pk, self.account.pk, request_id='query-race-1', gateway=gateway)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(worker)
            try:
                self.assertTrue(entered.wait(10))
                second = pool.submit(worker).result(timeout=10)
                self.assertEqual(second.status, 'SENDING')
            finally:
                release.set()
            self.assertEqual(first.result(timeout=10).status, 'DONE')
        self.assertEqual(len(calls), 1)
        self.assertEqual(DirectPaymentTestQuery.objects.count(), 1)
