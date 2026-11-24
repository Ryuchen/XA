"""Persisted reopen facts feed real candidate receipts and team preparation."""
import os
from datetime import timedelta
from time import sleep
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from console.capacity_config import save_capacity
from console.grab_config import save_grab_window
from orders.grab_admission import GrabAdmissionError, check_candidate
from orders.grab_bound_ranking import preview
from orders.grab_config import current_rules
from orders.grab_receipt_probe import record_intent
from orders.grab_team_draft import attach_pass_evidence, confirm, invite
from orders.models import OrderGrabRound
from orders.paid_service_scope import capture, record
from orders.tests import test_grab_bound_ranking as fixture
from stores.db import transaction
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store


@override_settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
                   REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED=True)
class ReopenedRoundTests(TransactionTestCase):
    def setUp(self):
        save_capacity(3, 0, lambda *args: None)
        self.users = [fixture.provider(tier, key=f'reopen-{index}')
                      for index, tier in enumerate(('', 'GOLD', ''))]

    def reopened(self, *, size):
        # Create an actual frozen pool in the past; the new round is now open.
        past = timezone.now() - timedelta(seconds=40)
        with patch('django.utils.timezone.now', return_value=past):
            order, first = fixture.pool_for(size=size, delays={
                'BLACK': 0, 'GOLD': 60, 'SILVER': 120, 'BRONZE': 180, 'NONE': 300})
            if size == 1:
                record(order, capture(order.service))
        opened = timezone.now() - timedelta(seconds=1)
        self.assertGreater(opened, first.deadline)
        round_ = OrderGrabRound.objects.create(pool=first.pool, number=2, opened_at=opened,
            deadline=opened + timedelta(seconds=30),
            total_deadline=max(first.total_deadline, opened + timedelta(seconds=30)),
            simultaneous_visibility=True)
        return order, first, round_

    def rank(self, round_, ids):
        with patch('django.utils.timezone.now', return_value=round_.deadline + timedelta(seconds=1)):
            with transaction.atomic():
                return preview(round_.pk, ids)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_single_new_round_ignores_initial_card_delay_and_old_round_is_closed(self):
        order, first, round_ = self.reopened(size=1)
        account = self.users[0][1]
        save_grab_window(10, current_rules()['version'], lambda *args: None)
        self.assertEqual(round_.deadline - round_.opened_at, timedelta(seconds=30))
        with self.assertRaisesMessage(GrabAdmissionError, '轮次已结束'):
            check_candidate(first.pool_id, account.pk, round_id=first.pk)
        hint = check_candidate(round_.pool_id, account.pk, round_id=round_.pk)
        self.assertEqual((hint.entry_pass_tier, hint.visible_at), ('', round_.opened_at))
        proof = record_intent(round_.pk, account.pk, 'SINGLE', 'reopened-single', {})
        self.assertEqual(proof.state, 'BEFORE')
        result = self.rank(round_, [proof.receipt_id])
        self.assertEqual((result.status, result.winner_id, result.can_assign),
                         ('POTENTIAL_WINNER', proof.receipt_id, False))
        with self.assertRaisesMessage(ValueError, '轮次已结束'):
            record_intent(first.pk, account.pk, 'SINGLE', 'stale-first', {})
        order.refresh_from_db()
        self.assertEqual((order.status, order.provider_account_id), ('PENDING', None))

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_double_team_can_confirm_immediately_after_reopening(self):
        _, _, round_ = self.reopened(size=2)
        captain, partner = self.users[0][1], self.users[1][1]
        sent = record_intent(round_.pk, captain.pk, 'INVITE', 'reopen-invite', {'partner_id': partner.pk})
        with transaction.atomic():
            team = invite(round_.pk, captain.pk, partner.pk, 'reopen-invite')
            attach_pass_evidence(round_.pk, team.pk, sent.receipt_id)
        sleep(0.02)
        accepted = record_intent(round_.pk, partner.pk, 'CONFIRM', 'reopen-confirm', {'team_id': team.pk})
        with transaction.atomic():
            confirm(round_.pk, team.pk, partner.pk)
            attach_pass_evidence(round_.pk, team.pk, accepted.receipt_id)
        result = self.rank(round_, [team.pk])
        self.assertEqual((result.status, result.winner_id, result.can_assign),
                         ('POTENTIAL_WINNER', team.pk, False))
        self.assertEqual([item.submitted_pass_tier for item in result.candidates[0].members], ['', 'GOLD'])

    def test_reopen_with_tampered_window_or_noncontiguous_history_is_rejected(self):
        _, _, round_ = self.reopened(size=1)
        account = self.users[0][1]
        OrderGrabRound.objects.filter(pk=round_.pk).update(deadline=round_.deadline + timedelta(seconds=1))
        with self.assertRaisesMessage(ValueError, '重开抢单轮次不一致'):
            record_intent(round_.pk, account.pk, 'SINGLE', 'bad-window', {})
        OrderGrabRound.objects.filter(pk=round_.pk).update(deadline=round_.deadline, number=3)
        with self.assertRaisesMessage(GrabAdmissionError, '重开抢单轮次不一致'):
            check_candidate(round_.pool_id, account.pk, round_id=round_.pk)

    def test_old_round_request_can_be_read_back_but_not_resubmitted(self):
        with patch('django.utils.timezone.now', return_value=timezone.now() - timedelta(seconds=40)):
            _, first = fixture.pool_for()
        account = self.users[0][1]
        original = record_intent(first.pk, account.pk, 'SINGLE', 'old-request', {})
        self.assertEqual(original.state, 'AFTER')
        opened = timezone.now() - timedelta(seconds=1)
        OrderGrabRound.objects.create(pool=first.pool, number=2, opened_at=opened,
            deadline=opened + timedelta(seconds=30),
            total_deadline=max(first.total_deadline, opened + timedelta(seconds=30)),
            simultaneous_visibility=True)
        replay = record_intent(first.pk, account.pk, 'SINGLE', 'old-request', {})
        self.assertEqual((replay.receipt_id, replay.state, replay.replayed),
                         (original.receipt_id, 'AFTER', True))
        with self.assertRaisesMessage(ValueError, '轮次已结束'):
            record_intent(first.pk, account.pk, 'SINGLE', 'new-old-round-request', {})

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_reopened_team_can_change_partner_without_carrying_old_seat(self):
        captain, first_partner, next_partner = (item[1] for item in self.users)
        with patch('django.utils.timezone.now', return_value=timezone.now() - timedelta(seconds=40)):
            _, first = fixture.pool_for(size=2)
            with transaction.atomic():
                old = invite(first.pk, captain.pk, first_partner.pk, 'old-team')
        opened = timezone.now() - timedelta(seconds=1)
        second = OrderGrabRound.objects.create(pool=first.pool, number=2, opened_at=opened,
            deadline=opened + timedelta(seconds=30),
            total_deadline=max(first.total_deadline, opened + timedelta(seconds=30)),
            simultaneous_visibility=True)
        with transaction.atomic():
            new = invite(second.pk, captain.pk, next_partner.pk, 'new-team')
        self.assertNotEqual(old.pk, new.pk)
        self.assertEqual(new.partner_id, next_partner.pk)
        self.assertEqual(list(second.occupied_seats.values_list('account_id', flat=True)), [captain.pk])
        self.assertEqual(list(first.occupied_seats.values_list('account_id', flat=True)), [captain.pk])


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated stores')
@override_settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
                   REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED=True)
class ReopenedRoundStoreTests(TransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    reset_sequences = True

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_same_round_and_receipt_ids_remain_store_local(self):
        rows = []
        for store, tier in ((resolve_store(), 'GOLD'), (xinan_store(), 'BRONZE')):
            with store_context(store):
                save_capacity(3, 0, lambda *args: None)
                _, account, _ = fixture.provider(tier, key=store.slug)
                with patch('django.utils.timezone.now', return_value=timezone.now() - timedelta(seconds=40)):
                    order, first = fixture.pool_for()
                    record(order, capture(order.service))
                opened = timezone.now() - timedelta(seconds=1)
                second = OrderGrabRound.objects.create(pool=first.pool, number=2, opened_at=opened,
                    deadline=opened + timedelta(seconds=30),
                    total_deadline=max(first.total_deadline, opened + timedelta(seconds=30)),
                    simultaneous_visibility=True)
                check_candidate(second.pool_id, account.pk, round_id=second.pk)
                proof = record_intent(second.pk, account.pk, 'SINGLE', 'same-reopened-id', {})
                rows.append((store, account.pk, second.pk, proof.receipt_id, tier))
        self.assertEqual(rows[0][1:4], rows[1][1:4])
        for store, _, round_id, receipt_id, tier in rows:
            with store_context(store):
                round_ = OrderGrabRound.objects.get(pk=round_id)
                with patch('django.utils.timezone.now', return_value=round_.deadline + timedelta(seconds=1)):
                    with transaction.atomic():
                        result = preview(round_id, [receipt_id])
                self.assertEqual((result.winner_id, result.can_assign), (receipt_id, False))
                self.assertEqual(result.candidates[0].members[0].submitted_pass_tier, tier)
        with store_context(rows[1][0]):
            bad = OrderGrabRound.objects.get(pk=rows[1][2])
            OrderGrabRound.objects.filter(pk=bad.pk).update(deadline=bad.deadline + timedelta(seconds=1))
            with transaction.atomic(), self.assertRaisesMessage(ValueError, '重开抢单轮次不一致'):
                preview(bad.pk, [rows[1][3]])
        with store_context(rows[0][0]):
            good = OrderGrabRound.objects.get(pk=rows[0][2])
            with patch('django.utils.timezone.now', return_value=good.deadline + timedelta(seconds=1)):
                with transaction.atomic():
                    self.assertEqual(preview(good.pk, [rows[0][3]]).winner_id, rows[0][3])
