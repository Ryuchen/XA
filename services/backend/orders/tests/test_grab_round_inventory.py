"""Inventory uses actual stored receipt/team evidence, never request subsets."""
from datetime import timedelta
from unittest import skipUnless

from django.conf import settings
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from unittest.mock import patch
from time import sleep

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.grab_receipt_probe import record_intent
from orders.grab_round_inventory import inspect_round
from orders.grab_team_draft import withdraw, withdraw_with_receipt
from orders.models import Order, OrderGrabReceiptPassEvidence, OrderGrabReceiptProbe, OrderProvider, OrderGrabTeamDraft
from orders.tests import test_grab_bound_ranking as fixture
from stores.db import transaction
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store


@override_settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True)
class SingleInventoryTest(TransactionTestCase):
    setUp = fixture.BoundSingleRankingTest.setUp
    submit = fixture.BoundSingleRankingTest.submit

    def inspect(self):
        with patch('django.utils.timezone.now', return_value=self.round.deadline + timedelta(seconds=1)):
            with transaction.atomic():
                return inspect_round(self.round.pk)

    def test_all_receipts_are_collected_and_ranked_without_qualifying_or_assigning(self):
        first = self.submit(self.first, 'a')
        second = self.submit(self.second, 'b')
        result = self.inspect()
        self.assertEqual(result.before_receipt_ids, (first, second))
        self.assertEqual(result.potential_candidate_ids, (first, second))
        self.assertEqual((result.status, result.ranking.winner_id), ('WAIT_ADMISSION', first))
        self.assertFalse(result.can_assign)
        self.assertFalse(result.can_cancel)
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.provider_account_id), ('PENDING', None))

    def test_boss_cancel_intent_takes_priority_without_moving_money(self):
        candidate = self.submit(self.first, 'candidate')
        boss = get_or_create_account_for_legacy_user(self.order.customer)
        Order.objects.filter(pk=self.order.pk).update(customer_account=boss)
        cancelled = record_intent(self.round.pk, boss.pk, 'BOSS_CANCEL', 'boss-cancel', {})
        result = self.inspect()
        self.assertEqual(result.status, 'CANCEL_INTENT')
        self.assertEqual(result.cancellation_receipt_ids, (cancelled.receipt_id,))
        self.assertEqual(result.potential_candidate_ids, (candidate,))
        self.assertIsNone(result.ranking)
        self.assertFalse(result.can_cancel)
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status), ('PENDING', 'PAID'))
        OrderGrabReceiptProbe.objects.filter(pk=candidate).update(fingerprint='0' * 64)
        result = self.inspect()
        self.assertEqual((result.status, result.review_receipt_ids), ('CANCEL_INTENT', (candidate,)))
        self.assertFalse(result.can_cancel)

    def test_pending_and_tampered_receipts_block_a_ranking(self):
        first = self.submit(self.first, 'first')
        second = self.submit(self.second, 'second')
        OrderGrabReceiptProbe.objects.filter(pk=second).update(state='PENDING', upper_observed_at=None)
        OrderGrabReceiptPassEvidence.objects.filter(receipt_id=second).update(state='PENDING', submitted_tier=None)
        pending = self.inspect()
        self.assertEqual((pending.status, pending.pending_receipt_ids, pending.ranking), ('PENDING', (second,), None))
        OrderGrabReceiptProbe.objects.filter(pk=first).update(fingerprint='0' * 64)
        damaged = self.inspect()
        self.assertEqual((damaged.status, damaged.review_receipt_ids, damaged.pending_receipt_ids),
                         ('REVIEW', (first,), (second,)))

    def test_late_receipt_is_not_treated_as_a_candidate(self):
        first = self.submit(self.first, 'on-time')
        second = self.submit(self.second, 'late')
        later = self.round.deadline + timedelta(seconds=2)
        OrderGrabReceiptProbe.objects.filter(pk=second).update(state='AFTER', lower_at=later,
                                                               upper_observed_at=later)
        OrderGrabReceiptPassEvidence.objects.filter(receipt_id=second).update(state='LATE', submitted_tier=None)
        result = self.inspect()
        self.assertEqual((result.status, result.potential_candidate_ids, result.after_receipt_ids),
                         ('WAIT_ADMISSION', (first,), (second,)))

    def test_duplicate_requests_from_one_person_require_review(self):
        first = self.submit(self.first, 'first')
        second = self.submit(self.first, 'second')
        result = self.inspect()
        self.assertEqual((result.status, result.potential_candidate_ids, result.review_receipt_ids),
                         ('REVIEW', (first,), (second,)))

    def test_candidate_submitted_before_visible_reports_reason(self):
        self.order, self.round = fixture.pool_for(delays={
            'BLACK': 0, 'GOLD': 60, 'SILVER': 120, 'BRONZE': 180, 'NONE': 300})
        self.submit(self.first, 'premature')
        result = self.inspect()
        self.assertEqual(result.status, 'REVIEW')
        self.assertIn('尚未向该陪玩开放', result.review_detail)
        self.assertFalse(result.can_assign)

    def test_empty_is_not_proof_of_no_candidates_and_switch_is_required(self):
        result = self.inspect()
        self.assertEqual((result.status, result.potential_candidate_ids, result.can_cancel),
                         ('WAIT_WATERMARK', (), False))
        with override_settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False):
            with transaction.atomic(), self.assertRaisesMessage(ValueError, '时钟尚未验证'):
                inspect_round(self.round.pk)

    def test_gift_or_prior_assignment_cannot_be_treated_as_an_unassigned_pool_order(self):
        Order.objects.filter(pk=self.order.pk).update(is_gift_snapshot=True)
        with self.assertRaisesMessage(ValueError, '订单或抢单轮次事实待核对'):
            self.inspect()
        Order.objects.filter(pk=self.order.pk).update(is_gift_snapshot=False)
        OrderProvider.objects.create(order=self.order, provider=self.first_user, provider_account=self.first,
                                     released_at=timezone.now())
        with self.assertRaisesMessage(ValueError, '订单或抢单轮次事实待核对'):
            self.inspect()

    def test_inventory_does_not_stop_before_a_later_corrupt_receipt(self):
        for index in range(257):
            last = self.submit(self.first, f'batch-{index}')
        OrderGrabReceiptProbe.objects.filter(pk=last).update(fingerprint='0' * 64)
        result = self.inspect()
        self.assertEqual(result.status, 'REVIEW')
        self.assertIn(last, result.review_receipt_ids)
        self.assertEqual(len(result.before_receipt_ids), 256)


@override_settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
                   REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED=True)
class TeamInventoryTest(TransactionTestCase):
    setUp = fixture.BoundTeamRankingTest.setUp
    team = fixture.BoundTeamRankingTest.team

    def inspect(self):
        with patch('django.utils.timezone.now', return_value=self.round.deadline + timedelta(seconds=1)):
            with transaction.atomic():
                return inspect_round(self.round.pk)

    def test_complete_teams_are_collected_without_external_selection(self):
        first = self.team(0, 1, 'first-team')
        second = self.team(2, 3, 'second-team')
        result = self.inspect()
        self.assertEqual((result.status, result.potential_candidate_ids), ('WAIT_ADMISSION', (first.pk, second.pk)))
        self.assertEqual(len(result.before_receipt_ids), 4)
        self.assertEqual(result.ranking.winner_id, first.pk)
        self.assertFalse(result.can_assign)

    def test_unapplied_invitation_and_unproved_withdrawal_block_ranking(self):
        first = self.team(0, 1, 'complete-team')
        unbound = record_intent(self.round.pk, self.people[2][1].pk, 'INVITE', 'not-applied',
                                {'partner_id': self.people[3][1].pk})
        result = self.inspect()
        self.assertEqual((result.status, result.unapplied_receipt_ids, result.potential_candidate_ids),
                         ('WAIT_APPLY', (unbound.receipt_id,), (first.pk,)))
        with transaction.atomic():
            withdraw(self.round.pk, first.pk, self.people[0][1].pk)
        result = self.inspect()
        self.assertEqual((result.status, result.unresolved_team_ids, result.ranking),
                         ('WAIT_APPLY', (first.pk,), None))

    def test_proved_withdrawal_allows_member_to_join_another_team(self):
        old = self.team(0, 1, 'old-team')
        sleep(0.02)
        receipt = record_intent(self.round.pk, self.people[1][1].pk, 'WITHDRAW',
                                'leave-old', {'team_id': old.pk})
        self.assertEqual(receipt.state, 'BEFORE')
        with transaction.atomic():
            result = withdraw_with_receipt(self.round.pk, old.pk, receipt.receipt_id)
            self.assertEqual(result.withdrawal_receipt_id, receipt.receipt_id)
        with transaction.atomic():
            self.assertEqual(withdraw_with_receipt(self.round.pk, old.pk, receipt.receipt_id).pk, old.pk)
        self.assertFalse(old.occupied_seats.exists())
        replacement = self.team(0, 2, 'replacement-team')
        inventory = self.inspect()
        self.assertEqual(inventory.status, 'WAIT_ADMISSION')
        self.assertEqual(inventory.potential_candidate_ids, (replacement.pk,))
        self.assertEqual((inventory.unapplied_receipt_ids, inventory.unresolved_team_ids), ((), ()))
        self.assertFalse(inventory.can_assign)

    def test_withdrawal_cannot_apply_damaged_or_unordered_receipt(self):
        team = self.team(0, 1, 'protected-team')
        sleep(0.02)
        result = record_intent(self.round.pk, self.people[0][1].pk, 'WITHDRAW',
                               'leave-protected', {'team_id': team.pk})
        receipt = OrderGrabReceiptProbe.objects.get(pk=result.receipt_id)
        original_fingerprint = receipt.fingerprint
        receipt.fingerprint = '0' * 64; receipt.save(update_fields=['fingerprint'])
        with transaction.atomic(), self.assertRaisesMessage(ValueError, '无法核对'):
            withdraw_with_receipt(self.round.pk, team.pk, receipt.pk)
        receipt.fingerprint = original_fingerprint
        prior = team.members.get(account=self.people[1][1]).receipt_pass_evidence.receipt
        receipt.lower_at = prior.lower_at; receipt.upper_observed_at = prior.upper_observed_at
        receipt.save(update_fields=['fingerprint', 'lower_at', 'upper_observed_at'])
        with transaction.atomic(), self.assertRaisesMessage(ValueError, '无法核对'):
            withdraw_with_receipt(self.round.pk, team.pk, receipt.pk)
        team.refresh_from_db()
        self.assertEqual((team.status, team.withdrawal_receipt_id, team.occupied_seats.count()),
                         ('UNVERIFIED', None, 2))

    def test_withdrawal_save_failure_restores_occupied_seats(self):
        team = self.team(0, 1, 'rollback-team')
        sleep(0.02)
        receipt = record_intent(self.round.pk, self.people[0][1].pk, 'WITHDRAW',
                                'leave-rollback', {'team_id': team.pk})
        with patch.object(OrderGrabTeamDraft, 'save', side_effect=RuntimeError('write failed')):
            with self.assertRaisesMessage(RuntimeError, 'write failed'):
                with transaction.atomic():
                    withdraw_with_receipt(self.round.pk, team.pk, receipt.receipt_id)
        team.refresh_from_db()
        self.assertEqual((team.status, team.withdrawal_receipt_id, team.occupied_seats.count()),
                         ('UNVERIFIED', None, 2))

    def test_unordered_invitation_and_confirmation_cannot_be_hidden_by_withdrawal(self):
        team = self.team(0, 1, 'unordered-team')
        sleep(0.02)
        withdrawal = record_intent(self.round.pk, self.people[0][1].pk, 'WITHDRAW',
                                   'leave-unordered', {'team_id': team.pk})
        invitation = team.members.get(account=self.people[0][1]).receipt_pass_evidence.receipt
        confirmation = team.members.get(account=self.people[1][1]).receipt_pass_evidence.receipt
        confirmation.lower_at = invitation.lower_at
        confirmation.upper_observed_at = invitation.upper_observed_at
        confirmation.save(update_fields=['lower_at', 'upper_observed_at'])
        with self.assertRaisesMessage(ValueError, '无法核对'):
            with transaction.atomic():
                withdraw_with_receipt(self.round.pk, team.pk, withdrawal.receipt_id)
        team.refresh_from_db()
        self.assertEqual((team.status, team.occupied_seats.count()), ('UNVERIFIED', 2))

    def test_late_or_unacknowledged_withdrawal_does_not_free_seats(self):
        team = self.team(0, 1, 'deadline-team')
        sleep(0.02)
        receipt = record_intent(self.round.pk, self.people[0][1].pk, 'WITHDRAW',
                                'leave-deadline', {'team_id': team.pk})
        row = OrderGrabReceiptProbe.objects.get(pk=receipt.receipt_id)
        for state in ('PENDING', 'AFTER'):
            if state == 'PENDING':
                row.state, row.upper_observed_at = state, None
            else:
                row.state = state
                row.lower_at = row.upper_observed_at = self.round.deadline + timedelta(seconds=2)
            row.save(update_fields=['state', 'lower_at', 'upper_observed_at'])
            with self.assertRaisesMessage(ValueError, '无法核对'):
                with transaction.atomic():
                    withdraw_with_receipt(self.round.pk, team.pk, row.pk)
            team.refresh_from_db()
            self.assertEqual((team.status, team.occupied_seats.count()), ('UNVERIFIED', 2))

    def test_changed_withdrawal_receipt_blocks_inventory_after_application(self):
        team = self.team(0, 1, 'tampered-team')
        sleep(0.02)
        receipt = record_intent(self.round.pk, self.people[0][1].pk, 'WITHDRAW',
                                'leave-tampered', {'team_id': team.pk})
        with transaction.atomic():
            withdraw_with_receipt(self.round.pk, team.pk, receipt.receipt_id)
        OrderGrabReceiptProbe.objects.filter(pk=receipt.receipt_id).update(fingerprint='0' * 64)
        result = self.inspect()
        self.assertEqual(result.status, 'REVIEW')
        self.assertIn(receipt.receipt_id, result.review_receipt_ids)
        self.assertIn(team.pk, result.unresolved_team_ids)
        self.assertFalse(result.can_assign)


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated stores')
@override_settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True)
class InventoryStoreTest(TransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    reset_sequences = True

    def test_same_ids_never_mix_receipts_across_stores(self):
        records = []
        for store, tier in ((resolve_store(), 'GOLD'), (xinan_store(), 'BRONZE')):
            with store_context(store):
                _, account, _ = fixture.provider(tier, key=store.slug)
                _, round_ = fixture.pool_for()
                proof = record_intent(round_.pk, account.pk, 'SINGLE', 'same-request', {})
                records.append((store, round_.pk, proof.receipt_id))
        self.assertEqual(records[0][1:], records[1][1:])
        with store_context(records[1][0]):
            OrderGrabReceiptProbe.objects.filter(pk=records[1][2]).update(fingerprint='0' * 64)
        for store, round_id, proof_id in records:
            with store_context(store):
                from orders.models import OrderGrabRound
                round_ = OrderGrabRound.objects.get(pk=round_id)
                with patch('django.utils.timezone.now', return_value=round_.deadline + timedelta(seconds=1)):
                    with transaction.atomic():
                        result = inspect_round(round_id)
                if store.slug == 'xinan':
                    self.assertEqual((result.status, result.review_receipt_ids), ('REVIEW', (proof_id,)))
                else:
                    self.assertEqual((result.status, result.potential_candidate_ids), ('WAIT_ADMISSION', (proof_id,)))

    @override_settings(PRESTART_SCOPE_SNAPSHOT_ENABLED=True, REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED=True)
    def test_withdrawal_proofs_and_team_seats_are_store_local(self):
        records = []
        for store in (resolve_store(), xinan_store()):
            with store_context(store):
                fixture.BoundTeamRankingTest.setUp(self)
                team = fixture.BoundTeamRankingTest.team(self, 0, 1, 'same-team')
                sleep(0.02)
                receipt = record_intent(self.round.pk, self.people[0][1].pk, 'WITHDRAW',
                                        'same-leave', {'team_id': team.pk})
                with transaction.atomic():
                    withdraw_with_receipt(self.round.pk, team.pk, receipt.receipt_id)
                records.append((store, self.round.pk, team.pk, receipt.receipt_id))
        self.assertEqual(records[0][1:], records[1][1:])
        with store_context(records[1][0]):
            OrderGrabReceiptProbe.objects.filter(pk=records[1][3]).update(fingerprint='0' * 64)
        for store, round_id, team_id, receipt_id in records:
            with store_context(store):
                from orders.models import OrderGrabRound
                round_ = OrderGrabRound.objects.get(pk=round_id)
                team = OrderGrabTeamDraft.objects.get(pk=team_id)
                self.assertEqual((team.withdrawal_receipt_id, team.occupied_seats.count()), (receipt_id, 0))
                with patch('django.utils.timezone.now', return_value=round_.deadline + timedelta(seconds=1)):
                    with transaction.atomic():
                        result = inspect_round(round_id)
                if store.slug == 'xinan':
                    self.assertEqual(result.status, 'REVIEW')
                    self.assertIn(team_id, result.unresolved_team_ids)
                else:
                    self.assertEqual((result.status, result.unresolved_team_ids), ('WAIT_WATERMARK', ()))
