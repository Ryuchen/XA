"""A selected team can discover and start an order after returning online."""
import os
from unittest import skipUnless
from datetime import timedelta
from unittest.mock import patch

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

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '选人通知工作流须本人试音录音/客服指派能力属正式契约深水区（18-盘点 派单/试音待联调）；最终联调启用')
    def test_quick_double_notice_prefixes_each_name_without_changing_profiles(self):
        from stores.models import Store
        self.assertEqual(Store.objects.filter(slug='shenglang').update(provider_name_prefix='SL'), 1)
        second = make_provider(display_name='第二位', deposit_paid=40000, wechat_avatar_legacy_exempt=True)
        account = get_or_create_account_for_legacy_user(second)
        account.openid = f'quick-double-prefix-{account.pk}'
        account.is_openid_bound = True
        account.save(update_fields=['openid', 'is_openid_bound'])
        second.escort_profile.account = account
        second.escort_profile.save(update_fields=['account'])
        self.service.price = 5000
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['price', 'billing_provider_count'])
        names = [self.provider.escort_profile.display_name, second.escort_profile.display_name]
        self.ok(self.recharge())
        data = self.ok(self.client.post('/api/admin/orders/dispatch/', {
            'customer_id': self.accounts[self.boss.pk].pk, 'service_id': self.service.pk,
            'client_request_id': 'quick-double-prefix', 'escort_mode': 'DOUBLE',
            'providers': [{'provider_id': self.accounts[self.provider.pk].pk}, {'provider_id': account.pk}],
        }, format='json'))
        self.login(self.boss)
        messages = self.ok(self.client.get('/api/messages/'))['messages']
        notice = next(item for item in messages if item['related_order_id'] == data['id'] and item['title'] == '已为你指派双陪')
        self.assertTrue(notice['preview'].startswith(f'SL-{names[0]}、SL-{names[1]}'), notice)
        self.provider.escort_profile.refresh_from_db()
        second.escort_profile.refresh_from_db()
        self.assertEqual([self.provider.escort_profile.display_name, second.escort_profile.display_name], names)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '选人通知工作流须本人试音录音/客服指派能力属正式契约深水区（18-盘点 派单/试音待联调）；最终联调启用')
    def test_staff_assignment_notice_reaches_boss_account_when_legacy_id_is_stale(self):
        from stores.models import Store
        self.assertEqual(Store.objects.filter(slug='shenglang').update(provider_name_prefix='SL'), 1)
        original_name = self.provider.escort_profile.display_name
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
        from stores.context import current_store
        from stores.display import provider_display_name
        contexts = []
        def inspect_name(name):
            store = current_store()
            contexts.append((getattr(store, 'slug', None), getattr(store, 'provider_name_prefix', None)))
            return provider_display_name(name)
        with patch('stores.display.provider_display_name', side_effect=inspect_name):
            self.ok(self.client.post(f'/api/admin/orders/{order.pk}/assign/',
                                     {'provider_id': self.accounts[self.provider.pk].pk}, format='json'))
        self.assertEqual(contexts, [('shenglang', 'SL')])
        self.login(self.boss)
        rows = self.ok(self.client.get('/api/messages/'))['messages']
        notice = next(item for item in rows if item['title'] == '已为你指派大神')
        self.assertEqual(notice['related_order_id'], order.pk)
        self.assertTrue(notice['preview'].startswith('SL-' + original_name), notice)
        self.provider.escort_profile.refresh_from_db()
        self.assertEqual(self.provider.escort_profile.display_name, original_name)
        self.assertEqual(self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))['status'], 'GRABBED')
        self.login(old_boss)
        rows = self.ok(self.client.get('/api/messages/'))['messages']
        self.assertFalse(any(item['title'] == '已为你指派大神' for item in rows))

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '选人通知工作流须本人试音录音/客服指派能力属正式契约深水区（18-盘点 派单/试音待联调）；最终联调启用')
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
        self.assertTrue(Message.objects.get(pk=notices[0]['id']).assignment_alert)
        self.assertTrue(self.ok(self.client.get('/api/messages/assignment-alerts/', {'after': 0}))['pending'])
        self.assertEqual(self.ok(self.client.get(f"/api/orders/orders/{order['id']}/"))['status'], 'GRABBED')

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '选人通知工作流须本人试音录音/客服指派能力属正式契约深水区（18-盘点 派单/试音待联调）；最终联调启用')
    def test_selected_provider_cannot_return_order_to_pool_by_rejecting(self):
        replacement = make_provider(display_name='新陪玩', deposit_paid=40000, wechat_avatar_legacy_exempt=True)
        for provider in (self.provider, replacement):
            account = get_or_create_account_for_legacy_user(provider)
            account.openid = f'selection-notice-{account.pk}'
            account.is_openid_bound = True
            account.save(update_fields=['openid', 'is_openid_bound'])
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
        rejected = self.client.post(f'/api/orders/orders/{order_id}/reject/', {'reason': '时间冲突'}, format='json')
        self.assertEqual(rejected.data['errorCode'], 'ORDER_SELF_REJECTION_DISABLED')
        old = self.ok(self.client.get(path))
        self.assertEqual(old['application']['status'], 'SELECTED')
        self.assertEqual(old['order']['status'], 'GRABBED')
        self.assertEqual(old['closed_reason'], '本次报名已选中，请查看订单状态。')
        self.login(self.boss)
        self.assertEqual(self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['status'], 'GRABBED')
        self.login(replacement)
        self.assertNotEqual(self.client.post(f'/api/orders/orders/{order_id}/grab/', {}, format='json').data['code'], 0)
        self.assertFalse(OrderApplication.objects.filter(order_id=order_id, applicant=self.accounts[replacement.pk]).exists())

    @override_settings(REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '选人通知工作流须本人试音录音/客服指派能力属正式契约深水区（18-盘点 派单/试音待联调）；最终联调启用')
    def test_selected_team_detail_tracks_staff_transfer_and_return(self):
        partner, incoming = (make_provider(display_name=name, deposit_paid=40000, wechat_avatar_legacy_exempt=True)
                             for name in ('原队友', '接手陪玩'))
        for provider in (self.provider, partner, incoming):
            account = get_or_create_account_for_legacy_user(provider)
            account.openid = f'selection-notice-{account.pk}'
            account.is_openid_bound = True
            account.save(update_fields=['openid', 'is_openid_bound'])
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
        select = f'/api/orders/orders/{order_id}/applications/{application.pk}/select/'
        self.ok(self.client.post(select, {}, format='json'))
        for provider in (self.provider, partner):
            self.login(provider)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/',
                                     {'entry_image': release_flow.screenshot()}, format='multipart'))
        self.login(self.boss)
        self.ok(self.client.post(select, {}, format='json'))
        for old, new in ((self.provider, incoming), (incoming, self.provider)):
            self.login(self.admin)
            self.ok(self.client.post(f'/api/admin/orders/{order_id}/transfer/', {
                'transfers': [{'old_provider_id': self.accounts[old.pk].pk,
                               'new_provider_id': self.accounts[new.pk].pk,
                               'split_type': 'PERCENT', 'split_value': 40, 'penalty_amount': 0}],
                'reason': '隔离转单报名状态验证',
            }, format='json'))
            current = Order.objects.get(pk=order_id)
            before = (current.status, current.provider_account_id, current.status_logs.count(),
                      current.transactions.count(), Message.objects.filter(related_order_id=order_id).count())
            self.login(self.boss)
            self.assertNotEqual(self.client.post(select, {}, format='json').data['code'], 0)
            current.refresh_from_db()
            self.assertEqual((current.status, current.provider_account_id, current.status_logs.count(),
                              current.transactions.count(), Message.objects.filter(related_order_id=order_id).count()), before)
            for viewer in (self.provider, partner):
                self.login(viewer)
                detail = self.ok(self.client.get(path))
                self.assertEqual(detail['order']['status'], 'IN_SERVICE')
                self.assertEqual(detail['closed_reason'],
                    '本次报名曾被选中，当前服务人员已调整。' if new == incoming
                    else '本次报名已选中，请查看订单状态。')

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '选人通知工作流须本人试音录音/客服指派能力属正式契约深水区（18-盘点 派单/试音待联调）；最终联调启用')
    def test_double_team_invitation_confirmation_and_selection_reach_correct_accounts(self):
        second, outsider = (make_provider(deposit_paid=40000, wechat_avatar_legacy_exempt=True) for _ in range(2))
        for user in (self.provider, second, outsider):
            self.accounts[user.pk] = get_or_create_account_for_legacy_user(user)
            account = self.accounts[user.pk]
            account.openid = f'selection-notice-{account.pk}'
            account.is_openid_bound = True
            account.save(update_fields=['openid', 'is_openid_bound'])
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
        public = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        self.assertFalse(public['applications'])
        self.assertFalse(public['my_applications'])
        self.assertFalse(public['customer']['phone'])
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
            self.assertTrue(Message.objects.get(pk=selected[0]['id']).assignment_alert)
            self.assertTrue(self.ok(self.client.get('/api/messages/assignment-alerts/', {'after': 0}))['pending'])
            detail = self.ok(self.client.get(f"/api/orders/orders/{selected[0]['related_order_id']}/"))
            self.assertEqual(detail['status'], 'GRABBED')
            self.assertIn('START', detail['allowed_actions'])
        self.login(outsider)
        self.assertFalse(Message.objects.filter(recipient_account=self.accounts[outsider.pk], related_order_id=order.pk).exists())
