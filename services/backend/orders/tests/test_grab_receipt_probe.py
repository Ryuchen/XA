"""Commit observations bracket registration; they never perform an order action."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone as dt_timezone
from threading import Barrier
from time import sleep
from types import SimpleNamespace
from unittest import TestCase as PureTestCase, skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import DatabaseError, close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user
from console.grab_config import save_grab_window
from orders.grab_pool_snapshot import create_initial_snapshot
from orders.grab_receipt_probe import (classify_bounds, classify_submitted_tier, compare_candidate_bounds, mark_unacknowledged_for_review,
                                       record_intent, unresolved_round)
from orders.grab_team_draft import first_round
from orders.models import Order, OrderGrabReceiptPassEvidence, OrderGrabReceiptProbe
from orders.tests.factories import make_order, make_provider, make_service, make_user
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.tests_hosts import xinan_store


class BoundsTest(PureTestCase):
    def test_formal_cutoff_classification_preserves_uncertain_opening(self):
        from orders.grab_round_opening import classify_window

        opened = datetime(2026, 11, 7, 12, tzinfo=dt_timezone.utc)
        round_ = SimpleNamespace(opened_at=opened)
        cutoff = opened + timedelta(seconds=30)
        with patch('orders.grab_round_opening.cutoff_bounds', return_value=(cutoff, cutoff)):
            lower = opened + timedelta(milliseconds=49)
            self.assertEqual(classify_window(lower=lower, upper=lower, precision_us=50000,
                                             round_=round_, phase='OPEN'), ('REVIEW', 'CROSSED_OPENING'))
            lower = opened + timedelta(milliseconds=50)
            self.assertEqual(classify_window(lower=lower, upper=lower, precision_us=50000,
                                             round_=round_, phase='OPEN'), ('BEFORE', ''))

    def test_deadline_proof_is_conservative_for_each_kind(self):
        end = datetime(2026, 11, 7, 12, tzinfo=dt_timezone.utc)
        def classify(lo, hi, cancel=False):
            return classify_bounds(lower=lo, upper=hi, precision_us=1000, deadline=end, cancellation=cancel)
        self.assertEqual(classify(end-timedelta(seconds=1), end-timedelta(milliseconds=1))[0], 'REVIEW')
        self.assertEqual(classify(end-timedelta(seconds=1), end-timedelta(milliseconds=1, microseconds=1))[0], 'BEFORE')
        self.assertEqual(classify(end-timedelta(seconds=1), end-timedelta(milliseconds=1), True)[0], 'BEFORE')
        self.assertEqual(classify(end-timedelta(milliseconds=1), end)[0], 'REVIEW')
        self.assertEqual(classify(end, end)[0], 'REVIEW')
        self.assertEqual(classify(end+timedelta(milliseconds=1), end+timedelta(milliseconds=1))[0], 'AFTER')
        self.assertEqual(classify(end, end, True)[0], 'REVIEW')
        self.assertEqual(classify(end+timedelta(milliseconds=1), end+timedelta(seconds=1), True)[0], 'REVIEW')
        self.assertEqual(classify(end+timedelta(milliseconds=2), end+timedelta(seconds=1), True)[0], 'AFTER')
        self.assertEqual(classify(end, None)[1], 'ACK_MISSING')
        self.assertEqual(classify(end, end-timedelta(seconds=1))[1], 'CLOCK_BACKWARDS')
        with self.assertRaises(ValueError):
            classify_bounds(lower=end.replace(tzinfo=None), upper=end, precision_us=1, deadline=end)

    def test_pass_expiry_inside_commit_interval_is_not_guessed(self):
        start = datetime(2026, 11, 7, 12, tzinfo=dt_timezone.utc)
        end = start + timedelta(seconds=1)
        def grade(tier, expiry):
            return classify_submitted_tier(raw_tier=tier, expires_at=expiry, lower=start, upper=end, precision_us=1000)
        self.assertEqual(grade('', None), ('KNOWN', '', ''))
        self.assertEqual(grade('SILVER', start-timedelta(seconds=1)), ('KNOWN', '', ''))
        self.assertEqual(grade('SILVER', end+timedelta(seconds=1)), ('KNOWN', 'SILVER', ''))
        self.assertEqual(grade('SILVER', start+timedelta(milliseconds=500)), ('REVIEW', None, 'PASS_EXPIRY_UNCERTAIN'))


@override_settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True)
class ReceiptProbeTest(TransactionTestCase):
    def setUp(self):
        save_grab_window(30, 0, lambda *args: None)
        boss = make_user(); self.boss_account = get_or_create_account_for_legacy_user(boss)
        provider = make_provider(); self.account = get_or_create_account_for_legacy_user(provider)
        profile = provider.escort_profile; profile.account = self.account; profile.save(update_fields=['account'])
        self.profile = profile
        self.order = make_order(boss, customer_account=self.boss_account, service=make_service(), payment_status='PAID')
        with transaction.atomic():
            pool = create_initial_snapshot(self.order.pk)
            self.round = first_round(pool.pk)

    def record(self, key='intent-1'):
        return record_intent(self.round.pk, self.account.pk, 'SINGLE', key, {})

    def test_real_commit_samples_and_retry_do_not_claim_assignment(self):
        self.assertEqual(unresolved_round(self.round.pk), 'WAIT_WATERMARK')
        result = self.record()
        self.assertEqual(result.state, 'BEFORE')
        row = OrderGrabReceiptProbe.objects.get(pk=result.receipt_id)
        self.assertEqual((row.pass_evidence.state, row.pass_evidence.submitted_tier), ('KNOWN', ''))
        self.assertLessEqual(row.lower_at, row.upper_observed_at)
        self.assertEqual(row.precision_us, 1000 if connection.vendor == 'sqlite' else 1)
        self.assertEqual(unresolved_round(self.round.pk), 'WAIT_APPLY')
        replay = self.record()
        self.assertTrue(replay.replayed); self.assertEqual(replay.receipt_id, result.receipt_id)
        self.assertEqual(mark_unacknowledged_for_review(self.round.pk, result.receipt_id), 'BEFORE')
        with self.assertRaisesMessage(ValueError, '其他登记'):
            record_intent(self.round.pk, self.account.pk, 'WITHDRAW', 'intent-1', {'team_id': 1})
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.provider_account_id), ('PENDING', None))
        self.assertEqual(OrderGrabReceiptProbe.objects.count(), 1)

    def test_lost_ack_is_never_replaced_by_retry_time(self):
        from orders import grab_receipt_probe as module
        real_sample, calls = module._db_clock, []
        def interrupted():
            calls.append(1)
            if len(calls) == 2:
                raise DatabaseError('simulated lost acknowledgement')
            return real_sample()
        with patch.object(module, '_db_clock', side_effect=interrupted):
            result = self.record()
        self.assertEqual(result.state, 'PENDING')
        self.assertEqual(unresolved_round(self.round.pk), 'PENDING')
        with patch.object(module, '_db_clock', side_effect=AssertionError('must not resample replay')):
            self.assertEqual(self.record().state, 'PENDING')
        self.assertEqual(mark_unacknowledged_for_review(self.round.pk, result.receipt_id), 'REVIEW')
        row = OrderGrabReceiptProbe.objects.get(pk=result.receipt_id)
        self.assertEqual((row.state, row.review_reason, row.upper_observed_at), ('REVIEW', 'ACK_MISSING', None))
        self.assertEqual(row.pass_evidence.state, 'REVIEW')
        self.assertEqual(unresolved_round(self.round.pk), 'REVIEW')
        self.assertEqual(self.record().state, 'REVIEW')

    def test_pass_at_registration_is_preserved_after_profile_changes(self):
        self.profile.pass_tier = 'SILVER'; self.profile.pass_expires_at = timezone.now()+timedelta(hours=1)
        self.profile.save(update_fields=['pass_tier', 'pass_expires_at'])
        result = self.record('silver-submission')
        evidence = OrderGrabReceiptPassEvidence.objects.get(receipt_id=result.receipt_id)
        self.assertEqual((evidence.state, evidence.submitted_tier), ('KNOWN', 'SILVER'))
        self.profile.pass_tier = 'GOLD'; self.profile.save(update_fields=['pass_tier'])
        self.assertTrue(self.record('silver-submission').replayed)
        evidence.refresh_from_db(); self.assertEqual(evidence.submitted_tier, 'SILVER')

    def test_pass_expiring_during_commit_requires_review(self):
        start = timezone.now(); driver = object()
        self.profile.pass_tier = 'SILVER'; self.profile.pass_expires_at = start + timedelta(milliseconds=10)
        self.profile.save(update_fields=['pass_tier', 'pass_expires_at'])
        with patch('orders.grab_receipt_probe._db_clock', side_effect=[(start, 1000, driver), (start+timedelta(milliseconds=20), 1000, driver)]):
            result = self.record('expiry-during-commit')
        evidence = OrderGrabReceiptPassEvidence.objects.get(receipt_id=result.receipt_id)
        self.assertEqual(result.state, 'REVIEW')
        self.assertEqual(evidence.receipt.state, 'BEFORE')  # Timing alone can be known.
        self.assertEqual((evidence.state, evidence.submitted_tier, evidence.review_reason), ('REVIEW', None, 'PASS_EXPIRY_UNCERTAIN'))
        self.assertEqual(unresolved_round(self.round.pk), 'REVIEW')

    def test_missing_pass_evidence_cannot_be_accepted_on_replay(self):
        result = self.record('missing-pass-evidence')
        OrderGrabReceiptPassEvidence.objects.filter(receipt_id=result.receipt_id).delete()
        self.assertEqual(self.record('missing-pass-evidence').state, 'REVIEW')
        self.assertEqual(unresolved_round(self.round.pk), 'REVIEW')

    def test_pass_evidence_write_failure_rolls_back_intent(self):
        with patch('orders.grab_receipt_probe.OrderGrabReceiptPassEvidence.objects.create', side_effect=DatabaseError('pass evidence failed')):
            with self.assertRaises(DatabaseError): self.record('pass-evidence-fails')
        self.assertFalse(OrderGrabReceiptProbe.objects.filter(request_key='pass-evidence-fails').exists())

    def test_only_disjoint_proved_intervals_can_determine_candidate_order(self):
        first = OrderGrabReceiptProbe.objects.get(pk=self.record('first-order').receipt_id)
        sleep(0.02)  # Outlast SQLite's displayed millisecond resolution.
        second = OrderGrabReceiptProbe.objects.get(pk=self.record('second-order').receipt_id)
        self.assertEqual(compare_candidate_bounds(first.pk, second.pk), 'FIRST_EARLIER')
        self.assertEqual(compare_candidate_bounds(second.pk, first.pk), 'SECOND_EARLIER')
        quantum = timedelta(microseconds=first.precision_us)
        second.lower_at = first.upper_observed_at + quantum + timedelta(microseconds=second.precision_us)
        second.upper_observed_at = second.lower_at
        second.save(update_fields=['lower_at', 'upper_observed_at'])
        self.assertEqual(compare_candidate_bounds(first.pk, second.pk), 'REVIEW')
        self.assertEqual(compare_candidate_bounds(second.pk, first.pk), 'REVIEW')
        second.lower_at += timedelta(microseconds=1)
        second.upper_observed_at = second.lower_at
        second.save(update_fields=['lower_at', 'upper_observed_at'])
        self.assertEqual(compare_candidate_bounds(first.pk, second.pk), 'FIRST_EARLIER')
        second.lower_at = first.lower_at; second.upper_observed_at = first.upper_observed_at
        second.save(update_fields=['lower_at', 'upper_observed_at'])
        self.assertEqual(compare_candidate_bounds(first.pk, second.pk), 'REVIEW')
        with self.settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False):
            with self.assertRaisesMessage(ValueError, '时钟尚未验证'):
                compare_candidate_bounds(first.pk, second.pk)
        second.state = 'PENDING'; second.upper_observed_at = None
        second.save(update_fields=['state', 'upper_observed_at'])
        with self.assertRaisesMessage(ValueError, '时间缺少'):
            compare_candidate_bounds(first.pk, second.pk)

    def test_commit_ack_lost_after_real_commit_leaves_recoverable_pending_record(self):
        db = transaction.get_connection()
        actual_commit = db.commit
        def commit_then_disconnect():
            actual_commit()
            raise DatabaseError('commit succeeded but acknowledgement was lost')
        with patch.object(db, 'commit', side_effect=commit_then_disconnect):
            with self.assertRaises(DatabaseError):
                self.record('commit-ack-lost')
        row = OrderGrabReceiptProbe.objects.get(request_key='commit-ack-lost')
        self.assertEqual((row.state, row.upper_observed_at), ('PENDING', None))
        self.assertEqual(row.pass_evidence.state, 'PENDING')
        self.assertEqual(self.record('commit-ack-lost').state, 'PENDING')
        self.assertEqual(mark_unacknowledged_for_review(self.round.pk, row.pk), 'REVIEW')
        row.pass_evidence.refresh_from_db(); self.assertEqual(row.pass_evidence.state, 'REVIEW')
        self.order.refresh_from_db(); self.assertEqual(self.order.status, 'PENDING')

    def test_post_commit_evidence_write_failure_keeps_unresolved_intent(self):
        original_save = OrderGrabReceiptProbe.save
        def fail_second_phase(instance, *args, **kwargs):
            if instance.pk and instance.state != 'PENDING':
                raise DatabaseError('post-commit evidence failure')
            return original_save(instance, *args, **kwargs)
        with patch.object(OrderGrabReceiptProbe, 'save', new=fail_second_phase):
            result = self.record('post-save-failure')
        row = OrderGrabReceiptProbe.objects.get(pk=result.receipt_id)
        self.assertEqual((result.state, row.state, row.upper_observed_at), ('PENDING', 'PENDING', None))
        self.assertEqual(row.pass_evidence.state, 'PENDING')
        self.assertEqual(unresolved_round(self.round.pk), 'PENDING')

    def test_candidate_comparison_refuses_inconsistent_pass_evidence(self):
        first = self.record('compare-pass-a'); second = self.record('compare-pass-b')
        evidence = OrderGrabReceiptPassEvidence.objects.get(receipt_id=second.receipt_id)
        evidence.submitted_tier = 'GOLD'; evidence.save(update_fields=['submitted_tier'])
        self.assertEqual(self.record('compare-pass-b').state, 'REVIEW')
        self.assertEqual(unresolved_round(self.round.pk), 'REVIEW')
        with self.assertRaisesMessage(ValueError, '通行证证据不一致'):
            compare_candidate_bounds(first.receipt_id, second.receipt_id)

    def test_replay_and_comparison_recheck_cutoff_and_command_fingerprint(self):
        first = self.record('integrity-first')
        second = self.record('integrity-second')
        row = OrderGrabReceiptProbe.objects.get(pk=first.receipt_id)
        original = (row.lower_at, row.upper_observed_at, row.fingerprint)
        late = self.round.deadline + timedelta(seconds=1)
        OrderGrabReceiptProbe.objects.filter(pk=row.pk).update(lower_at=late, upper_observed_at=late)
        self.assertEqual(self.record('integrity-first').state, 'REVIEW')
        self.assertEqual(unresolved_round(self.round.pk), 'REVIEW')
        with self.assertRaisesMessage(ValueError, '时间或内容证据不一致'):
            compare_candidate_bounds(first.receipt_id, second.receipt_id)
        OrderGrabReceiptProbe.objects.filter(pk=row.pk).update(lower_at=original[0], upper_observed_at=original[1])
        self.assertEqual(unresolved_round(self.round.pk), 'WAIT_APPLY')
        before_open = self.round.opened_at - timedelta(seconds=1)
        OrderGrabReceiptProbe.objects.filter(pk=row.pk).update(lower_at=before_open, upper_observed_at=before_open)
        self.assertEqual(self.record('integrity-first').state, 'REVIEW')
        self.assertEqual(unresolved_round(self.round.pk), 'REVIEW')
        with self.assertRaisesMessage(ValueError, '时间或内容证据不一致'):
            compare_candidate_bounds(first.receipt_id, second.receipt_id)
        OrderGrabReceiptProbe.objects.filter(pk=row.pk).update(lower_at=original[0], upper_observed_at=original[1])
        OrderGrabReceiptProbe.objects.filter(pk=row.pk).update(fingerprint='0' * 64)
        self.assertEqual(unresolved_round(self.round.pk), 'REVIEW')
        with self.assertRaisesMessage(ValueError, '时间或内容证据不一致'):
            compare_candidate_bounds(first.receipt_id, second.receipt_id)
        with self.assertRaisesMessage(ValueError, '其他登记'):
            self.record('integrity-first')

    def test_cancel_evidence_and_clock_contract_are_rechecked(self):
        candidate = self.record('clock-contract-candidate')
        with override_settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False):
            self.assertEqual(self.record('clock-contract-candidate').state, 'REVIEW')
            self.assertEqual(unresolved_round(self.round.pk), 'REVIEW')
        self.assertEqual(self.record('clock-contract-candidate').state, 'BEFORE')
        cancel = record_intent(self.round.pk, self.boss_account.pk, 'BOSS_CANCEL', 'cancel-integrity', {})
        late = self.round.deadline + timedelta(seconds=1)
        OrderGrabReceiptProbe.objects.filter(pk=cancel.receipt_id).update(lower_at=late, upper_observed_at=late)
        self.assertEqual(record_intent(self.round.pk, self.boss_account.pk, 'BOSS_CANCEL', 'cancel-integrity', {}).state, 'REVIEW')
        self.assertEqual(unresolved_round(self.round.pk), 'REVIEW')

    def test_registration_interval_touching_opening_is_persisted_for_review(self):
        opened = self.round.opened_at
        driver = object()
        lower = opened + timedelta(microseconds=500)
        upper = opened + timedelta(microseconds=1500)
        with patch('orders.grab_receipt_probe._db_clock',
                   side_effect=[(lower, 1000, driver), (upper, 1000, driver)]):
            outcome = self.record('opening-uncertain')
        row = OrderGrabReceiptProbe.objects.get(pk=outcome.receipt_id)
        self.assertEqual((outcome.state, row.state, row.review_reason), ('REVIEW', 'REVIEW', 'CROSSED_OPENING'))
        self.assertEqual(row.pass_evidence.state, 'REVIEW')
        self.assertEqual(unresolved_round(self.round.pk), 'REVIEW')

    def test_cancel_evidence_cannot_follow_an_order_to_a_different_owner(self):
        record_intent(self.round.pk, self.boss_account.pk, 'BOSS_CANCEL', 'owner-integrity', {})
        other = get_or_create_account_for_legacy_user(make_user())
        Order.objects.filter(pk=self.order.pk).update(customer_account=other)
        self.assertEqual(unresolved_round(self.round.pk), 'REVIEW')
        with self.assertRaisesMessage(ValueError, '本人订单取消'):
            record_intent(self.round.pk, self.boss_account.pk, 'BOSS_CANCEL', 'owner-integrity', {})

    def test_changed_provider_identity_does_not_reuse_old_pass_proof(self):
        first = self.record('changed-owner-first')
        second = self.record('changed-owner-second')
        self.profile.account = None
        self.profile.save(update_fields=['account'])
        self.assertEqual(self.record('changed-owner-first').state, 'REVIEW')
        self.assertEqual(unresolved_round(self.round.pk), 'REVIEW')
        with self.assertRaisesMessage(ValueError, '时间或内容证据不一致'):
            compare_candidate_bounds(first.receipt_id, second.receipt_id)

    def test_changed_account_type_cannot_be_used_for_ranking(self):
        first = self.record('changed-role-first')
        second = self.record('changed-role-second')
        type(self.account).objects.filter(pk=self.account.pk).update(account_type='BOSS')
        self.assertEqual(unresolved_round(self.round.pk), 'REVIEW')
        with self.assertRaisesMessage(ValueError, '仅陪玩'):
            self.record('changed-role-first')
        with self.assertRaisesMessage(ValueError, '时间或内容证据不一致'):
            compare_candidate_bounds(first.receipt_id, second.receipt_id)

    def test_late_batch_corruption_is_not_silently_ignored(self):
        seed = OrderGrabReceiptProbe.objects.get(pk=self.record('batch-seed').receipt_id)
        proof = seed.pass_evidence
        last = None
        for i in range(256):
            last = OrderGrabReceiptProbe.objects.create(round=self.round, actor=self.account,
                request_key=f'batch-{i}', kind=seed.kind, payload=seed.payload, fingerprint=seed.fingerprint,
                lower_at=seed.lower_at, upper_observed_at=seed.upper_observed_at,
                precision_us=seed.precision_us, state='BEFORE')
            OrderGrabReceiptPassEvidence.objects.create(receipt=last, profile_id_snapshot=proof.profile_id_snapshot,
                user_id_snapshot=proof.user_id_snapshot, raw_tier=proof.raw_tier, raw_expires_at=proof.raw_expires_at,
                state='KNOWN', submitted_tier=proof.submitted_tier)
        self.assertEqual(unresolved_round(self.round.pk), 'WAIT_APPLY')
        OrderGrabReceiptProbe.objects.filter(pk=last.pk).update(fingerprint='0' * 64)
        self.assertEqual(unresolved_round(self.round.pk), 'REVIEW')

    def test_delayed_ack_crossing_cutoff_goes_to_review(self):
        from orders import grab_receipt_probe as module
        self.round.opened_at = timezone.now() - timedelta(seconds=1)
        self.round.deadline = self.round.opened_at + timedelta(seconds=2)
        self.round.save(update_fields=['opened_at', 'deadline'])
        self.round.pool.opened_at = self.round.opened_at
        self.round.pool.window_seconds = 2
        self.round.pool.first_deadline = self.round.deadline
        self.round.pool.save(update_fields=['opened_at', 'window_seconds', 'first_deadline'])
        original, calls = module._db_clock, []
        def delayed_sample():
            calls.append(1)
            if len(calls) == 2:
                sleep(1.3)  # Simulate a delayed acknowledgement, not a DB commit timestamp.
            return original()
        with patch.object(module, '_db_clock', side_effect=delayed_sample):
            result = self.record('late-observation')
        row = OrderGrabReceiptProbe.objects.get(pk=result.receipt_id)
        self.assertEqual((row.state, row.review_reason), ('REVIEW', 'CROSSED_CUTOFF'))
        self.assertEqual(unresolved_round(self.round.pk), 'REVIEW')

    def test_commit_itself_crossing_cutoff_goes_to_review(self):
        self.round.opened_at = timezone.now() - timedelta(seconds=1)
        self.round.deadline = self.round.opened_at + timedelta(seconds=2)
        self.round.save(update_fields=['opened_at', 'deadline'])
        self.round.pool.opened_at = self.round.opened_at
        self.round.pool.window_seconds = 2
        self.round.pool.first_deadline = self.round.deadline
        self.round.pool.save(update_fields=['opened_at', 'window_seconds', 'first_deadline'])
        db = transaction.get_connection()
        actual_commit, calls = db.commit, []
        def delayed_commit():
            calls.append(1)
            if len(calls) == 1:
                sleep(1.3)
            return actual_commit()
        with patch.object(db, 'commit', side_effect=delayed_commit):
            result = self.record('late-commit')
        row = OrderGrabReceiptProbe.objects.get(pk=result.receipt_id)
        self.assertEqual((row.state, row.review_reason), ('REVIEW', 'CROSSED_CUTOFF'))
        self.assertEqual(unresolved_round(self.round.pk), 'REVIEW')

    def test_changed_connection_and_backwards_clock_require_review(self):
        now = datetime(2026, 11, 7, 12, tzinfo=dt_timezone.utc)
        a, b = object(), object()
        with patch('orders.grab_receipt_probe._db_clock', side_effect=[(now, 1, a), (now, 1, b)]):
            outcome = self.record('changed-connection')
        self.assertEqual(outcome.state, 'REVIEW')
        self.assertEqual(OrderGrabReceiptProbe.objects.get(pk=outcome.receipt_id).review_reason, 'CONNECTION_CHANGED')
        with patch('orders.grab_receipt_probe._db_clock', side_effect=[(now, 1, a), (now-timedelta(seconds=1), 1, a)]):
            outcome = self.record('clock-backwards')
        self.assertEqual(OrderGrabReceiptProbe.objects.get(pk=outcome.receipt_id).review_reason, 'CLOCK_BACKWARDS')

    @override_settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False)
    def test_unverified_database_clock_cannot_prove_timing(self):
        outcome = self.record('unverified-clock')
        self.assertEqual(outcome.state, 'REVIEW')
        self.assertEqual(OrderGrabReceiptProbe.objects.get(pk=outcome.receipt_id).review_reason, 'CLOCK_CONTRACT_UNVERIFIED')
        self.assertEqual(unresolved_round(self.round.pk), 'REVIEW')

    def test_owner_type_key_and_outer_transaction_boundaries(self):
        other_boss = get_or_create_account_for_legacy_user(make_user())
        with self.assertRaisesMessage(ValueError, '本人订单取消'):
            record_intent(self.round.pk, other_boss.pk, 'BOSS_CANCEL', 'not-owner', {})
        with self.assertRaisesMessage(ValueError, '仅陪玩'):
            record_intent(self.round.pk, self.boss_account.pk, 'SINGLE', 'wrong-role', {})
        with self.assertRaisesMessage(ValueError, '组队与登记人不匹配'):
            record_intent(self.round.pk, self.account.pk, 'CONFIRM', 'unknown-team', {'team_id': 999})
        with transaction.atomic(), self.assertRaisesMessage(RuntimeError, '最外层事务'):
            self.record()
        transaction.set_autocommit(False)
        try:
            with self.assertRaisesMessage(RuntimeError, '最外层事务'):
                self.record()
        finally:
            transaction.rollback(); transaction.set_autocommit(True)
        result = record_intent(self.round.pk, self.boss_account.pk, 'BOSS_CANCEL', 'own-cancel', {})
        self.assertEqual(result.state, 'BEFORE')
        self.order.refresh_from_db(); self.assertEqual(self.order.status, Order.Status.PENDING)

    def test_real_mysql_duplicate_registration_keeps_one_evidence_record(self):
        if connection.vendor != 'mysql':
            self.skipTest('requires isolated MySQL row locks')
        barrier = Barrier(2)
        def attempt(_):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return self.record('concurrent-key')
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, range(2)))
        self.assertEqual(results[0].receipt_id, results[1].receipt_id)
        self.assertEqual(OrderGrabReceiptProbe.objects.count(), 1)


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated databases')
@override_settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True)
class ReceiptProbeMultiStoreTest(TransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    reset_sequences = True

    def test_same_round_actor_and_key_remain_store_local(self):
        stores, ids = (resolve_store(), xinan_store()), []
        for store, tier in zip(stores, ('GOLD', 'BRONZE')):
            with store_context(store):
                save_grab_window(30, 0, lambda *args: None)
                provider = make_provider(); actor = get_or_create_account_for_legacy_user(provider)
                profile = provider.escort_profile; profile.account = actor; profile.pass_tier = tier
                profile.pass_expires_at = timezone.now()+timedelta(hours=1)
                profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at'])
                order = make_order(make_user(), service=make_service(), payment_status='PAID')
                with transaction.atomic():
                    pool = create_initial_snapshot(order.pk); round_ = first_round(pool.pk)
                ids.append((round_.pk, actor.pk))
        self.assertEqual(ids[0], ids[1])
        with store_context(stores[0]):
            receipt = record_intent(*ids[0], 'SINGLE', 'same-key', {})
            self.assertEqual(receipt.state, 'BEFORE')
            self.assertEqual(OrderGrabReceiptPassEvidence.objects.get(receipt_id=receipt.receipt_id).submitted_tier, 'GOLD')
        with store_context(stores[1]):
            self.assertFalse(OrderGrabReceiptProbe.objects.exists())
            self.assertEqual(unresolved_round(ids[1][0]), 'WAIT_WATERMARK')
            receipt = record_intent(*ids[1], 'SINGLE', 'same-key', {})
            self.assertEqual(OrderGrabReceiptPassEvidence.objects.get(receipt_id=receipt.receipt_id).submitted_tier, 'BRONZE')
            self.assertEqual(unresolved_round(ids[1][0]), 'WAIT_APPLY')
            OrderGrabReceiptProbe.objects.filter(pk=receipt.receipt_id).update(fingerprint='0' * 64)
            self.assertEqual(unresolved_round(ids[1][0]), 'REVIEW')
        with store_context(stores[0]):
            self.assertEqual(unresolved_round(ids[0][0]), 'WAIT_APPLY')
