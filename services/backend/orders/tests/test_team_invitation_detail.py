"""Invitation links remain usable even when the public pool is unavailable."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import json
import os
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch
from unittest import skipUnless

from django.test import override_settings
from django.utils import timezone
from django.db import close_old_connections, connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderApplication
from orders.tests import test_release_flow as release_flow
from orders.tests.factories import make_provider, make_service


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_RECHARGE_PROOF=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class TeamInvitationDetailTest(APITransactionTestCase):
    setUp = release_flow.ReleaseFlowTest.setUp
    login = release_flow.ReleaseFlowTest.login
    ok = release_flow.ReleaseFlowTest.ok
    recharge = release_flow.ReleaseFlowTest.recharge

    def invite(self):
        self.partner = make_provider()
        self.outsider = make_provider()
        for user in (self.provider, self.partner, self.outsider):
            self.accounts[user.pk] = get_or_create_account_for_legacy_user(user)
            profile = user.escort_profile
            profile.account = self.accounts[user.pk]
            profile.pass_tier = 'BLACK'
            profile.pass_expires_at = timezone.now() + timedelta(days=1)
            profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at'])
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        self.ok(self.recharge())
        self.login(self.boss)
        data = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'dispatch_mode': 'BOSS_SELECT',
            'client_request_id': 'invitation-detail-order', 'support_contact_id': self.card.pk,
        }, format='json'))
        self.order = Order.objects.get(pk=data['id'])
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{self.order.pk}/grab/', {
            'team_size': 2, 'partner_id': self.accounts[self.partner.pk].pk,
        }, format='json'))
        self.application = OrderApplication.objects.get(order=self.order)
        self.path = f'/api/orders/orders/{self.order.pk}/applications/{self.application.pk}/mine/'
        self.action = f'/api/orders/orders/{self.order.pk}/applications/{self.application.pk}/action/'

    def test_offline_partner_sees_invitation_reason_and_can_withdraw(self):
        self.invite()
        self.login(self.outsider)
        self.assertEqual(self.client.get(self.path).status_code, 404)
        self.login(self.partner)
        profile = self.partner.escort_profile
        profile.status = profile.Status.OFFLINE
        profile.save(update_fields=['status'])
        pool = self.ok(self.client.get('/api/orders/orders/', {'status': 'pending'}))
        self.assertFalse(any(order['id'] == self.order.pk for order in pool))
        result = self.ok(self.client.get(self.path))
        self.assertEqual((result['order']['id'], result['application']['id']), (self.order.pk, self.application.pk))
        self.assertFalse(result['can_confirm'])
        self.assertIn('离线', result['confirm_reason'])
        self.assertNotEqual(self.client.post(self.action, {'action': 'CONFIRM_TEAM'}, format='json').data['code'], 0)
        self.ok(self.client.post(self.action, {'action': 'WITHDRAW'}, format='json'))
        withdrawn = self.ok(self.client.get(self.path))
        self.assertEqual(withdrawn['application']['status'], 'WITHDRAWN')
        self.assertEqual(withdrawn['closed_reason'], '报名已撤回。')
        self.login(self.boss)
        self.assertFalse(self.ok(self.client.get(f'/api/orders/orders/{self.order.pk}/'))['applications'])

    def test_catalog_rename_keeps_original_name_in_order_and_invitation(self):
        self.invite()
        original_name = self.order.service_name_snapshot
        self.service.name = '改名后的服务'
        self.service.save(update_fields=['name'])

        self.login(self.partner)
        invitation_order = self.ok(self.client.get(self.path))['order']
        pool_order = next(row for row in self.ok(self.client.get('/api/orders/orders/', {'status': 'pending'}))
                          if row['id'] == self.order.pk)
        for row in (invitation_order, pool_order):
            self.assertEqual(row['service_name_snapshot'], original_name)
            self.assertEqual(row['service']['name'], '改名后的服务')

        self.login(self.boss)
        boss_order = self.ok(self.client.get(f'/api/orders/orders/{self.order.pk}/'))
        self.assertEqual(boss_order['service_name_snapshot'], original_name)

    def test_capacity_clearing_allows_confirm_and_selection(self):
        self.invite()
        single = make_service(price=200, commission_rate=20)
        for index in range(Order.MAX_CONCURRENT_ORDERS):
            self.login(self.boss)
            created = self.ok(self.client.post('/api/orders/create/', {
                'service_id': single.pk, 'dispatch_mode': 'AUTO_GRAB',
                'client_request_id': f'invitation-capacity-{index}', 'support_contact_id': self.card.pk,
            }, format='json'))
            self.login(self.partner)
            self.ok(self.client.post(f"/api/orders/orders/{created['id']}/grab/", {}, format='json'))
        self.login(self.partner)
        self.assertFalse(any(item['id'] == self.order.pk for item in self.ok(self.client.get('/api/orders/orders/', {'status': 'pending'}))))
        detail = self.ok(self.client.get(self.path))
        self.assertFalse(detail['can_confirm'])
        self.assertIn('名额已满', detail['confirm_reason'])
        self.assertNotEqual(self.client.post(self.action, {'action': 'CONFIRM_TEAM'}, format='json').data['code'], 0)
        # One real assigned service is completed, freeing a slot.
        assigned = Order.objects.filter(provider_account=self.accounts[self.partner.pk], status='GRABBED').first()
        self.ok(self.client.post(f'/api/orders/orders/{assigned.pk}/start/', {'entry_image': release_flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{assigned.pk}/complete/', {'completion_image': release_flow.screenshot()}, format='multipart'))
        self.assertTrue(self.ok(self.client.get(self.path))['can_confirm'])
        self.ok(self.client.post(self.action, {'action': 'CONFIRM_TEAM'}, format='json'))
        confirmed = self.ok(self.client.get(self.path))
        self.assertTrue(confirmed['application']['partner_confirmed'])
        self.assertFalse(confirmed['can_confirm'])
        self.login(self.boss)
        self.assertTrue(self.ok(self.client.get(f'/api/orders/orders/{self.order.pk}/'))['applications'][0]['ready'])
        another = self.ok(self.client.post('/api/orders/create/', {
            'service_id': single.pk, 'dispatch_mode': 'AUTO_GRAB',
            'client_request_id': 'invitation-capacity-after-confirm', 'support_contact_id': self.card.pk,
        }, format='json'))
        self.login(self.partner)
        self.ok(self.client.post(f"/api/orders/orders/{another['id']}/grab/", {}, format='json'))
        self.login(self.boss)
        blocked = self.ok(self.client.get(f'/api/orders/orders/{self.order.pk}/'))['applications'][0]
        self.assertFalse(blocked['ready'])
        self.assertIn('名额已满', blocked['not_ready_reason'])
        self.login(self.partner)
        self.ok(self.client.post(f"/api/orders/orders/{another['id']}/start/", {'entry_image': release_flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f"/api/orders/orders/{another['id']}/complete/", {'completion_image': release_flow.screenshot()}, format='multipart'))
        self.login(self.boss)
        self.assertTrue(self.ok(self.client.get(f'/api/orders/orders/{self.order.pk}/'))['applications'][0]['ready'])
        selected = self.ok(self.client.post(f'/api/orders/orders/{self.order.pk}/applications/{self.application.pk}/select/', {}, format='json'))
        self.assertEqual(selected['status'], 'GRABBED')
        self.login(self.partner)
        self.assertEqual(self.ok(self.client.get(self.path))['application']['status'], 'SELECTED')

    def test_boss_sees_current_eligibility_after_confirmed_partner_goes_offline(self):
        self.invite()
        self.login(self.partner)
        self.ok(self.client.post(self.action, {'action': 'CONFIRM_TEAM'}, format='json'))
        self.login(self.boss)
        detail_path = f'/api/orders/orders/{self.order.pk}/'
        row = self.ok(self.client.get(detail_path))['applications'][0]
        self.assertTrue(row['ready'])
        self.login(self.partner)
        self.ok(self.client.post('/api/users/escorts/status/', {'status': 'OFFLINE'}, format='json'))
        self.login(self.boss)
        row = self.ok(self.client.get(detail_path))['applications'][0]
        self.assertFalse(row['ready'])
        self.assertIn('离线', row['not_ready_reason'])
        failed = self.client.post(f'/api/orders/orders/{self.order.pk}/applications/{self.application.pk}/select/', {}, format='json')
        self.assertNotEqual(failed.data['code'], 0)
        self.login(self.partner)
        self.ok(self.client.post('/api/users/escorts/status/', {'status': 'AVAILABLE'}, format='json'))
        self.login(self.boss)
        self.assertTrue(self.ok(self.client.get(detail_path))['applications'][0]['ready'])
        self.assertEqual(self.ok(self.client.post(f'/api/orders/orders/{self.order.pk}/applications/{self.application.pk}/select/', {}, format='json'))['status'], 'GRABBED')

    def test_boss_details_batch_readiness_for_many_real_applications(self):
        self.invite()
        self.login(self.partner)
        self.ok(self.client.post(self.action, {'action': 'CONFIRM_TEAM'}, format='json'))
        detail_path = f'/api/orders/orders/{self.order.pk}/'
        self.login(self.boss)
        with CaptureQueriesContext(connection) as first:
            self.assertTrue(self.ok(self.client.get(detail_path))['applications'][0]['ready'])
        for _ in range(4):
            applicant = make_provider()
            account = get_or_create_account_for_legacy_user(applicant)
            self.accounts[applicant.pk] = account
            profile = applicant.escort_profile
            profile.account = account
            profile.pass_tier = 'BLACK'
            profile.pass_expires_at = timezone.now() + timedelta(days=1)
            profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at'])
            self.login(applicant)
            self.ok(self.client.post(f'/api/orders/orders/{self.order.pk}/grab/', {
                'team_size': 2, 'partner_id': self.accounts[self.partner.pk].pk,
            }, format='json'))
            application = OrderApplication.objects.get(order=self.order, applicant=account)
            self.login(self.partner)
            self.ok(self.client.post(f'/api/orders/orders/{self.order.pk}/applications/{application.pk}/action/',
                                     {'action': 'CONFIRM_TEAM'}, format='json'))
        self.login(self.boss)
        with CaptureQueriesContext(connection) as many:
            rows = self.ok(self.client.get(detail_path))['applications']
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(row['ready'] for row in rows))
        self.assertLessEqual(len(many), len(first) + 2, f'queries grew {len(first)} -> {len(many)}')

    def test_legacy_order_notice_lists_both_invitations_without_guessing(self):
        self.invite()
        self.login(self.outsider)
        self.ok(self.client.post(f'/api/orders/orders/{self.order.pk}/grab/', {
            'team_size': 2, 'partner_id': self.accounts[self.partner.pk].pk,
        }, format='json'))
        second = OrderApplication.objects.get(order=self.order, applicant=self.accounts[self.outsider.pk])
        self.login(self.partner)
        path = f'/api/orders/orders/{self.order.pk}/applications/mine/'
        applications = self.ok(self.client.get(path))['applications']
        self.assertEqual({row['id'] for row in applications}, {self.application.pk, second.pk})
        self.assertTrue(all(row['is_partner'] and not row['partner_confirmed'] for row in applications))
        self.ok(self.client.post(self.action, {'action': 'WITHDRAW'}, format='json'))
        remaining = self.ok(self.client.get(path))['applications']
        self.assertEqual([row['id'] for row in remaining], [second.pk])
        detail = self.ok(self.client.get(f'/api/orders/orders/{self.order.pk}/applications/{second.pk}/mine/'))
        self.assertEqual(detail['application']['id'], second.pk)
        unrelated = make_provider()
        self.accounts[unrelated.pk] = get_or_create_account_for_legacy_user(unrelated)
        self.login(unrelated)
        self.assertEqual(self.ok(self.client.get(path))['applications'], [])

    def test_expired_invitation_explains_that_registration_is_closed(self):
        self.invite()
        self.login(self.partner)
        with patch('django.utils.timezone.now', return_value=self.order.auto_cancel_at + timedelta(seconds=1)):
            response = self.client.get(self.path)
            self.assertEqual((response.status_code, response.data['code']), (409, 409))
            self.assertIn('结束报名', response.data['msg'])

    def check_withdraw_notifies_other_member(self, actor, recipient, *, confirmed=False):
        self.invite()
        if confirmed:
            self.login(self.partner)
            self.ok(self.client.post(self.action, {'action': 'CONFIRM_TEAM'}, format='json'))
        actor_user = actor(self)
        declined = actor_user.pk == self.partner.pk and not confirmed
        expected_title = '队友已拒绝组队邀请' if declined else '队友已撤回组队报名'
        self.login(actor_user)
        self.ok(self.client.post(self.action, {'action': 'WITHDRAW'}, format='json'))
        repeated = self.client.post(self.action, {'action': 'WITHDRAW'}, format='json')
        self.assertNotEqual(repeated.data['code'], 0)
        result = self.ok(self.client.get(self.path))
        self.assertEqual((result['application']['status'], result['can_confirm']), ('WITHDRAWN', False))
        self.assertEqual(result['closed_reason'], '报名已撤回。')
        self.login(recipient(self))
        result = self.ok(self.client.get(self.path))
        self.assertEqual(result['closed_reason'], '报名已撤回。')
        notices = self.ok(self.client.get('/api/messages/'))['messages']
        notices = [row for row in notices if row['title'] == expected_title]
        self.assertEqual(len(notices), 1)
        detail = self.ok(self.client.get(f"/api/messages/{notices[0]['id']}/"))
        self.assertEqual(detail['related_order_id'], self.order.pk)
        self.assertIn(self.order.order_no, detail['preview'])
        self.assertIn('组队邀请已被拒绝' if declined else '组队报名已撤回', detail['preview'])
        self.assertNotIn('application_id=', detail['action_url'])
        self.assertEqual(detail['action_url'], '/pages/orders/index?tab=pool')
        available = self.ok(self.client.get('/api/orders/orders/', {'status': 'pending'}))
        self.assertTrue(any(row['id'] == self.order.pk for row in available))
        node = shutil.which('node')
        if node and not declined:
            repo = Path(__file__).resolve().parents[4]
            env = {**os.environ, 'XA_WITHDRAWN_TEAM_NOTICE': json.dumps(detail, ensure_ascii=False)}
            result = subprocess.run([node, '--test', str(repo / 'apps/provider-miniapp/tests/message-order-navigation.cjs')],
                                    cwd=repo, env=env, capture_output=True, text=True, encoding='utf-8', timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('real withdrawn-team notice opens the pool', result.stdout)
        self.login(self.outsider)
        self.assertEqual(self.client.get(self.path).status_code, 404)
        self.assertFalse(any(row['title'] == expected_title
                             for row in self.ok(self.client.get('/api/messages/'))['messages']))
        self.login(self.boss)
        self.assertFalse(self.ok(self.client.get(f'/api/orders/orders/{self.order.pk}/'))['applications'])

    def test_partner_withdraw_notifies_applicant(self):
        self.check_withdraw_notifies_other_member(lambda case: case.partner, lambda case: case.provider)

    def test_confirmed_partner_withdraw_notifies_applicant(self):
        self.check_withdraw_notifies_other_member(lambda case: case.partner, lambda case: case.provider, confirmed=True)

    def test_unconfirmed_applicant_withdraw_notifies_partner(self):
        self.check_withdraw_notifies_other_member(lambda case: case.provider, lambda case: case.partner)

    def test_withdrawn_detail_remains_true_after_order_cancelled(self):
        self.invite()
        self.login(self.partner)
        self.ok(self.client.post(self.action, {'action': 'WITHDRAW'}, format='json'))
        self.login(self.boss)
        self.ok(self.client.post(f'/api/orders/orders/{self.order.pk}/cancel/', {}, format='json'))
        self.login(self.provider)
        result = self.ok(self.client.get(self.path))
        self.assertEqual(result['closed_reason'], '报名已撤回。')
        self.assertEqual(result['order']['status'], 'CANCELLED')

    def test_reapplication_with_another_partner_does_not_expose_new_round_to_former_partner(self):
        self.invite()
        self.login(self.provider)
        self.ok(self.client.post(self.action, {'action': 'WITHDRAW'}, format='json'))
        self.ok(self.client.post(f'/api/orders/orders/{self.order.pk}/grab/', {
            'team_size': 2, 'partner_id': self.accounts[self.outsider.pk].pk,
        }, format='json'))
        self.login(self.partner)
        self.assertEqual(self.ok(self.client.get(self.path))['application']['status'], 'WITHDRAWN')
        current = OrderApplication.objects.get(order=self.order, applicant=self.accounts[self.provider.pk], status='PENDING')
        current_path = f'/api/orders/orders/{self.order.pk}/applications/{current.pk}/mine/'
        self.login(self.outsider)
        self.assertEqual(self.client.get(self.path).status_code, 404)
        result = self.ok(self.client.get(current_path))
        self.assertEqual(result['application']['status'], 'PENDING')
        self.assertTrue(result['application']['is_partner'])
        self.assertTrue(result['can_confirm'])

    def test_same_partner_reinvitation_keeps_old_link_closed_and_new_round_actionable(self):
        self.invite()
        self.login(self.partner)
        self.ok(self.client.post(self.action, {'action': 'WITHDRAW'}, format='json'))
        self.login(self.provider)
        result = self.ok(self.client.post(f'/api/orders/orders/{self.order.pk}/grab/', {
            'team_size': 2, 'partner_id': self.accounts[self.partner.pk].pk,
        }, format='json'))
        current = OrderApplication.objects.get(order=self.order, applicant=self.accounts[self.provider.pk], status='PENDING')
        self.assertNotEqual(current.pk, self.application.pk)
        self.assertEqual(result['my_applications'][0]['id'], current.pk)
        self.login(self.partner)
        self.assertEqual(self.ok(self.client.get(self.path))['application']['status'], 'WITHDRAWN')
        self.assertNotEqual(self.client.post(self.action, {'action': 'CONFIRM_TEAM'}, format='json').data['code'], 0)
        current.refresh_from_db()
        self.assertFalse(current.partner_confirmed)
        new_action = f'/api/orders/orders/{self.order.pk}/applications/{current.pk}/action/'
        self.ok(self.client.post(new_action, {'action': 'CONFIRM_TEAM'}, format='json'))
        self.login(self.boss)
        choices = self.ok(self.client.get(f'/api/orders/orders/{self.order.pk}/'))['applications']
        self.assertEqual([(row['id'], row['ready']) for row in choices], [(current.pk, True)])
        self.ok(self.client.post(f'/api/orders/orders/{self.order.pk}/applications/{current.pk}/select/', {}, format='json'))
        self.login(self.partner)
        self.assertEqual(self.ok(self.client.get(self.path))['application']['status'], 'WITHDRAWN')
        current_path = f'/api/orders/orders/{self.order.pk}/applications/{current.pk}/mine/'
        selected = self.ok(self.client.get(current_path))
        self.assertEqual(selected['application']['status'], 'SELECTED')
        self.assertEqual(selected['closed_reason'], '本次报名已选中，请查看订单状态。')

    def test_real_messages_keep_each_invitation_round_after_reinviting_and_changing_partner(self):
        self.invite()
        order_id = self.order.pk

        def invitation_messages():
            messages = self.ok(self.client.get('/api/messages/'))['messages']
            return [row for row in messages if row['title'] == '收到组队邀请' and row['related_order_id'] == order_id]

        self.login(self.partner)
        first = invitation_messages()
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]['action_url'], f'/pages/orders/index?application_id={self.application.pk}')
        self.ok(self.client.post(self.action, {'action': 'WITHDRAW'}, format='json'))

        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/grab/', {
            'team_size': 2, 'partner_id': self.accounts[self.partner.pk].pk,
        }, format='json'))
        second = OrderApplication.objects.get(order=self.order, applicant=self.accounts[self.provider.pk], status='PENDING')
        self.assertNotEqual(second.pk, self.application.pk)
        self.login(self.partner)
        invitations = invitation_messages()
        self.assertEqual({row['action_url'] for row in invitations}, {
            f'/pages/orders/index?application_id={self.application.pk}',
            f'/pages/orders/index?application_id={second.pk}',
        })
        self.assertEqual(self.ok(self.client.get(self.path))['application']['status'], 'WITHDRAWN')
        self.assertNotEqual(self.client.post(self.action, {'action': 'CONFIRM_TEAM'}, format='json').data['code'], 0)
        second_action = f'/api/orders/orders/{order_id}/applications/{second.pk}/action/'
        self.ok(self.client.post(second_action, {'action': 'CONFIRM_TEAM'}, format='json'))
        self.login(self.provider)
        self.ok(self.client.post(second_action, {'action': 'WITHDRAW'}, format='json'))
        result = self.ok(self.client.post(f'/api/orders/orders/{order_id}/grab/', {
            'team_size': 2, 'partner_id': self.accounts[self.outsider.pk].pk,
        }, format='json'))
        third = OrderApplication.objects.get(order=self.order, applicant=self.accounts[self.provider.pk], status='PENDING')
        self.assertEqual([row['id'] for row in result['my_applications']], [third.pk])

        self.login(self.partner)
        self.assertEqual(self.ok(self.client.get(self.path))['application']['status'], 'WITHDRAWN')
        second_path = f'/api/orders/orders/{order_id}/applications/{second.pk}/mine/'
        self.assertEqual(self.ok(self.client.get(second_path))['application']['status'], 'WITHDRAWN')
        self.assertEqual(self.client.get(f'/api/orders/orders/{order_id}/applications/{third.pk}/mine/').status_code, 404)

        self.login(self.outsider)
        latest = invitation_messages()
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]['action_url'], f'/pages/orders/index?application_id={third.pk}')
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/applications/{third.pk}/action/',
                                 {'action': 'CONFIRM_TEAM'}, format='json'))
        self.login(self.boss)
        choices = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['applications']
        self.assertEqual([(row['id'], row['ready']) for row in choices], [(third.pk, True)])
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/applications/{third.pk}/select/', {}, format='json'))
        self.login(self.outsider)
        self.assertEqual(self.ok(self.client.get(f'/api/orders/orders/{order_id}/applications/{third.pk}/mine/'))['application']['status'], 'SELECTED')

    @skipUnless(connection.vendor == 'mysql', 'Requires real MySQL order row locks')
    def test_concurrent_reinvites_share_one_new_round(self):
        self.invite()
        self.login(self.partner)
        self.ok(self.client.post(self.action, {'action': 'WITHDRAW'}, format='json'))
        barrier = Barrier(2)
        token, _ = issue_account_tokens(self.accounts[self.provider.pk])
        def invite_again():
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                barrier.wait(timeout=10)
                return client.post(f'/api/orders/orders/{self.order.pk}/grab/', {
                    'team_size': 2, 'partner_id': self.accounts[self.partner.pk].pk,
                }, format='json').data
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result(timeout=30) for future in [pool.submit(invite_again), pool.submit(invite_again)]]
        self.assertEqual([result['code'] for result in results], [0, 0], results)
        rows = list(OrderApplication.objects.filter(order=self.order, applicant=self.accounts[self.provider.pk]).order_by('id'))
        self.assertEqual([row.status for row in rows], ['WITHDRAWN', 'PENDING'])
        self.assertNotEqual(rows[0].pk, rows[1].pk)
        self.assertFalse(rows[1].partner_confirmed)

    def test_withdrawn_detail_remains_true_after_another_team_is_selected(self):
        self.invite()
        self.login(self.partner)
        self.ok(self.client.post(self.action, {'action': 'WITHDRAW'}, format='json'))
        new_partner = make_provider()
        account = get_or_create_account_for_legacy_user(new_partner)
        profile = new_partner.escort_profile
        profile.account = account
        profile.pass_tier = 'BLACK'
        profile.pass_expires_at = timezone.now() + timedelta(days=1)
        profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at'])
        self.login(self.outsider)
        self.ok(self.client.post(f'/api/orders/orders/{self.order.pk}/grab/', {
            'team_size': 2, 'partner_id': account.pk,
        }, format='json'))
        other = OrderApplication.objects.get(order=self.order, applicant=self.accounts[self.outsider.pk])
        self.accounts[new_partner.pk] = account
        self.login(new_partner)
        self.ok(self.client.post(f'/api/orders/orders/{self.order.pk}/applications/{other.pk}/action/',
                                 {'action': 'CONFIRM_TEAM'}, format='json'))
        self.login(self.boss)
        self.ok(self.client.post(f'/api/orders/orders/{self.order.pk}/applications/{other.pk}/select/', {}, format='json'))
        self.login(self.partner)
        result = self.ok(self.client.get(self.path))
        self.assertEqual(result['closed_reason'], '报名已撤回。')
        self.assertEqual(result['order']['status'], 'GRABBED')

    def test_applicant_withdraw_after_confirmation_notifies_partner(self):
        self.check_withdraw_notifies_other_member(lambda case: case.provider, lambda case: case.partner, confirmed=True)

    def test_boss_selection_notifies_unselected_team_and_links_to_its_result(self):
        self.invite()
        self.login(self.partner)
        self.ok(self.client.post(self.action, {'action': 'CONFIRM_TEAM'}, format='json'))
        other_partner = make_provider()
        other_account = get_or_create_account_for_legacy_user(other_partner)
        self.accounts[other_partner.pk] = other_account
        profile = other_partner.escort_profile
        profile.account = other_account
        profile.pass_tier = 'BLACK'
        profile.pass_expires_at = timezone.now() + timedelta(days=1)
        profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at'])
        self.login(self.outsider)
        self.ok(self.client.post(f'/api/orders/orders/{self.order.pk}/grab/', {
            'team_size': 2, 'partner_id': other_account.pk,
        }, format='json'))
        other = OrderApplication.objects.get(order=self.order, applicant=self.accounts[self.outsider.pk])
        self.login(other_partner)
        self.ok(self.client.post(f'/api/orders/orders/{self.order.pk}/applications/{other.pk}/action/',
                                 {'action': 'CONFIRM_TEAM'}, format='json'))
        # The selected partner can also be invited by another applicant. The
        # losing applicant gets a result; the selected partner must not get a
        # contradictory "another provider was chosen" message.
        shared_applicant = make_provider()
        shared_account = get_or_create_account_for_legacy_user(shared_applicant)
        self.accounts[shared_applicant.pk] = shared_account
        profile = shared_applicant.escort_profile
        profile.account = shared_account
        profile.pass_tier = 'BLACK'
        profile.pass_expires_at = timezone.now() + timedelta(days=1)
        profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at'])
        self.login(shared_applicant)
        self.ok(self.client.post(f'/api/orders/orders/{self.order.pk}/grab/', {
            'team_size': 2, 'partner_id': self.accounts[self.partner.pk].pk,
        }, format='json'))
        shared = OrderApplication.objects.get(order=self.order, applicant=shared_account)
        self.login(self.partner)
        self.ok(self.client.post(f'/api/orders/orders/{self.order.pk}/applications/{shared.pk}/action/',
                                 {'action': 'CONFIRM_TEAM'}, format='json'))
        self.login(self.boss)
        choose = f'/api/orders/orders/{self.order.pk}/applications/{self.application.pk}/select/'
        self.ok(self.client.post(choose, {}, format='json'))
        self.ok(self.client.post(choose, {}, format='json'))
        for user in (self.outsider, other_partner):
            self.login(user)
            messages = self.ok(self.client.get('/api/messages/'))['messages']
            notices = [message for message in messages if message['title'] == '报名结果已更新']
            self.assertEqual(len(notices), 1)
            from site_messages.models import Message
            self.assertFalse(Message.objects.get(pk=notices[0]['id']).assignment_alert)
            self.assertFalse(self.ok(self.client.get('/api/messages/assignment-alerts/', {'after': 0}))['pending'])
            notice = self.ok(self.client.get(f"/api/messages/{notices[0]['id']}/"))
            self.assertEqual(notice['related_order_id'], self.order.pk)
            self.assertEqual(notice['action_url'], f'/pages/orders/index?application_id={other.pk}')
            result = self.ok(self.client.get(f'/api/orders/orders/{self.order.pk}/applications/{other.pk}/mine/'))
            self.assertEqual(result['application']['status'], 'NOT_SELECTED')
            self.assertFalse(result['can_confirm'])
            self.assertIn('选择其他陪玩', result['closed_reason'])
            self.assertNotEqual(self.client.post(f'/api/orders/orders/{self.order.pk}/applications/{other.pk}/action/',
                                                 {'action': 'WITHDRAW'}, format='json').data['code'], 0)
        self.login(self.provider)
        self.assertEqual(self.client.get(f'/api/orders/orders/{self.order.pk}/applications/{other.pk}/mine/').status_code, 404)
        messages = self.ok(self.client.get('/api/messages/'))['messages']
        self.assertFalse(any(message['title'] == '报名结果已更新' for message in messages))
        self.login(self.partner)
        messages = self.ok(self.client.get('/api/messages/'))['messages']
        self.assertFalse(any(message['title'] == '报名结果已更新' for message in messages))
        self.login(shared_applicant)
        notices = [message for message in self.ok(self.client.get('/api/messages/'))['messages']
                   if message['title'] == '报名结果已更新']
        self.assertEqual(len(notices), 1)
        notice = self.ok(self.client.get(f"/api/messages/{notices[0]['id']}/"))
        self.assertEqual(notice['action_url'], f'/pages/orders/index?application_id={shared.pk}')

    def test_single_application_result_is_available_to_unselected_provider(self):
        second = make_provider()
        for user in (self.provider, second):
            account = get_or_create_account_for_legacy_user(user)
            self.accounts[user.pk] = account
            profile = user.escort_profile
            profile.account = account
            profile.pass_tier = 'BLACK'
            profile.pass_expires_at = timezone.now() + timedelta(days=1)
            profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at'])
        self.ok(self.recharge())
        self.login(self.boss)
        order = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'dispatch_mode': 'BOSS_SELECT',
            'client_request_id': 'single-unselected-result', 'support_contact_id': self.card.pk,
        }, format='json'))
        for user in (self.provider, second):
            self.login(user)
            self.ok(self.client.post(f"/api/orders/orders/{order['id']}/grab/", {}, format='json'))
        selected = OrderApplication.objects.get(order_id=order['id'], applicant=self.accounts[self.provider.pk])
        losing = OrderApplication.objects.get(order_id=order['id'], applicant=self.accounts[second.pk])
        self.login(self.boss)
        self.ok(self.client.post(f"/api/orders/orders/{order['id']}/applications/{selected.pk}/select/", {}, format='json'))
        self.login(second)
        notices = [message for message in self.ok(self.client.get('/api/messages/'))['messages']
                   if message['title'] == '报名结果已更新']
        self.assertEqual(len(notices), 1)
        detail = self.ok(self.client.get(f"/api/orders/orders/{order['id']}/applications/{losing.pk}/mine/"))
        self.assertEqual(detail['application']['status'], 'NOT_SELECTED')
        self.assertEqual(detail['application']['team_size'], 1)
        self.assertIn('报名结束', detail['closed_reason'])
