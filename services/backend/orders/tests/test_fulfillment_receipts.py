"""Fulfillment receipts replay only the same account, action and evidence."""
import asyncio
import io
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.test import override_settings
from PIL import Image
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.tokens import issue_account_tokens
from orders.tests import test_release_flow as flow
from orders.tests import test_team_invitation_detail as invitation_flow
from site_messages.models import Message
from wallet.models import IdempotencyKey, ProviderReport


def picture(color='white'):
    data = io.BytesIO()
    Image.new('RGB', (4, 4), color).save(data, format='PNG')
    return SimpleUploadedFile('evidence.png', data.getvalue(), content_type='image/png')


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_RECHARGE_PROOF=True, REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class FulfillmentReceiptTest(APITransactionTestCase):
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    invite = invitation_flow.TeamInvitationDetailTest.invite

    def observe(self):
        layer = get_channel_layer()
        channel = async_to_sync(layer.new_channel)()
        async_to_sync(layer.group_add)(f'account_{self.accounts[self.boss.pk].pk}', channel)
        async def drain():
            events = []
            while True:
                try:
                    events.append(await asyncio.wait_for(layer.receive(channel), timeout=.1))
                except asyncio.TimeoutError:
                    return events
        return lambda: async_to_sync(drain)()

    def post_evidence(self, order_id, action, key=None, color='white', invalid=False):
        image = SimpleUploadedFile('bad.png', b'not an image', content_type='image/png') if invalid else picture(color)
        payload = {'entry_image' if action == 'start' else 'completion_image': image}
        if key is not None:
            payload['client_request_id'] = key
        return self.client.post(f'/api/orders/orders/{order_id}/{action}/', payload, format='multipart')

    def test_single_replay_conflicts_notifications_and_legacy(self):
        order = self.new_order()
        self.login(self.provider)
        drain = self.observe()
        invalid = self.post_evidence(order.pk, 'start', 'start-one', invalid=True)
        self.assertNotEqual(invalid.data['code'], 0)
        self.assertFalse(IdempotencyKey.objects.filter(account=self.accounts[self.provider.pk], request_id='start-one').exists())
        self.ok(self.post_evidence(order.pk, 'start', 'start-one'))
        self.ok(self.post_evidence(order.pk, 'start', 'start-one'))
        self.assertEqual(len([event for event in drain() if event['type'] == 'order_status_update']), 1)
        self.assertEqual(order.status_logs.filter(action='START').count(), 1)
        report = ProviderReport.objects.get(order=order, provider=self.provider)
        original = report.entry_image.name
        self.assertEqual(self.post_evidence(order.pk, 'start', 'start-one', color='red').status_code, 409)
        self.assertEqual(self.post_evidence(order.pk, 'complete', 'start-one').status_code, 409)
        self.assertNotEqual(self.post_evidence(order.pk, 'start', 'start-again').data['code'], 0)
        self.assertNotEqual(self.post_evidence(order.pk, 'start').data['code'], 0)
        report.refresh_from_db()
        self.assertEqual(report.entry_image.name, original)
        self.ok(self.post_evidence(order.pk, 'complete', 'complete-one'))
        self.ok(self.post_evidence(order.pk, 'complete', 'complete-one'))
        self.assertEqual(len([event for event in drain() if event['type'] == 'order_status_update']), 1)
        self.assertEqual(order.status_logs.filter(action='COMPLETE').count(), 1)
        self.assertEqual(Message.objects.filter(recipient_account=self.accounts[self.boss.pk], related_order_id=order.pk, title='订单已完成').count(), 1)
        self.assertEqual(self.post_evidence(order.pk, 'complete', 'complete-one', color='red').status_code, 409)
        self.assertNotEqual(self.post_evidence(order.pk, 'complete', 'complete-again').data['code'], 0)

    def test_both_team_members_replay_before_and_after_stage_changes(self):
        self.invite()
        self.login(self.partner)
        self.ok(self.client.post(self.action, {'action': 'CONFIRM_TEAM'}, format='json'))
        self.login(self.boss)
        self.ok(self.client.post(f'/api/orders/orders/{self.order.pk}/applications/{self.application.pk}/select/', {}, format='json'))
        drain = self.observe()
        for index, user in enumerate((self.provider, self.partner)):
            self.login(user)
            key = f'team-start-{index}'
            self.ok(self.post_evidence(self.order.pk, 'start', key))
            self.ok(self.post_evidence(self.order.pk, 'start', key))
        self.assertEqual(len([event for event in drain() if event['type'] == 'order_status_update']), 2)
        self.assertEqual(self.order.status_logs.filter(action='START').count(), 1)
        for index, user in enumerate((self.provider, self.partner)):
            self.login(user)
            key = f'team-complete-{index}'
            self.ok(self.post_evidence(self.order.pk, 'complete', key))
            self.ok(self.post_evidence(self.order.pk, 'complete', key))
        self.assertEqual(len([event for event in drain() if event['type'] == 'order_status_update']), 2)
        self.assertEqual(self.order.status_logs.filter(action='COMPLETE').count(), 1)
        self.assertEqual(Message.objects.filter(recipient_account=self.accounts[self.boss.pk], related_order_id=self.order.pk, title='订单已完成').count(), 1)
        self.login(self.provider)
        self.ok(self.post_evidence(self.order.pk, 'start', 'team-start-0'))
        self.assertEqual(self.post_evidence(self.order.pk, 'complete', 'team-complete-0', color='red').status_code, 409)
        self.assertEqual(len([event for event in drain() if event['type'] == 'order_status_update']), 0)

    def test_concurrent_completion_replays_once(self):
        if connection.vendor != 'mysql':
            self.skipTest('requires isolated MySQL row locks')
        order = self.new_order()
        self.login(self.provider)
        self.ok(self.post_evidence(order.pk, 'start'))
        drain = self.observe()
        token = issue_account_tokens(self.accounts[self.provider.pk])[0]
        barrier = Barrier(2)
        def send():
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                barrier.wait(timeout=10)
                response = client.post(f'/api/orders/orders/{order.pk}/complete/',
                    {'completion_image': picture(), 'client_request_id': 'concurrent-completion'}, format='multipart')
                return response.status_code, response.data['code']
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: send(), range(2)))
        self.assertEqual(results, [(200, 0), (200, 0)])
        self.assertEqual(order.status_logs.filter(action='COMPLETE').count(), 1)
        self.assertEqual(Message.objects.filter(recipient_account=self.accounts[self.boss.pk], related_order_id=order.pk, title='订单已完成').count(), 1)
        self.assertEqual(len([event for event in drain() if event['type'] == 'order_status_update']), 1)
