"""Prepared double teams: occupancy is not proof of on-time registration."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from time import sleep
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.capacity_config import save_capacity
from console.grab_config import save_grab_window
from orders.grab_pool_snapshot import create_initial_snapshot
from orders.grab_receipt_probe import record_intent
from orders.grab_team_draft import attach_pass_evidence, confirm, first_round, invite, withdraw
from orders.models import (OrderGrabReceiptPassEvidence, OrderGrabRoundSeat, OrderGrabTeamDraft,
                           OrderGrabTeamDraftMember)
from orders.paid_service_scope import capture, record
from orders.tests.factories import make_order, make_provider, make_service, make_user
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.tests_hosts import xinan_store
from wallet.models import SystemConfig
from wallet.services import get_wallet


@override_settings(PRESTART_SCOPE_SNAPSHOT_ENABLED=True, REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED=True)
class GrabTeamDraftTest(TransactionTestCase):
    def setUp(self):
        save_grab_window(30, 0, lambda *args: None)
        save_capacity(3, 0, lambda *args: None)
        self.accounts, self.profiles = [], []
        for i in range(3):
            user = make_provider(deposit_mode='EARNINGS', pass_tier='BLACK',
                                 pass_expires_at=timezone.now()+timedelta(hours=1))
            account = get_or_create_account_for_legacy_user(user)
            account.is_openid_bound = True; account.openid = f'team-test-openid-{i}'
            account.save(update_fields=['is_openid_bound', 'openid'])
            profile = user.escort_profile; profile.account = account; profile.wechat_avatar_legacy_exempt = True
            profile.save(update_fields=['account', 'wechat_avatar_legacy_exempt'])
            self.accounts.append(account); self.profiles.append(profile)
        service = make_service(billing_provider_count=2)
        self.order = make_order(make_user(), service=service, payment_status='PAID')
        record(self.order, capture(service))
        with transaction.atomic():
            self.pool = create_initial_snapshot(self.order.pk)
            self.round = first_round(self.pool.pk)

    def invitation(self, captain=0, partner=1, key='request-a'):
        with transaction.atomic():
            return invite(self.round.pk, self.accounts[captain].pk, self.accounts[partner].pk, key)

    def confirm(self, team, partner=1):
        with transaction.atomic():
            return confirm(self.round.pk, team.pk, self.accounts[partner].pk)

    def withdraw(self, team, person=0):
        with transaction.atomic():
            return withdraw(self.round.pk, team.pk, self.accounts[person].pk)

    def test_first_round_replay_never_rewrites_frozen_deadline(self):
        with transaction.atomic():
            self.assertEqual(first_round(self.pool.pk).pk, self.round.pk)
        self.round.deadline += timedelta(seconds=1); self.round.save(update_fields=['deadline'])
        with transaction.atomic(), self.assertRaisesMessage(ValueError, '首轮快照不一致'):
            first_round(self.pool.pk)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_partner_cannot_confirm_before_visibility_and_tier_is_only_provisional(self):
        # At the simulated entry, A is BLACK and B is SILVER; both expire
        # before the simulated confirmation, when a new card can be bought.
        entry_a = self.pool.entry_passes.get(account_id_snapshot=self.accounts[0].pk)
        entry_b = self.pool.entry_passes.get(account_id_snapshot=self.accounts[1].pk)
        expiry = self.pool.opened_at + timedelta(seconds=10)
        entry_a.expires_at = expiry; entry_a.save(update_fields=['expires_at'])
        entry_b.tier = 'SILVER'; entry_b.expires_at = expiry; entry_b.save(update_fields=['tier', 'expires_at'])
        for profile, tier in ((self.profiles[0], 'BLACK'), (self.profiles[1], 'SILVER')):
            profile.pass_tier = tier; profile.pass_expires_at = expiry
            profile.save(update_fields=['pass_tier', 'pass_expires_at'])
        team = self.invitation()
        with self.assertRaisesMessage(ValueError, '可见时间'): self.confirm(team)
        team.refresh_from_db(); self.assertEqual(team.status, 'INVITING')
        self.assertFalse(team.occupied_seats.filter(account=self.accounts[1]).exists())
        for profile, tier in ((self.profiles[0], 'GOLD'), (self.profiles[1], 'BLACK')):
            profile.pass_tier = tier; profile.pass_expires_at = self.pool.opened_at + timedelta(hours=2)
            profile.save(update_fields=['pass_tier', 'pass_expires_at'])
        with patch('orders.grab_admission.timezone') as clock, patch('orders.grab_team_draft.timezone') as team_clock:
            clock.now.return_value = team_clock.now.return_value = self.pool.opened_at + timedelta(seconds=60)
            team = self.confirm(team)
        self.assertEqual(team.status, 'UNVERIFIED')
        self.assertEqual(team.members.get(account=self.accounts[0]).provisional_pass_tier, 'BLACK')
        self.assertEqual(team.members.get(account=self.accounts[1]).provisional_pass_tier, 'BLACK')
        self.assertEqual(team.occupied_seats.count(), 2)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_one_team_per_round_withdrawal_and_replay(self):
        team_a = self.invitation()
        self.assertEqual(self.invitation().pk, team_a.pk)
        with self.assertRaisesMessage(ValueError, '其他组队'): self.invitation(partner=2)
        with self.assertRaisesMessage(ValueError, '已参加'): self.invitation(partner=2, key='new-key')
        team_c = self.invitation(captain=2, partner=1, key='request-c')
        self.confirm(team_a)
        with self.assertRaisesMessage(ValueError, '已参加'): self.confirm(team_c)
        self.withdraw(team_a, person=1)
        self.assertEqual(self.withdraw(team_a).status, 'WITHDRAWN')
        team_a.refresh_from_db(); self.assertEqual(team_a.withdrawn_by_id, self.accounts[1].pk)
        self.assertEqual(team_a.occupied_seats.count(), 0)
        self.assertEqual(team_a.members.filter(joined=True).count(), 2)
        self.assertEqual(self.confirm(team_c).status, 'UNVERIFIED')
        self.assertEqual(OrderGrabRoundSeat.objects.filter(round=self.round, account=self.accounts[1]).get().team_id, team_c.pk)
        self.assertEqual(self.invitation().status, 'WITHDRAWN')

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_unauthorized_confirmation_and_failed_seat_write_roll_back(self):
        team = self.invitation()
        with transaction.atomic(), self.assertRaisesMessage(ValueError, '受邀队友本人'):
            confirm(self.round.pk, team.pk, self.accounts[2].pk)
        with transaction.atomic(), self.assertRaisesMessage(ValueError, '只能退出自己的组队'):
            withdraw(self.round.pk, team.pk, self.accounts[2].pk)
        with patch('orders.grab_team_draft.OrderGrabRoundSeat.objects.create', side_effect=RuntimeError('write failed')):
            with self.assertRaisesMessage(RuntimeError, 'write failed'):
                self.confirm(team)
        team.refresh_from_db()
        self.assertEqual(team.status, 'INVITING')
        self.assertFalse(team.members.get(account=self.accounts[1]).joined)
        self.assertEqual(team.occupied_seats.count(), 1)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_invitation_seat_failure_rolls_back_team_and_members(self):
        with patch('orders.grab_team_draft.OrderGrabRoundSeat.objects.create', side_effect=RuntimeError('seat failed')):
            with self.assertRaisesMessage(RuntimeError, 'seat failed'):
                self.invitation()
        self.assertFalse(OrderGrabTeamDraft.objects.exists())
        self.assertFalse(OrderGrabTeamDraftMember.objects.exists())
        self.assertFalse(OrderGrabRoundSeat.objects.exists())

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_missing_seat_stops_replay_and_exit(self):
        team = self.invitation()
        team.occupied_seats.all().delete()
        with self.assertRaisesMessage(ValueError, '占位记录不一致'): self.invitation()
        with self.assertRaisesMessage(ValueError, '占位记录不一致'): self.withdraw(team)

    @override_settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True, LEGACY_FORCE_AUTH_COMPAT=False)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_each_member_receipt_is_linked_without_making_a_rankable_candidate(self):
        captain, partner = self.accounts[0], self.accounts[1]
        a = record_intent(self.round.pk, captain.pk, 'INVITE', 'evidence-a', {'partner_id': partner.pk})
        self.assertEqual(a.state, 'BEFORE')
        team = self.invitation(key='evidence-a')
        with transaction.atomic():
            member_a = attach_pass_evidence(self.round.pk, team.pk, a.receipt_id)
            self.assertEqual(member_a.receipt_pass_evidence.submitted_tier, 'BLACK')
            self.assertEqual(attach_pass_evidence(self.round.pk, team.pk, a.receipt_id).pk, member_a.pk)
        b = record_intent(self.round.pk, partner.pk, 'CONFIRM', 'evidence-b', {'team_id': team.pk})
        self.confirm(team)
        with transaction.atomic():
            member_b = attach_pass_evidence(self.round.pk, team.pk, b.receipt_id)
            self.assertEqual(member_b.receipt_pass_evidence.submitted_tier, 'BLACK')
        team.refresh_from_db()
        self.assertEqual(team.status, 'UNVERIFIED')
        self.assertEqual(team.members.filter(receipt_pass_evidence__isnull=False).count(), 2)
        with self.settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False):
            with transaction.atomic(), self.assertRaisesMessage(ValueError, '时钟尚未验证'):
                attach_pass_evidence(self.round.pk, team.pk, a.receipt_id)

    @override_settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True, LEGACY_FORCE_AUTH_COMPAT=False)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_inviter_recorded_tier_does_not_change_after_expiry_and_new_purchase(self):
        captain, partner = self.accounts[0], self.accounts[1]
        profile = self.profiles[0]
        expiry = timezone.now()+timedelta(seconds=2)
        profile.pass_tier = 'BLACK'; profile.pass_expires_at = expiry
        profile.save(update_fields=['pass_tier', 'pass_expires_at'])
        old_entry = self.pool.entry_passes.get(account_id_snapshot=captain.pk)
        old_entry.expires_at = expiry; old_entry.save(update_fields=['expires_at'])
        wallet = get_wallet(account=captain, user=profile.user)
        wallet.balance = 100000; wallet.save(update_fields=['balance'])
        a = record_intent(self.round.pk, captain.pk, 'INVITE', 'expiry-purchase', {'partner_id': partner.pk})
        self.assertEqual(a.state, 'BEFORE')
        sleep(2.1)
        client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(captain)[0])
        bought = client.post('/api/users/provider-pass/', {'tier': 'GOLD', 'days': 1,
                            'client_request_id': 'after-pass-expiry'}, format='json')
        self.assertEqual(bought.data['code'], 0, bought.data)
        team = self.invitation(key='expiry-purchase')
        with transaction.atomic():
            member = attach_pass_evidence(self.round.pk, team.pk, a.receipt_id)
            self.assertEqual(member.provisional_pass_tier, 'GOLD')
            self.assertEqual(member.receipt_pass_evidence.submitted_tier, 'BLACK')

    @override_settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_receipt_cannot_be_attached_to_a_different_team_or_wrong_actor(self):
        a = record_intent(self.round.pk, self.accounts[0].pk, 'INVITE', 'first-proof',
                          {'partner_id': self.accounts[1].pk})
        team = self.invitation(captain=2, partner=1, key='other-team')
        with transaction.atomic(), self.assertRaisesMessage(ValueError, '不匹配'):
            attach_pass_evidence(self.round.pk, team.pk, a.receipt_id)
        self.assertFalse(team.members.filter(receipt_pass_evidence__isnull=False).exists())

    @override_settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_conflicting_pass_facts_cannot_be_attached(self):
        a = record_intent(self.round.pk, self.accounts[0].pk, 'INVITE', 'corrupt-proof',
                          {'partner_id': self.accounts[1].pk})
        team = self.invitation(key='corrupt-proof')
        evidence = OrderGrabReceiptPassEvidence.objects.get(receipt_id=a.receipt_id)
        evidence.submitted_tier = 'GOLD'; evidence.save(update_fields=['submitted_tier'])
        with transaction.atomic(), self.assertRaisesMessage(ValueError, '证据不一致'):
            attach_pass_evidence(self.round.pk, team.pk, a.receipt_id)
        self.assertIsNone(team.members.get(account=self.accounts[0]).receipt_pass_evidence_id)

    @override_settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_receipt_before_pool_open_cannot_be_attached(self):
        a = record_intent(self.round.pk, self.accounts[0].pk, 'INVITE', 'before-open',
                          {'partner_id': self.accounts[1].pk})
        team = self.invitation(key='before-open')
        from orders.models import OrderGrabReceiptProbe
        before = self.pool.opened_at - timedelta(seconds=1)
        OrderGrabReceiptProbe.objects.filter(pk=a.receipt_id).update(lower_at=before, upper_observed_at=before)
        with transaction.atomic(), self.assertRaisesMessage(ValueError, '登记时间或身份凭证'):
            attach_pass_evidence(self.round.pk, team.pk, a.receipt_id)
        self.assertFalse(team.members.filter(receipt_pass_evidence__isnull=False).exists())

    @override_settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_withdrawn_team_receipt_cannot_be_reused_for_a_new_team(self):
        captain, partner = self.accounts[0], self.accounts[1]
        a = record_intent(self.round.pk, captain.pk, 'INVITE', 'old-team', {'partner_id': partner.pk})
        old = self.invitation(key='old-team')
        with transaction.atomic(): attach_pass_evidence(self.round.pk, old.pk, a.receipt_id)
        self.withdraw(old)
        new = self.invitation(key='new-team')
        with transaction.atomic(), self.assertRaisesMessage(ValueError, '不匹配'):
            attach_pass_evidence(self.round.pk, new.pk, a.receipt_id)
        self.assertFalse(new.members.filter(receipt_pass_evidence__isnull=False).exists())
        self.assertEqual(old.members.filter(receipt_pass_evidence__isnull=False).count(), 1)

    def test_real_mysql_two_teams_race_to_confirm_one_partner(self):
        if connection.vendor != 'mysql':
            self.skipTest('requires isolated MySQL row locks')
        teams = (self.invitation(), self.invitation(captain=2, key='request-c'))
        barrier = Barrier(2)
        def attempt(team):
            close_old_connections()
            try:
                with transaction.atomic():
                    OrderGrabTeamDraft.objects.count()  # establish a repeatable-read snapshot
                    barrier.wait(timeout=10)
                    try:
                        confirm(self.round.pk, team.pk, self.accounts[1].pk)
                        return 'accepted'
                    except ValueError:
                        return 'denied'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            result = list(executor.map(attempt, teams))
        self.assertCountEqual(result, ['accepted', 'denied'])
        self.assertEqual(OrderGrabRoundSeat.objects.filter(round=self.round, account=self.accounts[1]).count(), 1)

    def test_real_mysql_withdraw_and_confirmation_leave_no_seat(self):
        if connection.vendor != 'mysql':
            self.skipTest('requires isolated MySQL row locks')
        team = self.invitation()
        barrier = Barrier(2)
        def attempt(action):
            close_old_connections()
            try:
                with transaction.atomic():
                    OrderGrabTeamDraft.objects.count()
                    barrier.wait(timeout=10)
                    try:
                        if action == 'confirm':
                            confirm(self.round.pk, team.pk, self.accounts[1].pk)
                        else:
                            withdraw(self.round.pk, team.pk, self.accounts[0].pk)
                        return 'ok'
                    except ValueError:
                        return 'denied'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            result = list(executor.map(attempt, ('confirm', 'withdraw')))
        self.assertEqual(result[1], 'ok')
        self.assertIn(result[0], ('ok', 'denied'))
        team.refresh_from_db()
        self.assertEqual(team.status, 'WITHDRAWN')
        self.assertEqual(team.occupied_seats.count(), 0)


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated databases')
@override_settings(PRESTART_SCOPE_SNAPSHOT_ENABLED=True, REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED=True)
class GrabTeamDraftMultiStoreTest(TransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    reset_sequences = True

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_same_round_and_account_ids_do_not_share_seats(self):
        ids = []
        stores = (resolve_store(), xinan_store())
        for store in stores:
            with store_context(store):
                save_grab_window(30, 0, lambda *args: None); save_capacity(3, 0, lambda *args: None)
                people = []
                for i in range(2):
                    user = make_provider(deposit_mode='EARNINGS', pass_tier='BLACK', pass_expires_at=timezone.now()+timedelta(hours=1))
                    account = get_or_create_account_for_legacy_user(user)
                    account.is_openid_bound = True; account.openid = f'multi-team-{i}'
                    account.save(update_fields=['is_openid_bound', 'openid'])
                    profile = user.escort_profile; profile.account = account; profile.wechat_avatar_legacy_exempt = True
                    profile.save(update_fields=['account', 'wechat_avatar_legacy_exempt'])
                    people.append(account.pk)
                service = make_service(billing_provider_count=2)
                order = make_order(make_user(), service=service, payment_status='PAID'); record(order, capture(service))
                with transaction.atomic():
                    pool = create_initial_snapshot(order.pk); round_ = first_round(pool.pk)
                    team = invite(round_.pk, people[0], people[1], 'same-request')
                ids.append((round_.pk, *people, team.pk))
        self.assertEqual(ids[0], ids[1])
        with store_context(stores[0]), transaction.atomic():
            confirm(ids[0][0], ids[0][3], ids[0][2])
        with store_context(stores[1]):
            self.assertEqual(OrderGrabTeamDraft.objects.get(pk=ids[1][3]).status, 'INVITING')
            self.assertEqual(OrderGrabRoundSeat.objects.filter(round_id=ids[1][0]).count(), 1)

    @override_settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_same_ids_cannot_attach_another_stores_pass_evidence(self):
        stores, ids = (resolve_store(), xinan_store()), []
        for store, tier in zip(stores, ('GOLD', 'BRONZE')):
            with store_context(store):
                save_grab_window(30, 0, lambda *args: None); save_capacity(3, 0, lambda *args: None)
                for card in ('BLACK', 'GOLD', 'SILVER', 'BRONZE', 'NONE'):
                    SystemConfig.objects.update_or_create(key=f'provider_pass_delay_seconds_{card}', defaults={'value': '0'})
                people = []
                for i in range(2):
                    user = make_provider(deposit_mode='EARNINGS', pass_tier=tier, pass_expires_at=timezone.now()+timedelta(hours=1))
                    account = get_or_create_account_for_legacy_user(user)
                    account.is_openid_bound = True; account.openid = f'proof-multi-{i}'
                    account.save(update_fields=['is_openid_bound', 'openid'])
                    profile = user.escort_profile; profile.account = account; profile.wechat_avatar_legacy_exempt = True
                    profile.save(update_fields=['account', 'wechat_avatar_legacy_exempt'])
                    people.append(account.pk)
                service = make_service(billing_provider_count=2)
                order = make_order(make_user(), service=service, payment_status='PAID'); record(order, capture(service))
                with transaction.atomic():
                    pool = create_initial_snapshot(order.pk); round_ = first_round(pool.pk)
                    team = invite(round_.pk, people[0], people[1], 'same-proof-key')
                ids.append((round_.pk, *people, team.pk))
        self.assertEqual(ids[0], ids[1])
        with store_context(stores[0]):
            a = record_intent(ids[0][0], ids[0][1], 'INVITE', 'same-proof-key', {'partner_id': ids[0][2]})
            with transaction.atomic():
                self.assertEqual(attach_pass_evidence(ids[0][0], ids[0][3], a.receipt_id).receipt_pass_evidence.submitted_tier, 'GOLD')
        with store_context(stores[1]):
            with transaction.atomic(), self.assertRaisesMessage(ValueError, '登记时间尚未核验'):
                attach_pass_evidence(ids[1][0], ids[1][3], a.receipt_id)
            b = record_intent(ids[1][0], ids[1][1], 'INVITE', 'same-proof-key', {'partner_id': ids[1][2]})
            with transaction.atomic():
                self.assertEqual(attach_pass_evidence(ids[1][0], ids[1][3], b.receipt_id).receipt_pass_evidence.submitted_tier, 'BRONZE')
