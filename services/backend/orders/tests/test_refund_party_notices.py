"""Refund notifications for the real three-party transferred team flow."""
from django.test import override_settings
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import Order
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_provider
from site_messages.models import Message
from wallet.models import ProviderReport


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class RefundPartyNoticeTest(APITransactionTestCase):
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    finish = flow.ReleaseFlowTest.finish
    settle_after_window = flow.ReleaseFlowTest.settle_after_window

    def test_distinct_inviter_with_reversed_income_is_told_to_check_wallet(self):
        inviter = make_provider(display_name='推荐人')
        self.accounts[inviter.pk] = get_or_create_account_for_legacy_user(inviter)
        inviter.escort_profile.account = self.accounts[inviter.pk]
        inviter.escort_profile.save(update_fields=['account'])
        self.boss.inviter, self.boss.inviter_commission_rate = inviter, 50
        self.boss.save(update_fields=['inviter', 'inviter_commission_rate'])
        order = self.new_order()
        report = self.finish(order)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                                 {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
        self.settle_after_window()
        preview = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/', {'refund_amount': 2500}))
        self.ok(self.client.post(f'/api/admin/orders/{order.pk}/refund/', {
            'reason': '隔离推荐分佣退款', 'refund_amount': 2500,
            'plan_token': preview['plan_token'], 'client_request_id': 'refund-inviter-notice',
        }, format='json'))
        self.login(inviter)
        notices = self.ok(self.client.get('/api/messages/'))['messages']
        rows = [row for row in notices if row['title'] == '订单部分退款']
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]['related_order_id'])
        self.assertIn('推荐收益变化请查看钱包流水', rows[0]['preview'])
        self.assertEqual(rows[0]['action_url'], '/pages/wallet/index')
        detail = self.ok(self.client.get(f"/api/messages/{rows[0]['id']}/"))
        self.assertTrue(detail['is_read'])
        self.assertEqual(detail['action_url'], '/pages/wallet/index')
        transactions = self.ok(self.client.get('/api/wallet/transactions/'))['transactions']
        self.assertTrue(any(row['is_reversal'] and row['amount'] < 0 for row in transactions))

    def test_transferred_team_members_receive_committed_partial_refund_once(self):
        second = make_provider(display_name='留守陪玩')
        incoming = make_provider(display_name='转入陪玩')
        outsider = make_provider(display_name='无关陪玩')
        for provider in (second, incoming, outsider):
            account = self.accounts[provider.pk] = get_or_create_account_for_legacy_user(provider)
            provider.escort_profile.account = account
            provider.escort_profile.save(update_fields=['account'])
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        self.ok(self.recharge())
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk,
            'provider_ids': [self.accounts[self.provider.pk].pk, self.accounts[second.pk].pk],
            'support_contact_id': self.card.pk, 'client_request_id': 'refund-notice-team-order',
        }, format='json'))['id']
        for provider in (self.provider, second):
            self.login(provider)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/',
                                     {'entry_image': flow.screenshot()}, format='multipart'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/orders/{order_id}/transfer/', {
            'transfers': [{'old_provider_id': self.accounts[self.provider.pk].pk,
                           'new_provider_id': self.accounts[incoming.pk].pk,
                           'split_type': 'PERCENT', 'split_value': 40, 'penalty_amount': 0}],
            'reason': '隔离转单退款通知验证',
        }, format='json'))
        self.login(self.provider)
        report = self.ok(self.client.post('/api/wallet/reports/', {'order_id': order_id}, format='json'))
        self.ok(self.client.post(f"/api/wallet/reports/{report['id']}/images/",
                                 {'kind': 'COMPLETION', 'image': flow.screenshot()}, format='multipart'))
        for provider in (second, incoming):
            self.login(provider)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/',
                                     {'completion_image': flow.screenshot()}, format='multipart'))
        for provider in (self.provider, second, incoming):
            self.login(provider)
            report = self.ok(self.client.post('/api/wallet/reports/', {'order_id': order_id}, format='json'))
            if provider == incoming:
                self.ok(self.client.post(f"/api/wallet/reports/{report['id']}/images/",
                                         {'kind': 'ENTRY', 'image': flow.screenshot()}, format='multipart'))
            self.ok(self.client.post(f"/api/wallet/reports/{report['id']}/images/",
                                     {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
            self.ok(self.client.post(f"/api/wallet/reports/{report['id']}/submit/"))
        self.login(self.admin)
        for report in ProviderReport.objects.filter(order_id=order_id):
            self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
        self.settle_after_window()
        self.assertIsNotNone(Order.objects.get(pk=order_id).settled_at)
        preview = self.ok(self.client.get(f'/api/admin/orders/{order_id}/refund-preview/', {'refund_amount': 2500}))
        request = {'reason': '隔离部分退款', 'refund_amount': 2500,
                   'plan_token': preview['plan_token'], 'client_request_id': 'refund-notice-team-partial'}
        self.ok(self.client.post(f'/api/admin/orders/{order_id}/refund/', request, format='json'))
        notices = Message.objects.filter(related_order_id=order_id, title='订单部分退款')
        expected = {self.accounts[user.pk].pk for user in (self.boss, self.provider, second, incoming)}
        self.assertEqual(set(notices.values_list('recipient_account_id', flat=True)), expected)
        self.assertEqual(notices.count(), 4)
        self.assertFalse(notices.filter(recipient_account=self.accounts[outsider.pk]).exists())
        self.ok(self.client.post(f'/api/admin/orders/{order_id}/refund/', request, format='json'))
        self.assertEqual(notices.count(), 4)
        for user in (self.boss, self.provider, second, incoming):
            self.login(user)
            self.assertEqual(self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['payment_status'],
                             'PARTIALLY_REFUNDED')
            rows = self.ok(self.client.get('/api/messages/'))['messages']
            self.assertEqual(len([row for row in rows if row['related_order_id'] == order_id
                                  and row['title'] == '订单部分退款']), 1)
        self.login(self.admin)
        preview = self.ok(self.client.get(f'/api/admin/orders/{order_id}/refund-preview/'))
        final_request = {'reason': '隔离剩余款项退款', 'plan_token': preview['plan_token'],
                         'client_request_id': 'refund-notice-team-final'}
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/refund/', final_request, format='json'))
        final_notices = Message.objects.filter(related_order_id=order_id, title='订单已退款')
        self.assertEqual(set(final_notices.values_list('recipient_account_id', flat=True)), expected)
        self.assertEqual(final_notices.count(), 4)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/refund/', final_request, format='json'))
        self.assertEqual(final_notices.count(), 4)
