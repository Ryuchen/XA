"""A real completed service, refund and requalification refresh the queue."""
from datetime import timedelta
from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from engagement.confirmation import process_order
from engagement.models import RewardClaim, RewardItem, VipTier
from orders.models import Order
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_boss_type


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
    CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class VipClaimQueueRealtimeTest(APITransactionTestCase):
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    finish = flow.ReleaseFlowTest.finish
    settle_after_window = flow.ReleaseFlowTest.settle_after_window

    def test_refund_and_new_service_refresh_existing_vip_entitlement(self):
        reward = RewardItem.objects.create(code='vip-queue-live', name='会员交付权益', kind='SERVICE', in_shop=False)
        VipTier.objects.create(level=1, boss_type=make_boss_type(), recharge_threshold=1000,
                               upgrade_rewards=[{'reward': reward.pk, 'quantity': 1}])
        role = self.admin.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'engagement:fulfill', 'dashboard:view']
        role.save(update_fields=['permissions'])
        order = self.new_order()
        report = self.finish(order)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
        self.settle_after_window()  # Advance only the business clock; settlement code remains real.
        claim = RewardClaim.objects.get(account=self.accounts[self.boss.pk], reward=reward)
        self.assertEqual(claim.status, 'PENDING')
        token = issue_account_tokens(self.accounts[self.admin.pk])[0]

        def refund():
            self.login(self.admin)
            preview = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/'))
            self.ok(self.client.post(f'/api/admin/orders/{order.pk}/refund/', {'reason': '隔离会员退款',
                'plan_token': preview['plan_token'], 'client_request_id': 'vip-queue-refund'}, format='json'))
            claim.refresh_from_db()
            return claim.status

        def requalify():
            self.login(self.boss)
            result = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk,
                'provider_id': self.accounts[self.provider.pk].pk, 'support_contact_id': self.card.pk,
                'client_request_id': 'vip-queue-second-service'}, format='json'))
            second = Order.objects.get(pk=result['id'])
            report2 = self.finish(second)
            self.ok(self.client.post(f'/api/wallet/reports/{report2.pk}/images/', {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
            self.ok(self.client.post(f'/api/wallet/reports/{report2.pk}/submit/'))
            self.login(self.admin)
            self.ok(self.client.post(f'/api/admin/reports/{report2.pk}/approve/'))
            with patch('django.utils.timezone.now', return_value=timezone.now()+timedelta(hours=73)):
                process_order(second.pk)
            claim.refresh_from_db()
            return claim.status

        async def verify():
            socket = WebsocketCommunicator(application, f'/ws/orders/?token={token}')
            self.assertTrue((await socket.connect())[0])
            async def next_claim():
                while True:
                    event = await socket.receive_json_from(timeout=3)
                    if event['type'] == 'claim_queue_update':
                        return event
            try:
                self.assertEqual(await database_sync_to_async(refund)(), 'CANCELLED')
                self.assertEqual((await next_claim())['data'], {})
                self.assertEqual(await database_sync_to_async(requalify)(), 'PENDING')
                self.assertEqual((await next_claim())['data'], {})
            finally:
                await socket.disconnect()
        async_to_sync(verify)()
        self.login(self.boss)
        messages = self.ok(self.client.get('/api/messages/'))['messages']
        changed = [item for item in messages if item['title'] == '权益状态已调整']
        restored = [item for item in messages if item['title'] == '权益已恢复']
        self.assertEqual((len(changed), len(restored)), (1, 1))
        self.assertIn('1 项未交付权益已撤销', changed[0]['preview'])
        self.assertIn('1 项未交付权益已恢复', restored[0]['preview'])
        for notice in changed + restored:
            self.assertEqual(self.ok(self.client.get(f"/api/messages/{notice['id']}/"))['action_url'], '/pages/points/index?tab=claims')

    def test_upgrade_updates_claim_terms_and_requires_operator_recheck(self):
        reward = RewardItem.objects.create(code='vip-naming-terms', name='会员冠名', kind='NAMING', in_shop=False)
        VipTier.objects.create(level=1, boss_type=make_boss_type(), recharge_threshold=1000,
                               upgrade_rewards=[{'reward': reward.pk, 'quantity': 3}])
        VipTier.objects.create(level=4, boss_type=make_boss_type(), recharge_threshold=15000,
                               upgrade_rewards=[{'reward': reward.pk, 'quantity': 6}])
        role = self.admin.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'engagement:fulfill']
        role.save(update_fields=['permissions'])
        order = self.new_order()
        report = self.finish(order)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
        self.settle_after_window()
        claim = RewardClaim.objects.filter(account=self.accounts[self.boss.pk], reward=reward).order_by('id').first()
        self.assertEqual((claim.duration_days, claim.duration_months), (7, 0))
        self.login(self.admin)
        old = self.ok(self.client.get(f'/api/admin/club-claims/{claim.pk}/'))
        token = issue_account_tokens(self.accounts[self.admin.pk])[0]

        def upgrade():
            self.login(self.boss)
            result = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk,
                'provider_id': self.accounts[self.provider.pk].pk, 'support_contact_id': self.card.pk,
                'client_request_id': 'vip-terms-second-service'}, format='json'))
            second = Order.objects.get(pk=result['id'])
            report2 = self.finish(second)
            self.ok(self.client.post(f'/api/wallet/reports/{report2.pk}/images/', {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
            self.ok(self.client.post(f'/api/wallet/reports/{report2.pk}/submit/'))
            self.login(self.admin)
            self.ok(self.client.post(f'/api/admin/reports/{report2.pk}/approve/'))
            with patch('django.utils.timezone.now', return_value=timezone.now()+timedelta(hours=73)):
                process_order(second.pk)
            claim.refresh_from_db()
            return claim.duration_days, claim.duration_months

        async def verify():
            socket = WebsocketCommunicator(application, f'/ws/orders/?token={token}')
            self.assertTrue((await socket.connect())[0])
            try:
                self.assertEqual(await database_sync_to_async(upgrade)(), (0, 1))
                events = 0
                while events < 4:
                    event = await socket.receive_json_from(timeout=3)
                    if event['type'] == 'claim_queue_update':
                        events += 1
            finally:
                await socket.disconnect()
        async_to_sync(verify)()
        self.login(self.admin)
        payload = {'status': 'COMPLETED', 'note': '已核对冠名', 'expected_status': old['status'],
            'expected_details': old['details'], 'expected_terms': {'duration_days': old['duration_days'],
                'duration_months': old['duration_months'], 'highlighted': old['highlighted']}}
        conflict = self.client.post(f'/api/admin/club-claims/{claim.pk}/process/', payload, format='json')
        self.assertEqual((conflict.status_code, conflict.data['errorCode']), (409, 'CLAIM_TERMS_CHANGED'))
        self.login(self.boss)
        latest = self.ok(self.client.get(f'/api/club/claims/{claim.pk}/'))
        self.assertEqual((latest['status'], latest['duration_days'], latest['duration_months']), ('PENDING', 0, 1))
        payload['expected_terms'] = {'duration_days': 0, 'duration_months': 1, 'highlighted': latest['highlighted']}
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/club-claims/{claim.pk}/process/', payload, format='json'))
