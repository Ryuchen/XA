from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone as datetime_timezone
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import patch

from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.direct_payment import DirectPaymentError, VerifiedPayment, confirm_verified_payment, prepare_direct_order
from orders.direct_payment_delivery_models import DirectPaymentOutbox
from orders.direct_payment_session import (
    FakePrepayGateway, create_test_session, request_fake_prepay, request_test_cancellation, resume_test_session,
    recover_test_sessions,
)
from orders.direct_payment_session_models import DirectPaymentSession, DirectPaymentSessionObservation
from orders.models import Order
from orders.tests.factories import make_service, make_user, make_wallet


class SessionFixture:
    def setUp(self):
        super().setUp()
        user = make_user()
        self.account = get_or_create_account_for_legacy_user(user)
        self.account.openid, self.account.wechat_appid, self.account.is_openid_bound = 'openid-test', 'wx-test', True
        self.account.save(update_fields=['openid', 'wechat_appid', 'is_openid_bound'])
        make_wallet(user, balance=50000)
        service = make_service(price=10000)
        request = SimpleNamespace(account=self.account, legacy_user=user, data={
            'service_id': service.pk, 'expected_amount': 10000, 'client_request_id': 'test-session-intent',
        })
        self.intent = prepare_direct_order(request, appid='wx-test', mchid='mch-test')
        self.deadline = timezone.now() + timedelta(minutes=15)
        self.evidence = VerifiedPayment(self.intent.merchant_order_no, 'wx-transaction', 'wx-test', 'mch-test',
            'openid-test', 10000, 'CNY', timezone.now(), payer_total_fen=10000, payer_currency='CNY')

    def create(self):
        return create_test_session(self.intent.pk, self.account.pk, payment_deadline=self.deadline)


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True)
class SessionRecordTest(SessionFixture, TestCase):
    def test_owner_reuses_same_session_and_cannot_change_frozen_deadline(self):
        self.assertEqual(self.create().state, 'PREPARED')
        self.assertEqual(self.create().state, 'PREPARED')
        self.assertEqual(DirectPaymentSession.objects.count(), 1)
        other = get_or_create_account_for_legacy_user(make_user())
        with self.assertRaises(DirectPaymentError):
            resume_test_session(self.intent.pk, other.pk)
        with self.assertRaises(DirectPaymentError):
            create_test_session(self.intent.pk, self.account.pk, payment_deadline=self.deadline + timedelta(minutes=1))

    def test_cancellation_is_not_channel_close_and_paid_event_is_held(self):
        self.create()
        result = request_test_cancellation(self.intent.pk, self.account.pk)
        self.assertEqual((result.state, result.cancel_requested, result.fake_prepay_id), ('CANCEL_REQUESTED', True, None))
        self.assertTrue(request_test_cancellation(self.intent.pk, self.account.pk).cancel_requested)
        order = Order.objects.get(pk=self.intent.order_id)
        self.assertEqual((order.status, order.payment_status, order.refunded_at), ('PENDING', 'UNPAID', None))
        with patch('orders.direct_payment._notify_paid') as notify:
            paid = confirm_verified_payment(order.pk, self.evidence)
            notify.assert_not_called()
        self.assertEqual((paid.status, paid.review_reason, paid.transaction_id),
                         ('REVIEW', 'CANCEL_PAYMENT_CONFLICT', self.evidence.transaction_id))
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status, order.pool_entered_at), ('PENDING', 'UNPAID', None))
        self.assertFalse(DirectPaymentOutbox.objects.filter(intent=paid).exists())
        self.assertEqual(confirm_verified_payment(order.pk, self.evidence).status, 'REVIEW')
        self.assertEqual(resume_test_session(self.intent.pk, self.account.pk).state, 'PAYMENT_REVIEW')

    def test_historical_without_session_keeps_payment_behavior(self):
        self.assertFalse(DirectPaymentSession.objects.filter(intent=self.intent).exists())
        with patch('orders.direct_payment._notify_paid'):
            result = confirm_verified_payment(self.intent.order_id, self.evidence)
        self.assertEqual(result.status, 'PAID')
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'PAID')

    def test_session_identity_tamper_stops_dispatch_but_preserves_payment_evidence(self):
        self.create()
        DirectPaymentSession.objects.filter(intent=self.intent).update(mchid='other')
        with patch('orders.direct_payment._notify_paid'):
            result = confirm_verified_payment(self.intent.order_id, self.evidence)
        self.assertEqual((result.status, result.transaction_id), ('REVIEW', 'wx-transaction'))
        self.assertFalse(DirectPaymentOutbox.objects.filter(intent=result).exists())

    def test_deadline_is_frozen_and_timezone_normalized(self):
        local_deadline = self.deadline.astimezone(datetime_timezone(timedelta(hours=8)))
        create_test_session(self.intent.pk, self.account.pk, payment_deadline=local_deadline)
        self.assertEqual(resume_test_session(self.intent.pk, self.account.pk).state, 'PREPARED')
        DirectPaymentSession.objects.filter(intent=self.intent).update(payment_deadline=self.deadline + timedelta(minutes=1))
        with self.assertRaises(DirectPaymentError):
            resume_test_session(self.intent.pk, self.account.pk)
        with patch('orders.direct_payment._notify_paid'):
            result = confirm_verified_payment(self.intent.order_id, self.evidence)
        self.assertEqual((result.status, result.transaction_id), ('REVIEW', 'wx-transaction'))
        self.assertFalse(DirectPaymentOutbox.objects.filter(intent=result).exists())

    def test_paid_order_cannot_be_relabelled_as_unpaid_cancel(self):
        self.create()
        with patch('orders.direct_payment._notify_paid'):
            confirm_verified_payment(self.intent.order_id, self.evidence)
        with self.assertRaises(DirectPaymentError):
            request_test_cancellation(self.intent.pk, self.account.pk)
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'PAID')
        self.assertEqual(resume_test_session(self.intent.pk, self.account.pk).state, 'PAID')


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True)
class FakePrepayTest(SessionFixture, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.create()

    def test_single_fake_request_outside_transaction_and_owner_recovery(self):
        calls = []
        def fake(snapshot):
            self.assertFalse(connection.in_atomic_block)
            calls.append(snapshot)
            return 'fake-prepay-1'
        gateway = FakePrepayGateway(fake)
        result = request_fake_prepay(self.intent.pk, self.account.pk, gateway=gateway)
        self.assertEqual((result.state, result.fake_prepay_id), ('PAYABLE', 'fake-prepay-1'))
        self.assertEqual(resume_test_session(self.intent.pk, self.account.pk).fake_prepay_id, 'fake-prepay-1')
        self.assertEqual(request_fake_prepay(self.intent.pk, self.account.pk, gateway=gateway), result)
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0].merchant_order_no, calls[0].mchid, calls[0].amount_fen),
                         (self.intent.merchant_order_no, self.intent.mchid, self.intent.amount_fen))

    def test_timeout_and_expired_lease_never_resend(self):
        calls = []
        def fake(snapshot):
            calls.append(snapshot)
            raise TimeoutError('unknown')
        gateway = FakePrepayGateway(fake)
        self.assertEqual(request_fake_prepay(self.intent.pk, self.account.pk, gateway=gateway).state, 'UNKNOWN')
        self.assertEqual(request_fake_prepay(self.intent.pk, self.account.pk, gateway=gateway).state, 'UNKNOWN')
        self.assertEqual(len(calls), 1)
        session = DirectPaymentSession.objects.get(intent=self.intent)
        session.state, session.lease_until = 'SENDING', timezone.now() - timedelta(seconds=1)
        session.save(update_fields=['state', 'lease_until'])
        self.assertEqual(request_fake_prepay(self.intent.pk, self.account.pk, gateway=gateway).state, 'UNKNOWN')
        self.assertEqual(len(calls), 1)
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'UNPAID')

    def test_cancellation_while_fake_request_runs_wins_over_late_response(self):
        def fake(snapshot):
            request_test_cancellation(self.intent.pk, self.account.pk)
            return 'fake-after-cancel'
        result = request_fake_prepay(self.intent.pk, self.account.pk, gateway=FakePrepayGateway(fake))
        self.assertEqual((result.state, result.fake_prepay_id), ('CANCEL_REQUESTED', None))
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).status, 'PENDING')
        self.assertEqual(DirectPaymentSessionObservation.objects.get(session__intent=self.intent).outcome, 'VALID')

    def test_response_after_lease_expires_is_not_exposed_or_resent(self):
        calls = []
        def fake(snapshot):
            calls.append(snapshot)
            DirectPaymentSession.objects.filter(intent=self.intent).update(lease_until=timezone.now()-timedelta(seconds=1))
            return 'fake-too-late'
        gateway = FakePrepayGateway(fake)
        result = request_fake_prepay(self.intent.pk, self.account.pk, gateway=gateway)
        self.assertEqual((result.state, result.fake_prepay_id), ('UNKNOWN', None))
        self.assertEqual(request_fake_prepay(self.intent.pk, self.account.pk, gateway=gateway).state, 'UNKNOWN')
        self.assertEqual(len(calls), 1)
        observation = DirectPaymentSessionObservation.objects.get(session__intent=self.intent)
        self.assertEqual((observation.outcome, observation.fake_prepay_id), ('VALID', 'fake-too-late'))

    def test_disabled_owner_does_not_lose_fake_response_or_get_parameters(self):
        from club_accounts.models import ClubAccount
        def fake(snapshot):
            ClubAccount.objects.filter(pk=self.account.pk).update(is_active=False)
            return 'fake-private-result'
        result = request_fake_prepay(self.intent.pk, self.account.pk, gateway=FakePrepayGateway(fake))
        self.assertEqual((result.state, result.fake_prepay_id), ('UNKNOWN', None))
        observation = DirectPaymentSessionObservation.objects.get(session__intent=self.intent)
        self.assertEqual((observation.outcome, observation.fake_prepay_id), ('VALID', 'fake-private-result'))
        with self.assertRaises(DirectPaymentError):
            resume_test_session(self.intent.pk, self.account.pk)
        ClubAccount.objects.filter(pk=self.account.pk).update(is_active=True)
        self.assertEqual(resume_test_session(self.intent.pk, self.account.pk).state, 'UNKNOWN')
        self.assertIsNone(resume_test_session(self.intent.pk, self.account.pk).fake_prepay_id)
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'UNPAID')

    def test_rebound_openid_records_generic_error_and_withholds_parameters(self):
        from club_accounts.models import ClubAccount
        def fake(snapshot):
            ClubAccount.objects.filter(pk=self.account.pk).update(openid='other-openid')
            raise TimeoutError('do-not-store-sensitive-error')
        result = request_fake_prepay(self.intent.pk, self.account.pk, gateway=FakePrepayGateway(fake))
        self.assertEqual((result.state, result.fake_prepay_id), ('UNKNOWN', None))
        observation = DirectPaymentSessionObservation.objects.get(session__intent=self.intent)
        self.assertEqual((observation.outcome, observation.fake_prepay_id), ('ERROR', ''))
        self.assertNotIn('do-not-store-sensitive-error', str(observation.__dict__))
        with self.assertRaises(DirectPaymentError):
            resume_test_session(self.intent.pk, self.account.pk)

    def test_changed_account_cycle_is_reconciled_without_exposing_fake_id(self):
        from club_accounts.models import ClubAccount
        def fake(snapshot):
            ClubAccount.objects.filter(pk=self.account.pk).update(business_cycle=self.account.business_cycle+1)
            return 'fake-after-cycle-change'
        result = request_fake_prepay(self.intent.pk, self.account.pk, gateway=FakePrepayGateway(fake))
        self.assertEqual((result.state, result.fake_prepay_id), ('UNKNOWN', None))
        self.assertEqual(DirectPaymentSessionObservation.objects.get(session__intent=self.intent).outcome, 'VALID')
        with self.assertRaises(DirectPaymentError):
            resume_test_session(self.intent.pk, self.account.pk)

    def test_invalid_oversized_fake_response_is_classified_without_storing_body(self):
        secret = 'sensitive' * 20
        result = request_fake_prepay(self.intent.pk, self.account.pk, gateway=FakePrepayGateway(lambda _: secret))
        self.assertEqual((result.state, result.fake_prepay_id), ('UNKNOWN', None))
        observation = DirectPaymentSessionObservation.objects.get(session__intent=self.intent)
        self.assertEqual((observation.outcome, observation.fake_prepay_id), ('INVALID', ''))

    def test_interrupted_worker_is_recovered_internally_without_resending(self):
        from club_accounts.models import ClubAccount
        calls = []
        def fake(snapshot):
            calls.append(snapshot)
            raise KeyboardInterrupt()
        gateway = FakePrepayGateway(fake)
        with self.assertRaises(KeyboardInterrupt):
            request_fake_prepay(self.intent.pk, self.account.pk, gateway=gateway)
        DirectPaymentSession.objects.filter(intent=self.intent).update(lease_until=timezone.now()-timedelta(seconds=1))
        ClubAccount.objects.filter(pk=self.account.pk).update(is_active=False)
        self.assertEqual(recover_test_sessions(), [self.intent.pk])
        self.assertEqual(recover_test_sessions(), [])
        self.assertEqual(DirectPaymentSession.objects.get(intent=self.intent).state, 'UNKNOWN')
        self.assertEqual(len(calls), 1)
        self.assertFalse(DirectPaymentSessionObservation.objects.filter(session__intent=self.intent).exists())

    def test_expired_payable_projection_does_not_claim_closed(self):
        request_fake_prepay(self.intent.pk, self.account.pk, gateway=FakePrepayGateway(lambda _: 'fake'))
        with patch('orders.direct_payment_session.timezone.now', return_value=self.deadline + timedelta(seconds=1)):
            result = resume_test_session(self.intent.pk, self.account.pk)
        self.assertEqual((result.state, result.fake_prepay_id), ('EXPIRED', None))
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).status, 'PENDING')

    def test_one_clock_value_controls_state_and_parameter_visibility(self):
        request_fake_prepay(self.intent.pk, self.account.pk, gateway=FakePrepayGateway(lambda _: 'fake'))
        before = self.deadline - timedelta(microseconds=1)
        after = self.deadline + timedelta(microseconds=1)
        with patch('orders.direct_payment_session.timezone.now', side_effect=[before, after]) as clock:
            result = resume_test_session(self.intent.pk, self.account.pk)
        self.assertEqual((result.state, result.fake_prepay_id), ('PAYABLE', 'fake'))
        clock.assert_called_once()
        with patch('orders.direct_payment_session.timezone.now', return_value=after):
            result = resume_test_session(self.intent.pk, self.account.pk)
        self.assertEqual((result.state, result.fake_prepay_id), ('EXPIRED', None))

    def test_failed_recovery_backs_off_and_next_session_is_not_starved(self):
        from orders.direct_payment_session import _locked_intent_session
        user = self.intent.order.customer
        request = SimpleNamespace(account=self.account, legacy_user=user, data={
            'service_id': self.intent.order.service_id, 'expected_amount': 10000,
            'client_request_id': 'test-session-second',
        })
        second = prepare_direct_order(request, appid='wx-test', mchid='mch-test')
        create_test_session(second.pk, self.account.pk, payment_deadline=self.deadline)
        DirectPaymentSession.objects.filter(intent_id__in=[self.intent.pk, second.pk]).update(
            state='SENDING', lease_until=timezone.now()-timedelta(seconds=1))
        def fail_first(intent_id):
            if intent_id == self.intent.pk:
                raise RuntimeError('sensitive-internal-error')
            return _locked_intent_session(intent_id)
        with patch('orders.direct_payment_session._locked_intent_session', side_effect=fail_first), \
             self.assertLogs('orders.direct_payment_session', level='WARNING') as logs:
            self.assertEqual(recover_test_sessions(limit=1), [])
        self.assertNotIn('sensitive-internal-error', '\n'.join(logs.output))
        first_row = DirectPaymentSession.objects.get(intent=self.intent)
        self.assertEqual((first_row.state, first_row.recovery_failures, first_row.recovery_last_error),
                         ('SENDING', 1, 'RECOVERY_FAILED'))
        self.assertGreater(first_row.recovery_retry_at, timezone.now())
        self.assertEqual(recover_test_sessions(limit=1), [second.pk])
        DirectPaymentSession.objects.filter(pk=first_row.pk).update(recovery_retry_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(recover_test_sessions(limit=1), [self.intent.pk])
        first_row.refresh_from_db()
        self.assertEqual((first_row.state, first_row.recovery_failures, first_row.recovery_last_error),
                         ('UNKNOWN', 1, 'RECOVERY_FAILED'))

    def test_failed_recovery_does_not_stop_other_items_in_same_batch(self):
        from orders.direct_payment_session import _locked_intent_session
        request = SimpleNamespace(account=self.account, legacy_user=self.intent.order.customer, data={
            'service_id': self.intent.order.service_id, 'expected_amount': 10000,
            'client_request_id': 'test-session-other',
        })
        second = prepare_direct_order(request, appid='wx-test', mchid='mch-test')
        create_test_session(second.pk, self.account.pk, payment_deadline=self.deadline)
        DirectPaymentSession.objects.filter(intent_id__in=[self.intent.pk, second.pk]).update(
            state='SENDING', lease_until=timezone.now()-timedelta(seconds=1))
        def fail_first(intent_id):
            if intent_id == self.intent.pk:
                raise RuntimeError('sensitive-internal-error')
            return _locked_intent_session(intent_id)
        with patch('orders.direct_payment_session._locked_intent_session', side_effect=fail_first), \
             self.assertLogs('orders.direct_payment_session', level='WARNING'):
            self.assertEqual(recover_test_sessions(limit=2), [second.pk])
        self.assertEqual(DirectPaymentSession.objects.get(intent=self.intent).state, 'SENDING')
        self.assertEqual(DirectPaymentSession.objects.get(intent=second).state, 'UNKNOWN')

    def test_payment_while_fake_request_runs_preserves_payment_and_hides_fake_id(self):
        def fake(snapshot):
            with patch('orders.direct_payment._notify_paid'):
                confirm_verified_payment(self.intent.order_id, self.evidence)
            return 'fake-after-paid'
        result = request_fake_prepay(self.intent.pk, self.account.pk, gateway=FakePrepayGateway(fake))
        self.assertIsNone(result.fake_prepay_id)
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'PAID')

    def test_deadline_expiry_never_means_closed_and_real_gateway_is_refused(self):
        DirectPaymentSession.objects.filter(intent=self.intent).update(payment_deadline=timezone.now()-timedelta(seconds=1))
        with self.assertRaises(DirectPaymentError):
            request_fake_prepay(self.intent.pk, self.account.pk, gateway=FakePrepayGateway(lambda _: 'fake'))
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).status, 'PENDING')
        with self.assertRaises(DirectPaymentError):
            request_fake_prepay(self.intent.pk, self.account.pk, gateway=object())

    def test_fake_io_is_rejected_under_caller_transaction(self):
        calls = []
        with transaction.atomic(), self.assertRaises(DirectPaymentError):
            request_fake_prepay(self.intent.pk, self.account.pk, gateway=FakePrepayGateway(lambda x: calls.append(x)))
        self.assertEqual(calls, [])


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True)
@skipUnlessDBFeature('has_select_for_update')
class SessionConcurrencyTest(SessionFixture, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.create()

    def test_two_workers_never_send_twice(self):
        entered, release = Event(), Event()
        calls = []
        def fake(snapshot):
            calls.append(snapshot)
            entered.set()
            if not release.wait(10):
                raise AssertionError('worker was not released')
            return 'fake-one'
        gateway = FakePrepayGateway(fake)
        def worker():
            close_old_connections()
            try:
                return request_fake_prepay(self.intent.pk, self.account.pk, gateway=gateway).state
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(worker)
            try:
                self.assertTrue(entered.wait(10))
                self.assertEqual(pool.submit(worker).result(timeout=10), 'SENDING')
            finally:
                release.set()
            self.assertEqual(first.result(timeout=10), 'PAYABLE')
        self.assertEqual(len(calls), 1)

    def test_cancel_and_verified_payment_serialize_on_order(self):
        barrier = Barrier(2)
        def worker(kind):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                if kind == 'cancel':
                    try:
                        return request_test_cancellation(self.intent.pk, self.account.pk).state
                    except DirectPaymentError:
                        return 'rejected'
                return confirm_verified_payment(self.intent.order_id, self.evidence).status
            finally:
                close_old_connections()
        with patch('orders.direct_payment._notify_paid'), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, ('cancel', 'pay')))
        self.intent.refresh_from_db()
        session = DirectPaymentSession.objects.get(intent=self.intent)
        if session.cancel_requested_at:
            self.assertEqual(self.intent.status, 'REVIEW')
            self.assertFalse(DirectPaymentOutbox.objects.filter(intent=self.intent).exists())
        else:
            self.assertEqual(self.intent.status, 'PAID')
            self.assertIn('rejected', results)

    def test_owner_deactivation_while_fake_request_is_running_preserves_observation(self):
        from club_accounts.models import ClubAccount
        entered, release = Event(), Event()
        def fake(snapshot):
            entered.set()
            if not release.wait(10):
                raise AssertionError('worker was not released')
            return 'fake-result-after-disable'
        def worker():
            close_old_connections()
            try:
                return request_fake_prepay(self.intent.pk, self.account.pk, gateway=FakePrepayGateway(fake))
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(worker)
            try:
                self.assertTrue(entered.wait(10))
                ClubAccount.objects.filter(pk=self.account.pk).update(is_active=False)
            finally:
                release.set()
            result = future.result(timeout=20)
        self.assertEqual((result.state, result.fake_prepay_id), ('UNKNOWN', None))
        observation = DirectPaymentSessionObservation.objects.get(session__intent=self.intent)
        self.assertEqual((observation.outcome, observation.fake_prepay_id), ('VALID', 'fake-result-after-disable'))
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'UNPAID')
