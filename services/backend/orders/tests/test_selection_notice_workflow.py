"""A selected team can discover and start an order after returning online."""
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import Order, OrderApplication
from orders.tests import test_release_flow as release_flow
from orders.tests.factories import make_provider, make_user
from site_messages.models import Message


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_RECHARGE_PROOF=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class SelectionNoticeWorkflowTest(APITransactionTestCase):
    setUp = release_flow.ReleaseFlowTest.setUp
    login = release_flow.ReleaseFlowTest.login
    ok = release_flow.ReleaseFlowTest.ok
    recharge = release_flow.ReleaseFlowTest.recharge

    def test_staff_assignment_notice_reaches_boss_account_when_legacy_id_is_stale(self):
        self.ok(self.recharge())
        self.login(self.boss)
        data = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'dispatch_route': 'STAFF',
            'client_request_id': 'staff-assignment-notice', 'support_contact_id': self.card.pk,
        }, format='json'))
        order = Order.objects.get(pk=data['id'])
        old_boss = make_user()
        self.accounts[old_boss.pk] = get_or_create_account_for_legacy_user(old_boss)
        Order.objects.filter(pk=order.pk).update(customer=old_boss)
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/orders/{order.pk}/assign/',
                                 {'provider_id': self.accounts[self.provider.pk].pk}, format='json'))
        self.login(self.boss)
        rows = self.ok(self.client.get('/api/messages/'))['messages']
        notice = next(item for item in rows if item['title'] == '已为你指派大神')
        self.assertEqual(notice['related_order_id'], order.pk)
        self.assertEqual(self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))['status'], 'GRABBED')
        self.login(old_boss)
        rows = self.ok(self.client.get('/api/messages/'))['messages']
        self.assertFalse(any(item['title'] == '已为你指派大神' for item in rows))

    def test_single_application_selection_notifies_provider_once(self):
        profile = self.provider.escort_profile
        profile.pass_tier = 'BLACK'
        profile.pass_expires_at = timezone.now() + timedelta(days=1)
        profile.save(update_fields=['pass_tier', 'pass_expires_at'])
        self.ok(self.recharge())
        self.login(self.boss)
        order = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'dispatch_mode': 'BOSS_SELECT',
            'support_contact_id': self.card.pk, 'client_request_id': 'single-notice-order',
        }, format='json'))
        self.login(self.provider)
        self.ok(self.client.post(f"/api/orders/orders/{order['id']}/grab/", {}, format='json'))
        application = OrderApplication.objects.get(order_id=order['id'])
        self.login(self.boss)
        select = f"/api/orders/orders/{order['id']}/applications/{application.pk}/select/"
        self.ok(self.client.post(select, {}, format='json'))
        self.ok(self.client.post(select, {}, format='json'))
        self.login(self.provider)
        rows = self.ok(self.client.get('/api/messages/'))['messages']
        notices = [item for item in rows if item['title'] == '老板已选择你服务']
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]['related_order_id'], order['id'])
        self.assertIn('请尽快开始服务', notices[0]['preview'])
        self.assertEqual(self.ok(self.client.get(f"/api/orders/orders/{order['id']}/"))['status'], 'GRABBED')

    def test_previous_selection_describes_rejection_and_new_selection(self):
        replacement = make_provider(display_name='新陪玩')
        for provider in (self.provider, replacement):
            account = get_or_create_account_for_legacy_user(provider)
            self.accounts[provider.pk] = account
            profile = provider.escort_profile
            profile.account = account
            profile.pass_tier = 'BLACK'
            profile.pass_expires_at = timezone.now() + timedelta(days=1)
            profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at'])
        self.ok(self.recharge())
        self.login(self.boss)
        order = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'dispatch_mode': 'BOSS_SELECT',
            'support_contact_id': self.card.pk, 'client_request_id': 'selection-history-rejection',
        }, format='json'))
        order_id = order['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/grab/', {}, format='json'))
        first = OrderApplication.objects.get(order_id=order_id, applicant=self.accounts[self.provider.pk])
        path = f'/api/orders/orders/{order_id}/applications/{first.pk}/mine/'
        self.login(self.boss)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/applications/{first.pk}/select/', {}, format='json'))
        self.login(self.provider)
        self.assertEqual(self.ok(self.client.get(path))['closed_reason'], '本次报名已选中，请查看订单状态。')
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/reject/', {'reason': '时间冲突'}, format='json'))
        old = self.ok(self.client.get(path))
        self.assertEqual(old['application']['status'], 'SELECTED')
        self.assertEqual(old['order']['status'], 'PENDING')
        self.assertEqual(old['closed_reason'], '本次报名曾被选中，订单已回到待接单池。')
        self.login(self.boss)
        self.assertEqual(self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['status'], 'PENDING')
        self.login(replacement)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/grab/', {}, format='json'))
        second = OrderApplication.objects.get(order_id=order_id, applicant=self.accounts[replacement.pk])
        self.login(self.boss)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/applications/{second.pk}/select/', {}, format='json'))
        self.login(self.provider)
        old = self.ok(self.client.get(path))
        self.assertEqual(old['closed_reason'], '本次报名曾被选中，订单已有新的服务安排。')
        self.login(replacement)
        current = self.ok(self.client.get(f'/api/orders/orders/{order_id}/applications/{second.pk}/mine/'))
        self.assertEqual(current['closed_reason'], '本次报名已选中，请查看订单状态。')

    @override_settings(REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    def test_selected_team_detail_tracks_staff_transfer_and_return(self):
        partner, incoming = make_provider(display_name='原队友'), make_provider(display_name='接手陪玩')
        for provider in (self.provider, partner, incoming):
            account = get_or_create_account_for_legacy_user(provider)
            self.accounts[provider.pk] = account
            profile = provider.escort_profile
            profile.account = account
            profile.pass_tier = 'BLACK'
            profile.pass_expires_at = timezone.now() + timedelta(days=1)
            profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at'])
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        self.ok(self.recharge())
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'dispatch_mode': 'BOSS_SELECT',
            'support_contact_id': self.card.pk, 'client_request_id': 'selected-team-transfer-history',
        }, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/grab/', {
            'team_size': 2, 'partner_id': self.accounts[partner.pk].pk,
        }, format='json'))
        application = OrderApplication.objects.get(order_id=order_id)
        path = f'/api/orders/orders/{order_id}/applications/{application.pk}/mine/'
        self.login(partner)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/applications/{application.pk}/action/',
                                 {'action': 'CONFIRM_TEAM'}, format='json'))
        self.login(self.boss)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/applications/{application.pk}/select/', {}, format='json'))
        for provider in (self.provider, partner):
            self.login(provider)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/',
                                     {'entry_image': release_flow.screenshot()}, format='multipart'))
        for old, new in ((self.provider, incoming), (incoming, self.provider)):
            self.login(self.admin)
            self.ok(self.client.post(f'/api/admin/orders/{order_id}/transfer/', {
                'transfers': [{'old_provider_id': self.accounts[old.pk].pk,
                               'new_provider_id': self.accounts[new.pk].pk,
                               'split_type': 'PERCENT', 'split_value': 40, 'penalty_amount': 0}],
                'reason': '隔离转单报名状态验证',
            }, format='json'))
            for viewer in (self.provider, partner):
                self.login(viewer)
                detail = self.ok(self.client.get(path))
                self.assertEqual(detail['order']['status'], 'IN_SERVICE')
                self.assertEqual(detail['closed_reason'],
                    '本次报名曾被选中，当前服务人员已调整。' if new == incoming
                    else '本次报名已选中，请查看订单状态。')

    def test_double_team_invitation_confirmation_and_selection_reach_correct_accounts(self):
        second, outsider = make_provider(), make_provider()
        for user in (self.provider, second, outsider):
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
            'client_request_id': 'team-notice-order', 'support_contact_id': self.card.pk,
        }, format='json'))
        order = Order.objects.get(pk=data['id'])
        self.login(self.provider)
        payload = {'team_size': 2, 'partner_id': self.accounts[second.pk].pk}
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/grab/', payload, format='json'))
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/grab/', payload, format='json'))
        application = OrderApplication.objects.get(order=order)
        self.login(second)
        inbox = self.ok(self.client.get('/api/messages/'))['messages']
        invites = [item for item in inbox if item['title'] == '收到组队邀请']
        self.assertEqual(len(invites), 1)
        self.assertEqual(invites[0]['related_order_id'], order.pk)
        self.assertEqual(self.ok(self.client.get(f"/api/messages/{invites[0]['id']}/"))['action_url'], f'/pages/orders/index?application_id={application.pk}')
        pool = self.ok(self.client.get('/api/orders/orders/', {'status': 'pending'}))
        invited_order = next(item for item in pool if item['id'] == order.pk)
        self.assertTrue(any(item['id'] == application.pk and item['is_partner'] for item in invited_order['my_applications']))
        self.login(outsider)
        self.assertEqual(self.client.get(f'/api/orders/orders/{order.pk}/').status_code, 404)
        self.login(second)
        action = f'/api/orders/orders/{order.pk}/applications/{application.pk}/action/'
        self.ok(self.client.post(action, {'action': 'CONFIRM_TEAM'}, format='json'))
        self.ok(self.client.post(action, {'action': 'CONFIRM_TEAM'}, format='json'))
        self.login(self.provider)
        inbox = self.ok(self.client.get('/api/messages/'))['messages']
        confirmations = [item for item in inbox if item['title'] == '队友已确认组队']
        self.assertEqual(len(confirmations), 1)
        self.assertEqual(self.ok(self.client.get(f"/api/messages/{confirmations[0]['id']}/"))['action_url'], f'/pages/orders/index?application_id={application.pk}')
        self.login(self.boss)
        select = f'/api/orders/orders/{order.pk}/applications/{application.pk}/select/'
        self.ok(self.client.post(select, {}, format='json'))
        self.ok(self.client.post(select, {}, format='json'))
        for user in (self.provider, second):
            self.login(user)
            inbox = self.ok(self.client.get('/api/messages/'))['messages']
            selected = [item for item in inbox if item['title'] == '老板已选择你服务']
            self.assertEqual(len(selected), 1)
            self.assertEqual(selected[0]['related_order_id'], order.pk)
            detail = self.ok(self.client.get(f"/api/orders/orders/{selected[0]['related_order_id']}/"))
            self.assertEqual(detail['status'], 'GRABBED')
            self.assertIn('START', detail['allowed_actions'])
        self.login(outsider)
        self.assertFalse(Message.objects.filter(recipient_account=self.accounts[outsider.pk], related_order_id=order.pk).exists())
