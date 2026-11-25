"""Reservation decisions use durable tenant-DB clock evidence.

SQLite is an explicit test seam only.  Production timing is covered by the
separate real-MySQL module; these tests never claim SQLite is a trusted clock.
"""
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from orders.models import Reservation, ReservationClockReceipt, ReservationSeatEvent
from orders.reservation_clock import (
    ClockSample,
    ClockConflict,
    ClockUnavailable,
    classify_bounds,
    recover_receipts,
    sample,
    valid_bounds,
)
from orders.reservation_expiration import expire_due_reservations
from orders.reservation_services import create_reservation
from orders.reservation_team import create_team_reservation, decide, replace
from orders.tests.factories import make_provider, make_service
from orders.tests.test_reservation_api import ReservationApiTestBase, prepare_reservation_payment, slot
from users.models import EscortProfile
from wallet.models import IdempotencyKey, Transaction, Wallet


CLOCK_SETTINGS = dict(
    RESERVATION_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True,
    LEGACY_FORCE_AUTH_COMPAT=False,
    PROVIDER_WECHAT_GATE_ENABLED=False,
    KOOK_ENABLED=False,
    WECOM_DISPATCH_ENABLED=False,
    CELERY_TASK_ALWAYS_EAGER=False,
)


def evidence_sample(instant, *, base=None, before_ns=1_000_000_000, elapsed_ns=1_000_000):
    """Build coherent evidence around a real SQLite test connection."""
    base = base or sample()
    after_ns = before_ns + elapsed_ns
    return ClockSample(instant, base.driver, {
        **base.evidence,
        'process_id': base.evidence['process_id'],
        'before_ns': before_ns,
        'after_ns': after_ns,
    })


def client_for(account):
    return APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])


class ReservationClockContractTest(TransactionTestCase):
    @override_settings(RESERVATION_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False)
    def test_sqlite_is_rejected_by_default_and_never_falls_back_to_application_time(self):
        with patch('django.utils.timezone.now', side_effect=AssertionError('application clock fallback')):
            with self.assertRaises(ClockUnavailable):
                sample()

    @override_settings(**CLOCK_SETTINGS)
    def test_samples_are_aware_utc_and_include_connection_and_monotonic_evidence(self):
        observed = sample()
        self.assertEqual(observed.instant.utcoffset(), timedelta(0))
        self.assertTrue(observed.evidence['database_identity'])
        self.assertGreater(observed.evidence['connection_id'], 0)
        self.assertLessEqual(observed.evidence['before_ns'], observed.evidence['after_ns'])

    def test_bounds_reject_connection_changes_backwards_clocks_and_inconsistent_elapsed_time(self):
        instant = datetime(2026, 11, 25, 12, tzinfo=dt_timezone.utc)
        lower = evidence_sample(instant, before_ns=1_000_000_000)
        upper = evidence_sample(instant + timedelta(seconds=1), base=lower,
                                before_ns=2_000_000_000)
        self.assertTrue(valid_bounds(lower, upper))
        changed = ClockSample(upper.instant, object(), {
            **upper.evidence, 'connection_id': upper.evidence['connection_id'] + 1,
        })
        self.assertFalse(valid_bounds(lower, changed))
        backwards = evidence_sample(instant - timedelta(seconds=1), base=lower,
                                    before_ns=2_000_000_000)
        self.assertFalse(valid_bounds(lower, backwards))
        impossible_elapsed = evidence_sample(instant + timedelta(seconds=1), base=lower,
                                             before_ns=1_500_000_000)
        self.assertFalse(valid_bounds(lower, impossible_elapsed))

    def test_deadline_classification_is_conservative_at_the_boundary(self):
        deadline = datetime(2026, 11, 25, 12, tzinfo=dt_timezone.utc)
        self.assertEqual(classify_bounds(
            lower=deadline - timedelta(seconds=1),
            upper=deadline - timedelta(microseconds=2),
            precision_us=1, deadline=deadline), ('BEFORE', ''))
        self.assertEqual(classify_bounds(
            lower=deadline - timedelta(seconds=1), upper=deadline,
            precision_us=1, deadline=deadline), ('REVIEW', 'CROSSED_CUTOFF'))
        self.assertEqual(classify_bounds(
            lower=deadline, upper=deadline + timedelta(seconds=1),
            precision_us=1, deadline=deadline), ('AFTER', ''))
        self.assertEqual(classify_bounds(
            lower=deadline - timedelta(seconds=1), upper=None,
            precision_us=1, deadline=deadline), ('REVIEW', 'ACK_MISSING'))
        self.assertEqual(classify_bounds(
            lower=deadline, upper=deadline - timedelta(seconds=1),
            precision_us=1, deadline=deadline), ('REVIEW', 'CLOCK_BACKWARDS'))


@override_settings(**CLOCK_SETTINGS)
class ReservationClockWorkflowTest(ReservationApiTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=None)
        self.boss_client = client_for(self.boss_account)
        self.provider_client = client_for(self.provider_account)
        self.start = slot(hour=10)
        self.payload = self.create_payload(start_time=self.start.isoformat(),
                                           end_time=(self.start + timedelta(hours=1)).isoformat())

    def create_booking(self, **overrides):
        response = self.boss_client.post('/api/orders/reservations/',
                                         {**self.payload, **overrides}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        return Reservation.objects.get(pk=response.data['data']['id'])

    def confirm(self, booking, key=None):
        payload = {'decision_key': key} if key else {}
        return self.provider_client.post(f'/api/orders/reservations/{booking.pk}/confirm/',
                                         payload, format='json')

    def make_team(self):
        members = [self.provider_account]
        for _ in range(2):
            user = make_provider(deposit_mode='EARNINGS')
            from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            EscortProfile.objects.filter(user=user).update(account=account)
            members.append(account)
        service = make_service(price=10000, billing_provider_count=2,
                               fulfillment_provider_count=2, price_unit='小时')
        booking = create_team_reservation(customer_account=self.boss_account, customer=self.boss,
            providers=members[:2], service=service, start_time=self.start,
            end_time=self.start + timedelta(minutes=90))
        return booking, members, service

    def test_clock_unavailable_returns_503_without_creating_or_confirming(self):
        with patch('orders.reservation_clock.sample', side_effect=ClockUnavailable('时钟不可用')):
            response = self.boss_client.post('/api/orders/reservations/', self.payload, format='json')
            self.assertEqual(response.status_code, 503)
            self.assertFalse(Reservation.objects.exists())
        booking = self.create_booking()
        with patch('orders.reservation_clock.sample', side_effect=ClockUnavailable('时钟不可用')):
            response = self.confirm(booking)
            self.assertEqual(response.status_code, 503)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Reservation.Status.PENDING)

    def test_lock_wait_past_confirmation_deadline_expires_without_confirming(self):
        booking = self.create_booking()
        deadline = booking.confirmation_deadline
        before = evidence_sample(deadline - timedelta(seconds=1))
        after = evidence_sample(deadline + timedelta(seconds=1), base=before,
                                before_ns=2_000_000_000)
        with patch('orders.reservation_clock.sample', side_effect=[before, after, after]):
            response = self.confirm(booking)
        self.assertEqual(response.status_code, 409, response.data)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Reservation.Status.EXPIRED)
        self.assertIsNone(booking.confirmed_at)
        self.assertFalse(booking.status_logs.filter(action='CONFIRM').exists())
        self.assertTrue(booking.clock_receipts.filter(action='EXPIRE', state='AFTER').exists())

    def test_commit_crossing_confirmation_deadline_is_review_and_rolls_decision_forward(self):
        booking = self.create_booking()
        deadline = booking.confirmation_deadline
        before = evidence_sample(deadline - timedelta(seconds=1))
        after = evidence_sample(deadline + timedelta(seconds=1), base=before,
                                before_ns=3_001_000_000)
        with patch('orders.reservation_clock.sample', side_effect=[before, before, after]):
            response = self.confirm(booking, 'crossed-confirm')
        self.assertEqual(response.status_code, 409, response.data)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Reservation.Status.PENDING)
        self.assertIsNone(booking.confirmed_at)
        self.assertIsNone(booking.payment_deadline)
        receipt = booking.clock_receipts.get(decision_key='crossed-confirm')
        self.assertEqual((receipt.state, receipt.review_reason), ('REVIEW', 'CROSSED_CUTOFF'))
        self.assertEqual(booking.status_logs.filter(action='CONFIRM').count(), 1)
        self.assertEqual(booking.seat_events.count(), 0)

    def test_review_receipt_blocks_confirm_replace_quote_and_convert_without_debit(self):
        prepare_reservation_payment(self.boss_account)
        booking = self.create_booking()
        receipt = ReservationClockReceipt.objects.create(
            reservation=booking, action='QUOTE', decision_key='review-gate',
            deadline=booking.confirmation_deadline, lower_at=timezone.now(),
            precision_us=1, database_identity='sqlite-test', connection_id=1,
            process_id=1, monotonic_before_ns=1, monotonic_after_ns=2,
            state='REVIEW', review_reason='CONNECTION_CHANGED',
        )
        self.assertEqual(receipt.state, 'REVIEW')
        self.assertEqual(self.confirm(booking).status_code, 409)
        self.assertEqual(self.boss_client.post(
            f'/api/orders/reservations/{booking.pk}/replace-provider/',
            {'seat_number': 1, 'provider_account_id': self.provider_account.pk},
            format='json').status_code, 409)
        self.assertEqual(self.boss_client.get(
            f'/api/orders/reservations/{booking.pk}/quote/').status_code, 409)
        balance = Wallet.objects.get(account=self.boss_account).balance
        response = self.boss_client.post(f'/api/orders/reservations/{booking.pk}/convert/',
            {'client_request_id': 'review-convert'}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Wallet.objects.get(account=self.boss_account).balance, balance)
        self.assertFalse(Transaction.objects.filter(wallet__account=self.boss_account, tx_type='PAY').exists())
        self.assertFalse(IdempotencyKey.objects.filter(account=self.boss_account,
                                                       request_id='review-convert').exists())

    def test_lost_upper_ack_and_original_key_replay_do_not_duplicate_confirmation(self):
        booking = self.create_booking()
        original_deadline = booking.confirmation_deadline
        before = evidence_sample(original_deadline - timedelta(seconds=2))
        with patch('orders.reservation_clock.sample', side_effect=[before, before, ClockUnavailable('ACK lost')]):
            response = self.confirm(booking, 'lost-upper-ack')
        self.assertEqual(response.status_code, 503, response.data)
        receipt = booking.clock_receipts.get(decision_key='lost-upper-ack')
        self.assertEqual(receipt.state, 'PENDING')
        with patch('orders.reservation_clock.sample', side_effect=AssertionError('replay must read receipt')):
            replay = self.confirm(booking, 'lost-upper-ack')
        self.assertEqual(replay.status_code, 503, replay.data)
        booking.refresh_from_db()
        self.assertEqual(booking.confirmation_deadline, original_deadline)
        self.assertEqual(booking.status_logs.filter(action='CONFIRM').count(), 1)

    def test_single_and_double_confirmation_write_before_receipts(self):
        single = self.create_booking(start_time=slot(hour=6), end_time=slot(hour=7))
        self.assertEqual(self.confirm(single, 'single-confirm').data['code'], 0)
        self.assertEqual(single.clock_receipts.get(decision_key='single-confirm').state, 'BEFORE')
        team, members, _ = self.make_team()
        booking, error = decide(team.pk, members[0], accept=True, decision_key='team-first')
        self.assertEqual((booking.status, error), ('PENDING', ''))
        booking, error = decide(team.pk, members[1], accept=True, decision_key='team-second')
        self.assertEqual((booking.status, error), ('CONFIRMED', ''))
        self.assertEqual(set(team.clock_receipts.filter(
            action__in=['TEAM_CONFIRM', 'TEAM_REJECT']).values_list('decision_key', flat=True)),
                         {'team-first', 'team-second'})
        self.assertEqual(team.clock_receipts.count(), 3)  # CREATE + one decision per seat
        self.assertTrue(all(row.state == 'BEFORE' for row in team.clock_receipts.all()))

    def test_double_reject_and_replacement_replay_keep_one_event_and_deadline(self):
        team, members, _ = self.make_team()
        booking, error = decide(team.pk, members[0], accept=True, decision_key='first-accept')
        self.assertEqual(error, '')
        booking, error = decide(team.pk, members[1], accept=False, reason='无法赴约',
                                decision_key='second-reject')
        self.assertEqual(error, '')
        booking, error = replace(team.pk, self.boss_account, 2, members[2],
                                 client_request_id='replace-once')
        self.assertEqual(error, '')
        deadline = booking.seats.get(number=2).confirmation_deadline
        events = booking.seat_events.count()
        with patch('orders.reservation_clock.sample', side_effect=AssertionError('replay must read receipt')):
            booking, error = replace(team.pk, self.boss_account, 2, members[2],
                                     client_request_id='replace-once')
        self.assertEqual(error, '')
        self.assertEqual(booking.seats.get(number=2).confirmation_deadline, deadline)
        self.assertEqual(booking.seat_events.count(), events)
        self.assertEqual(team.clock_receipts.get(decision_key='replace-once').state, 'BEFORE')

    def test_replacement_crossing_start_restores_only_target_seat_and_keeps_evidence(self):
        team, members, _ = self.make_team()
        decide(team.pk, members[0], accept=True, decision_key='cross-first-accept')
        decide(team.pk, members[1], accept=False, reason='无法赴约',
               decision_key='cross-second-reject')
        before = evidence_sample(team.start_time - timedelta(seconds=1))
        after = evidence_sample(team.start_time + timedelta(seconds=1), base=before,
                                before_ns=3_001_000_000)
        event_count = team.seat_events.count()
        with patch('orders.reservation_clock.sample', side_effect=[before, before, after]):
            with self.assertRaises(ClockConflict):
                replace(team.pk, self.boss_account, 2, members[2],
                        client_request_id='replace-crossed')
        team.refresh_from_db()
        first = team.seats.get(number=1)
        second = team.seats.get(number=2)
        self.assertEqual((team.status, first.status, first.provider_account_id),
                         ('PENDING', 'CONFIRMED', members[0].pk))
        self.assertEqual((second.status, second.provider_account_id, second.confirmation_deadline),
                         ('VACANT', None, None))
        self.assertEqual(team.seat_events.count(), event_count + 1)  # uncertain invitation remains audit evidence
        receipt = team.clock_receipts.get(decision_key='replace-crossed')
        self.assertEqual((receipt.state, receipt.review_reason), ('REVIEW', 'CROSSED_CUTOFF'))
        with patch('orders.reservation_clock.sample', side_effect=AssertionError('REVIEW must block replay')):
            with self.assertRaises(ClockConflict):
                replace(team.pk, self.boss_account, 2, members[2],
                        client_request_id='replace-crossed')
        self.assertEqual(team.seat_events.count(), event_count + 1)

    def test_expiration_scan_clock_failure_does_not_infer_or_strand_later_candidates(self):
        first = self.create_booking(start_time=slot(hour=10))
        second = self.create_booking(start_time=slot(hour=12), end_time=slot(hour=13))
        Reservation.objects.filter(pk=first.pk).update(confirmation_deadline=timezone.now()-timedelta(seconds=2))
        Reservation.objects.filter(pk=second.pk).update(confirmation_deadline=timezone.now()-timedelta(seconds=2))
        with patch('orders.reservation_clock.sample', side_effect=ClockUnavailable('store DB disconnected')):
            with self.assertRaises(ClockUnavailable):
                expire_due_reservations()
        self.assertEqual(Reservation.objects.filter(status='PENDING').count(), 2)
        real_sample = sample
        calls = []
        def fail_first_then_sample():
            calls.append(1)
            if len(calls) == 2:  # first candidate's locked recheck
                raise ClockUnavailable('first lock disconnected')
            return real_sample()
        with patch('orders.reservation_clock.sample', side_effect=fail_first_then_sample):
            self.assertEqual(expire_due_reservations(), 1)
        statuses = dict(Reservation.objects.filter(pk__in=[first.pk, second.pk]).values_list('pk', 'status'))
        self.assertEqual(statuses[first.pk], 'PENDING')
        self.assertEqual(statuses[second.pk], 'EXPIRED')

    def test_quote_crossing_deadline_returns_conflict_and_convert_recheck_prevents_debit(self):
        prepare_reservation_payment(self.boss_account)
        booking = self.create_booking()
        self.assertEqual(self.confirm(booking).data['code'], 0)
        booking.refresh_from_db()
        deadline = booking.payment_deadline
        before = evidence_sample(deadline - timedelta(seconds=2))
        after = evidence_sample(deadline + timedelta(seconds=1), base=before,
                                before_ns=4_001_000_000)
        with patch('orders.reservation_clock.sample', side_effect=[before, before, after]):
            quote = self.boss_client.get(f'/api/orders/reservations/{booking.pk}/quote/')
        self.assertEqual(quote.status_code, 409, quote.data)
        self.assertEqual(quote.data['msg'], '预约时钟回执需人工核对', quote.data)
        self.assertEqual(booking.clock_receipts.get(action='QUOTE').review_reason, 'CROSSED_CUTOFF')
        booking.refresh_from_db()
        updated_at = booking.updated_at
        self.assertEqual(recover_receipts(reservation=booking), 0)
        booking.refresh_from_db()
        self.assertEqual(booking.updated_at, updated_at)  # read-only REVIEW has nothing to undo

        # A REVIEW quote must block conversion, so exercise the independent
        # pre-debit crossing on another reservation rather than bypassing that gate.
        conversion = self.create_booking(start_time=slot(hour=12), end_time=slot(hour=13))
        self.assertEqual(self.confirm(conversion).data['code'], 0)
        conversion.refresh_from_db()
        deadline = conversion.payment_deadline
        before = evidence_sample(deadline - timedelta(seconds=2))
        after = evidence_sample(deadline + timedelta(seconds=1), base=before,
                                before_ns=4_001_000_000)
        balance = Wallet.objects.get(account=self.boss_account).balance
        with patch('orders.reservation_clock.sample', side_effect=[before, before, before, after]):
            response = self.boss_client.post(f'/api/orders/reservations/{conversion.pk}/convert/',
                {'client_request_id': 'crossed-before-debit'}, format='json')
        # Conversion retains the established HTTP-200 business envelope; the
        # durable clock conflict is the non-zero business code and no funds write.
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['code'], 409, response.data)
        self.assertEqual(conversion.clock_receipts.get(action='CONVERT_CHECK').review_reason,
                         'CROSSED_CUTOFF')
        self.assertEqual(Wallet.objects.get(account=self.boss_account).balance, balance)
        self.assertFalse(Transaction.objects.filter(wallet__account=self.boss_account, tx_type='PAY').exists())
        self.assertFalse(IdempotencyKey.objects.filter(account=self.boss_account,
                                                       request_id='crossed-before-debit').exists())

    def test_create_crossing_deadline_is_cancelled_with_review_evidence(self):
        real_sample = sample
        initial = []
        bounded = []

        def controlled_clock():
            if len(initial) < 2:
                observed = real_sample()
                initial.append(observed)
                return observed
            if not bounded:
                # The second sample is the locked creation time; the configured
                # confirmation snapshot is exactly 30 minutes from that value.
                deadline = initial[1].instant + timedelta(minutes=30)
                lower = evidence_sample(deadline - timedelta(seconds=1),
                                        base=initial[1], before_ns=1_000_000_000)
                upper = evidence_sample(deadline + timedelta(seconds=1), base=lower,
                                        before_ns=3_001_000_000)
                bounded.extend([lower, upper])
            return bounded.pop(0)

        with patch('orders.reservation_clock.sample', side_effect=controlled_clock):
            response = self.boss_client.post('/api/orders/reservations/',
                {**self.payload, 'decision_key': 'create-crossed'}, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        booking = Reservation.objects.get()
        self.assertEqual(booking.status, Reservation.Status.CANCELLED)
        self.assertIn('撤销本次创建', booking.cancel_reason)
        receipt = booking.clock_receipts.get(decision_key='create-crossed')
        self.assertEqual((receipt.state, receipt.review_reason), ('REVIEW', 'CROSSED_CUTOFF'))
        self.assertEqual(booking.status_logs.filter(action='CREATE').count(), 1)
        self.assertEqual(booking.status_logs.filter(action='CANCEL').count(), 1)

    def test_same_key_replays_original_result_and_different_content_conflicts(self):
        payload = {**self.payload, 'decision_key': 'create-once'}
        created = self.boss_client.post('/api/orders/reservations/', payload, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        with patch('orders.reservation_views.notify_reservation_after_commit',
                   side_effect=AssertionError('replay must not notify again')):
            replay = self.boss_client.post('/api/orders/reservations/', payload, format='json')
        self.assertEqual(replay.data['code'], 0, replay.data)
        self.assertEqual(replay.data['data']['id'], created.data['data']['id'])
        changed = self.boss_client.post('/api/orders/reservations/',
                                        {**payload, 'remark': '不同内容'}, format='json')
        self.assertEqual(changed.status_code, 409, changed.data)
        self.assertEqual(Reservation.objects.count(), 1)
        original = Reservation.objects.get(pk=created.data['data']['id'])
        self.assertEqual(original.status_logs.filter(action='CREATE').count(), 1)
        self.assertEqual(original.clock_receipts.filter(action='CREATE').count(), 1)

        self.assertEqual(self.confirm(original, 'confirm-once').data['code'], 0)
        with patch('orders.reservation_clock.sample', side_effect=AssertionError('replay must read receipt')):
            confirmed_replay = self.confirm(original, 'confirm-once')
        self.assertEqual(confirmed_replay.data['code'], 0, confirmed_replay.data)
        self.assertEqual(original.status_logs.filter(action='CONFIRM').count(), 1)
        other = self.create_booking(start_time=slot(hour=12), end_time=slot(hour=13))
        with patch('orders.reservation_clock.sample', side_effect=AssertionError('conflict must read receipt')):
            conflict = self.confirm(other, 'confirm-once')
        self.assertEqual(conflict.status_code, 409, conflict.data)
        other.refresh_from_db()
        self.assertEqual(other.status, Reservation.Status.PENDING)
        self.assertFalse(other.status_logs.filter(action='CONFIRM').exists())

        rejected = self.create_booking(start_time=slot(hour=14), end_time=slot(hour=15))
        path = f'/api/orders/reservations/{rejected.pk}/reject/'
        first_reject = self.provider_client.post(
            path, {'decision_key': 'reject-once', 'reason': '原理由'}, format='json')
        self.assertEqual(first_reject.data['code'], 0, first_reject.data)
        changed_reject = self.provider_client.post(
            path, {'decision_key': 'reject-once', 'reason': '不同理由'}, format='json')
        self.assertEqual(changed_reject.status_code, 409, changed_reject.data)
        self.assertEqual(rejected.status_logs.filter(action='REJECT').count(), 1)

    def test_convert_failure_after_probe_binds_original_funds_fingerprint(self):
        prepare_reservation_payment(self.boss_account)
        booking = self.create_booking()
        self.assertEqual(self.confirm(booking).data['code'], 0)
        booking.refresh_from_db()
        quote = self.boss_client.get(f'/api/orders/reservations/{booking.pk}/quote/').data['data']
        payload = {
            'client_request_id': 'convert-funds-fingerprint',
            'expected_amount': quote['amount'],
            'expected_service': quote['service'],
        }
        balance = Wallet.objects.get(account=self.boss_account).balance
        real_sample = sample
        calls = []

        def fail_after_probe():
            calls.append(1)
            # Visibility and locked-expiry checks precede the two probe bounds.
            # The fifth sample is the post-claim, pre-debit observation.
            if len(calls) == 5:
                raise ClockUnavailable('post-probe acknowledgement lost')
            return real_sample()

        with patch('orders.reservation_clock.sample', side_effect=fail_after_probe):
            first = self.boss_client.post(
                f'/api/orders/reservations/{booking.pk}/convert/', payload, format='json')
        self.assertEqual(first.data['code'], 503, first.data)
        self.assertEqual(len(calls), 5)
        rows = list(booking.clock_receipts.values_list('action', 'decision_key', 'state'))
        self.assertTrue(booking.clock_receipts.filter(
            action='CONVERT_CHECK', decision_key=payload['client_request_id']).exists(), rows)
        receipt = booking.clock_receipts.get(
            action='CONVERT_CHECK', decision_key=payload['client_request_id'])
        self.assertEqual(receipt.state, 'BEFORE')
        self.assertEqual(len(receipt.request_fingerprint), 64)
        self.assertEqual(Wallet.objects.get(account=self.boss_account).balance, balance)
        self.assertFalse(Transaction.objects.filter(
            wallet__account=self.boss_account, tx_type='PAY').exists())
        self.assertFalse(IdempotencyKey.objects.filter(
            account=self.boss_account, request_id=payload['client_request_id']).exists())

        changed = self.boss_client.post(
            f'/api/orders/reservations/{booking.pk}/convert/',
            {**payload, 'expected_amount': quote['amount'] + 10}, format='json')
        self.assertEqual(changed.status_code, 409, changed.data)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Reservation.Status.CONFIRMED)
        self.assertIsNone(booking.order)
        self.assertEqual(Wallet.objects.get(account=self.boss_account).balance, balance)
        self.assertFalse(Transaction.objects.filter(
            wallet__account=self.boss_account, tx_type='PAY').exists())

    def test_team_pending_receipt_blocks_other_seat_until_original_recovers(self):
        team, members, _ = self.make_team()
        deadline = team.confirmation_deadline
        before = evidence_sample(deadline - timedelta(seconds=2))
        with patch('orders.reservation_clock.sample',
                   side_effect=[before, before, ClockUnavailable('ACK lost')]):
            with self.assertRaises(ClockUnavailable):
                decide(team.pk, members[0], accept=True, decision_key='team-lost-upper')
        receipt = team.clock_receipts.get(decision_key='team-lost-upper')
        self.assertEqual(receipt.state, 'PENDING')
        self.assertEqual(team.seats.get(number=1).status, 'CONFIRMED')
        with patch('orders.reservation_clock.sample', side_effect=AssertionError('gate must precede sampling')):
            with self.assertRaises(ClockUnavailable):
                decide(team.pk, members[1], accept=True, decision_key='team-must-wait')
        self.assertFalse(team.clock_receipts.filter(decision_key='team-must-wait').exists())
        self.assertEqual(team.seats.get(number=2).status, 'PENDING')

        upper = evidence_sample(deadline - timedelta(seconds=1), base=before,
                                before_ns=2_001_000_000)
        with patch('orders.reservation_clock.sample', return_value=upper):
            self.assertEqual(recover_receipts(reservation=team), 1)
        receipt.refresh_from_db()
        self.assertEqual(receipt.state, 'BEFORE')
        booking, error = decide(team.pk, members[1], accept=True, decision_key='team-after-recovery')
        self.assertEqual((booking.status, error), ('CONFIRMED', ''))
        self.assertEqual(list(booking.seats.values_list('status', flat=True)),
                         ['CONFIRMED', 'CONFIRMED'])

    def test_review_survives_recovery_but_new_due_evidence_can_expire(self):
        booking = self.create_booking()
        review = ReservationClockReceipt.objects.create(
            reservation=booking, action='CONFIRM', decision_key='review-expiry',
            deadline=booking.confirmation_deadline, lower_at=timezone.now(),
            precision_us=1, database_identity='sqlite-test', connection_id=1,
            process_id=1, monotonic_before_ns=1, monotonic_after_ns=2,
            state='REVIEW', review_reason='CROSSED_CUTOFF',
        )
        Reservation.objects.filter(pk=booking.pk).update(
            confirmation_deadline=timezone.now() - timedelta(seconds=1))
        self.assertEqual(expire_due_reservations(), 1)
        booking.refresh_from_db()
        review.refresh_from_db()
        self.assertEqual(booking.status, Reservation.Status.EXPIRED)
        self.assertEqual((review.state, review.review_reason), ('REVIEW', 'CROSSED_CUTOFF'))
        self.assertTrue(booking.clock_receipts.filter(action='EXPIRE', state='AFTER').exists())

    def test_receipt_business_fields_and_finalized_state_cannot_be_modified(self):
        booking = self.create_booking()
        receipt = booking.clock_receipts.first()
        receipt.deadline = timezone.now()
        with self.assertRaises(ValueError):
            receipt.save(update_fields=['deadline'])
        receipt.refresh_from_db()
        with self.assertRaises(ValueError):
            receipt.delete()
