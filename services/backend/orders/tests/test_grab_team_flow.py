import os
from unittest import skipUnless
import time
from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from console.capacity_config import save_capacity
from console.grab_config import save_grab_window
from orders.grab_team_flow import resolve
from orders.models import Order, OrderGrabTeamRoundState, ServiceItem
from orders.tests import test_game_commission_payment as fixture
from orders.tests.factories import make_provider, make_console_user
from wallet.models import IdempotencyKey, SystemConfig, Transaction


@override_settings(GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True, GRAB_TEAM_WINDOW_ENABLED=True,
                   GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
                   REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED=True, GAME_COMMISSION_PAYMENT_ENABLED=False,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class GrabTeamFlowTest(APITransactionTestCase):
    setUp = fixture.GameCommissionPaymentTest.setUp
    link = fixture.GameCommissionPaymentTest.link
    auth = fixture.GameCommissionPaymentTest.auth
    create = fixture.GameCommissionPaymentTest.create

    def assert_stale_team_checkout_rejected(self, billing_count, changed_terms):
        from engagement.models import PointFundingLot
        from orders import views

        self.service.fulfillment_provider_count = 2
        self.service.billing_provider_count = billing_count
        self.service.save(update_fields=['fulfillment_provider_count', 'billing_provider_count'])
        save_grab_window(30, 0, lambda *args: None)
        quote = self.client.post('/api/orders/quote/', {'service_id': self.service.pk,
                                 'dispatch_mode': 'AUTO_GRAB'}, format='json')
        self.assertEqual(quote.data['code'], 0, quote.data)
        original_ensure_locks = views.ensure_provider_dispatch_locks

        def change_before_payment(ids):
            original_ensure_locks(ids)
            ServiceItem.objects.filter(pk=self.service.pk).update(**changed_terms)

        with patch('orders.views.ensure_provider_dispatch_locks', side_effect=change_before_payment) as change:
            response = self.create(dispatch_mode='AUTO_GRAB', expected_amount=quote.data['data']['amount'])

        change.assert_called_once()
        self.assertEqual(response.data.get('errorCode'), 'SERVICE_CHANGED', response.data)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 100000)
        self.assertEqual(PointFundingLot.objects.get(account=self.boss_account).remaining, 100000)
        self.assertFalse(Order.objects.exists())
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(IdempotencyKey.objects.filter(account=self.boss_account).exists())

    def test_checkout_rejects_removed_team_seat_after_quote(self):
        self.assert_stale_team_checkout_rejected(1, {'fulfillment_provider_count': 1})

    def test_checkout_rejects_repriced_team_with_same_total(self):
        self.assert_stale_team_checkout_rejected(2, {'price': 10000, 'billing_provider_count': 1})

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_disabled_clock_keeps_existing_team_round_from_legacy_cancel_or_timeout(self):
        from orders.tasks import auto_cancel_pending_order
        self.prepare()
        with self.settings(GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False):
            self.auth(self.boss_account)
            cancel = self.client.post(f'/api/orders/orders/{self.order.pk}/cancel/', {}, format='json')
            self.assertEqual(cancel.status_code, 503, cancel.data)
            self.assertEqual(auto_cancel_pending_order.run(self.order.pk), 'disabled')
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status), ('PENDING', 'PAID'))

    def prepare(self, *, partner_delay=0, with_game=False):
        self.enterContext(patch('orders.grab_team_flow.enqueue'))
        if not with_game:
            self.service.game_category = None
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['game_category', 'billing_provider_count'])
        save_grab_window(1, 0, lambda *args: None)
        save_capacity(3, 0, lambda *args: None)
        for tier in ('BLACK', 'GOLD', 'SILVER', 'BRONZE', 'NONE'):
            seconds = partner_delay if tier in ('SILVER', 'BRONZE', 'NONE') else 0
            SystemConfig.objects.update_or_create(key='provider_pass_delay_seconds_' + tier,
                                                  defaults={'value': str(seconds)})
        for index, account in enumerate(self.accounts):
            account.is_openid_bound, account.openid = True, f'team-window-wechat-{index}'
            account.save(update_fields=['is_openid_bound', 'openid'])
            profile = self.providers[index].escort_profile
            profile.wechat_avatar_verified_at = timezone.now()
            profile.pass_tier = 'BLACK' if index == 0 else 'SILVER'
            profile.pass_expires_at = timezone.now() + timedelta(hours=1)
            profile.save(update_fields=['wechat_avatar_verified_at', 'pass_tier', 'pass_expires_at'])
        self.auth(self.boss_account)
        response = self.create(dispatch_mode='AUTO_GRAB')
        self.assertEqual(response.status_code, 200, response.data)
        self.order = Order.objects.get(pk=response.data['data']['id'])
        time.sleep(0.01)

    def invite(self):
        self.auth(self.accounts[0])
        result = self.client.post(f'/api/orders/orders/{self.order.pk}/grab-team/',
            {'partner_id': self.accounts[1].pk, 'client_request_id': 'captain-invite'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        return result.data['data']['team_id']

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_full_team_is_ranked_and_both_members_receive_the_order(self):
        self.prepare()
        team_id = self.invite()
        self.auth(self.accounts[1])
        response = self.client.post(f'/api/orders/grab-team/invitations/{team_id}/confirm/',
                                    {'client_request_id': 'partner-confirm'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)
        time.sleep(1.05)
        self.assertEqual(resolve(self.order.pk), 'ASSIGNED')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.GRABBED)
        self.assertEqual(set(self.order.providers.values_list('provider_account_id', flat=True)),
                         {self.accounts[0].pk, self.accounts[1].pk})
        self.assertEqual(OrderGrabTeamRoundState.objects.get(round__pool__order=self.order).winner_id, team_id)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_invitee_cannot_see_order_before_delay_and_may_complete_after_empty_round(self):
        self.prepare(partner_delay=2)
        team_id = self.invite()
        self.auth(self.accounts[1])
        listed = self.client.get('/api/orders/grab-team/invitations/')
        self.assertEqual(listed.status_code, 200)
        self.assertFalse(listed.data['data'][0]['order_visible'])
        self.assertNotIn('order_id', listed.data['data'][0])
        rejected = self.client.post(f'/api/orders/grab-team/invitations/{team_id}/confirm/',
                                    {'client_request_id': 'early-confirm'}, format='json')
        self.assertEqual(rejected.status_code, 400)
        time.sleep(1.05)
        self.assertEqual(resolve(self.order.pk), 'PARKED')
        time.sleep(1.0)
        accepted = self.client.post(f'/api/orders/grab-team/invitations/{team_id}/confirm/',
                                    {'client_request_id': 'late-confirm'}, format='json')
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.GRABBED)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_first_winning_team_loses_eligibility_and_reopens(self):
        self.prepare()
        team_id = self.invite()
        self.auth(self.accounts[1])
        result = self.client.post(f'/api/orders/grab-team/invitations/{team_id}/confirm/',
                                  {'client_request_id': 'partner-before-offline'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        profile = self.providers[1].escort_profile
        profile.status = 'OFFLINE'
        profile.save(update_fields=['status'])
        time.sleep(1.05)
        self.assertEqual(resolve(self.order.pk), 'REOPENED')
        states = OrderGrabTeamRoundState.objects.filter(round__pool__order=self.order).order_by('round__number')
        self.assertEqual(list(states.values_list('status', flat=True)), ['REOPENED', 'OPEN'])
        self.assertTrue(states.last().round.simultaneous_visibility)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_two_full_teams_are_ranked_and_both_winning_members_notified(self):
        self.prepare()
        for index, tier in enumerate(('GOLD', 'SILVER'), start=2):
            user = make_provider(deposit_mode='EARNINGS', pass_tier=tier,
                                 pass_expires_at=timezone.now() + timedelta(hours=1))
            account = self.link(user)
            account.is_openid_bound, account.openid = True, f'team-window-wechat-{index}'
            account.save(update_fields=['is_openid_bound', 'openid'])
            profile = user.escort_profile
            profile.wechat_avatar_verified_at = timezone.now()
            profile.save(update_fields=['wechat_avatar_verified_at'])
            profile.service_items.add(self.service)
            self.accounts.append(account)
            self.providers.append(user)
        first_team = self.invite()
        self.auth(self.accounts[1])
        self.assertEqual(self.client.post(f'/api/orders/grab-team/invitations/{first_team}/confirm/',
            {'client_request_id': 'first-team-confirm'}, format='json').status_code, 200)
        self.auth(self.accounts[2])
        response = self.client.post(f'/api/orders/orders/{self.order.pk}/grab-team/',
            {'partner_id': self.accounts[3].pk, 'client_request_id': 'second-team-invite'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        second_team = response.data['data']['team_id']
        self.auth(self.accounts[3])
        self.assertEqual(self.client.post(f'/api/orders/grab-team/invitations/{second_team}/confirm/',
            {'client_request_id': 'second-team-confirm'}, format='json').status_code, 200)
        time.sleep(1.05)
        self.assertEqual(resolve(self.order.pk), 'ASSIGNED')
        self.order.refresh_from_db()
        self.assertEqual(set(self.order.providers.values_list('provider_account_id', flat=True)),
                         {self.accounts[0].pk, self.accounts[1].pk})
        from site_messages.models import Message
        for account in self.accounts[:2]:
            self.assertTrue(Message.objects.filter(recipient_account=account, related_order_id=self.order.pk,
                                                   title='抢单成功').exists())
        for account in self.accounts[2:]:
            self.assertTrue(Message.objects.filter(recipient_account=account, related_order_id=self.order.pk,
                                                   title='本次未抢到订单').exists())

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_boss_cancellation_refunds_a_pending_team(self):
        self.prepare()
        team_id = self.invite()
        self.auth(self.accounts[1])
        accepted = self.client.post(f'/api/orders/grab-team/invitations/{team_id}/confirm/',
            {'client_request_id': 'accepted-before-cancel'}, format='json')
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.auth(self.boss_account)
        result = self.client.post(f'/api/orders/orders/{self.order.pk}/cancel/',
                                  {'reason': '改变计划'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.order.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual((self.order.status, self.wallet.balance), ('CANCELLED', 100000))

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_boss_cancel_after_empty_window_does_not_depend_on_worker(self):
        self.prepare()
        time.sleep(1.05)
        self.auth(self.boss_account)
        result = self.client.post(f'/api/orders/orders/{self.order.pk}/cancel/',
                                  {'reason': '不再需要'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.order.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((self.order.status, self.wallet.balance), ('CANCELLED', 100000))

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_team_member_withdraws_before_switching_to_another_team(self):
        self.prepare()
        user = make_provider(deposit_mode='EARNINGS', pass_tier='GOLD',
                             pass_expires_at=timezone.now() + timedelta(hours=1))
        third = self.link(user)
        third.is_openid_bound, third.openid = True, 'team-switch-wechat'
        third.save(update_fields=['is_openid_bound', 'openid'])
        user.escort_profile.wechat_avatar_verified_at = timezone.now()
        user.escort_profile.save(update_fields=['wechat_avatar_verified_at'])
        user.escort_profile.service_items.add(self.service)
        first_team = self.invite()
        self.auth(self.accounts[1])
        result = self.client.post(f'/api/orders/grab-team/invitations/{first_team}/confirm/',
                                  {'client_request_id': 'confirm-before-switch'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.auth(third)
        occupied = self.client.post(f'/api/orders/orders/{self.order.pk}/grab-team/',
            {'partner_id': self.accounts[1].pk, 'client_request_id': 'occupied-invite'}, format='json')
        self.assertEqual(occupied.status_code, 400)
        self.auth(self.accounts[1])
        time.sleep(0.01)
        result = self.client.post(f'/api/orders/grab-team/invitations/{first_team}/withdraw/',
                                  {'client_request_id': 'leave-before-switch'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.auth(third)
        second = self.client.post(f'/api/orders/orders/{self.order.pk}/grab-team/',
            {'partner_id': self.accounts[1].pk, 'client_request_id': 'second-captain-invite'}, format='json')
        self.assertEqual(second.status_code, 200, second.data)
        self.auth(self.accounts[1])
        result = self.client.post(f"/api/orders/grab-team/invitations/{second.data['data']['team_id']}/confirm/",
                                  {'client_request_id': 'confirm-new-team'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_invitation_never_reveals_order_to_currently_ineligible_partner(self):
        self.prepare()
        self.invite()
        profile = self.providers[1].escort_profile
        profile.status = 'OFFLINE'
        profile.save(update_fields=['status'])
        self.auth(self.accounts[1])
        result = self.client.get('/api/orders/grab-team/invitations/')
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.data['data'][0]['order_visible'])
        self.assertNotIn('order_id', result.data['data'][0])

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_incomplete_team_does_not_block_timeout_refund(self):
        from orders.models import OrderGrabPoolSnapshot, OrderGrabRound
        self.prepare()
        self.invite()
        pool = OrderGrabPoolSnapshot.objects.get(order=self.order)
        OrderGrabPoolSnapshot.objects.filter(pk=pool.pk).update(
            original_total_deadline=pool.first_deadline, total_deadline=pool.first_deadline)
        OrderGrabRound.objects.filter(pool=pool, number=1).update(total_deadline=pool.first_deadline)
        Order.objects.filter(pk=self.order.pk).update(auto_cancel_at=pool.first_deadline)
        time.sleep(1.05)
        self.assertEqual(resolve(self.order.pk), 'CANCELLED')
        self.wallet.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.wallet.balance), ('CANCELLED', 100000))

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_persisted_withdrawal_is_applied_before_allocating_team(self):
        from orders.grab_receipt_probe import record_intent
        from orders.models import OrderGrabTeamDraft
        self.prepare()
        team_id = self.invite()
        self.auth(self.accounts[1])
        result = self.client.post(f'/api/orders/grab-team/invitations/{team_id}/confirm/',
                                  {'client_request_id': 'confirm-before-retry'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        time.sleep(0.01)
        team = OrderGrabTeamDraft.objects.get(pk=team_id)
        receipt = record_intent(team.round_id, self.accounts[1].pk, 'WITHDRAW', 'withdraw-retry',
                                {'team_id': team_id}, team_flow=True)
        self.assertEqual(receipt.state, 'BEFORE')
        time.sleep(1.05)
        self.assertEqual(resolve(self.order.pk), 'WAIT_APPLY')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)
        retried = self.client.post(f'/api/orders/grab-team/invitations/{team_id}/withdraw/',
                                   {'client_request_id': 'withdraw-retry'}, format='json')
        self.assertEqual(retried.status_code, 200, retried.data)
        self.assertEqual(resolve(self.order.pk), 'PARKED')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_parked_disqualified_team_can_form_again_after_eligibility_recovers(self):
        from orders.grab_receipt_probe import record_intent
        from orders.models import OrderGrabTeamDraft
        self.prepare()
        time.sleep(1.05)
        self.assertEqual(resolve(self.order.pk), 'PARKED')
        team_id = self.invite()
        team = OrderGrabTeamDraft.objects.get(pk=team_id)
        result = record_intent(team.round_id, self.accounts[1].pk, 'CONFIRM', 'before-disqualification',
                               {'team_id': team_id}, team_flow=True)
        self.assertEqual(result.state, 'BEFORE')
        profile = self.providers[0].escort_profile
        profile.status = 'OFFLINE'
        profile.save(update_fields=['status'])
        self.assertEqual(resolve(self.order.pk), 'PARKED')
        team.refresh_from_db()
        self.assertEqual(team.status, 'DISQUALIFIED')
        self.assertFalse(team.occupied_seats.exists())
        profile.status = 'AVAILABLE'
        profile.save(update_fields=['status'])
        self.auth(self.accounts[0])
        second = self.client.post(f'/api/orders/orders/{self.order.pk}/grab-team/',
            {'partner_id': self.accounts[1].pk, 'client_request_id': 'invite-after-recovery'}, format='json')
        self.assertEqual(second.status_code, 200, second.data)
        self.auth(self.accounts[1])
        result = self.client.post(f"/api/orders/grab-team/invitations/{second.data['data']['team_id']}/confirm/",
                                  {'client_request_id': 'confirm-after-recovery'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.GRABBED)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_staff_arranged_and_staff_created_double_orders_enter_team_pool(self):
        from orders.models import OrderGrabPoolSnapshot
        self.prepare()
        self.auth(self.boss_account)
        arranged = self.create(dispatch_mode='BOSS_SELECT', dispatch_route='STAFF',
                               client_request_id='boss-staff-team')
        self.assertEqual(arranged.status_code, 200, arranged.data)
        arranged_order = Order.objects.get(pk=arranged.data['data']['id'])
        self.assertEqual(OrderGrabPoolSnapshot.objects.get(order=arranged_order).required_provider_count, 2)

        staff_user = make_console_user(['order:view', 'order:dispatch'])
        staff = self.link(staff_user)
        staff_user.admin_membership.account = staff
        staff_user.admin_membership.save(update_fields=['account'])
        self.auth(staff)
        result = self.client.post('/api/admin/orders/dispatch/', {'customer_id': self.boss_account.pk,
            'service_id': self.service.pk, 'escort_mode': 'DOUBLE', 'client_request_id': 'staff-team-entry'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['code'], 0, result.data)
        self.order = Order.objects.get(pk=result.data['data']['id'])
        self.assertEqual(OrderGrabPoolSnapshot.objects.get(order=self.order).required_provider_count, 2)
        self.assertFalse(self.order.requires_boss_selection)
        time.sleep(0.01)
        team_id = self.invite()
        self.auth(self.accounts[1])
        confirmed = self.client.post(f'/api/orders/grab-team/invitations/{team_id}/confirm/',
            {'client_request_id': 'staff-team-confirm'}, format='json')
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        time.sleep(1.05)
        self.assertEqual(resolve(self.order.pk), 'ASSIGNED')
        self.order.refresh_from_db()
        self.assertTrue(self.order.requires_boss_selection)

    @override_settings(GAME_COMMISSION_PAYMENT_ENABLED=True)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_double_assignment_uses_payment_time_game_commission_snapshot(self):
        self.prepare(with_game=True)
        team_id = self.invite()
        self.auth(self.accounts[1])
        response = self.client.post(f'/api/orders/grab-team/invitations/{team_id}/confirm/',
            {'client_request_id': 'game-snapshot-confirm'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.config.rules = {'companion': 11, 'grind': 50}
        self.config.version = 2
        self.config.save(update_fields=['rules', 'version'])
        time.sleep(1.05)
        self.assertEqual(resolve(self.order.pk), 'ASSIGNED')
        self.order.refresh_from_db()
        self.assertEqual(list(self.order.providers.values_list('commission_rate', flat=True)), [30, 30])
        self.assertTrue(all(row.game_commission_snapshot['version'] == 1 for row in self.order.providers.all()))
