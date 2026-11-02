"""A two-person service exposes its actual evidence handoff to all three roles."""
from django.test import override_settings
from rest_framework.test import APITransactionTestCase
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from orders.models import Order
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_provider


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class TeamProgressWorkflowTest(APITransactionTestCase):
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge

    def test_staff_dispatch_handoff_and_reports_use_the_same_progress(self):
        second = make_provider(display_name='客服指派队友')
        self.accounts[second.pk] = get_or_create_account_for_legacy_user(second)
        second.escort_profile.account = self.accounts[second.pk]
        second.escort_profile.save(update_fields=['account'])
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        self.ok(self.recharge())
        self.login(self.boss)
        order = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'dispatch_route': 'STAFF',
            'client_request_id': 'staff-progress-three-roles',
        }, format='json'))
        order_id = order['id']
        self.login(self.admin)
        tokens = [issue_account_tokens(self.accounts[user.pk])[0] for user in (self.provider, second, self.boss)]
        async def assign_and_receive():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={token}') for token in tokens]
            try:
                for socket in sockets:
                    self.assertTrue((await socket.connect())[0])
                response = await database_sync_to_async(self.client.post)(f'/api/admin/orders/{order_id}/assign/', {
                    'provider_ids': [self.accounts[self.provider.pk].pk, self.accounts[second.pk].pk],
                }, format='json')
                self.ok(response)
                for index, socket in enumerate(sockets):
                    for _ in range(5):
                        event = await socket.receive_json_from(timeout=2)
                        if event['type'] == 'site_message_update':
                            if index < 2:
                                self.assertGreater(event['data']['assignment_message_id'], 0)
                            else:
                                self.assertEqual(event['data'], {})
                            break
                    else:
                        self.fail('assignment message event missing')
            finally:
                for socket in sockets:
                    await socket.disconnect()
        async_to_sync(assign_and_receive)()

        def progress_for(user):
            self.login(user)
            prefix = '/api/admin/orders/' if user == self.admin else '/api/orders/orders/'
            return self.ok(self.client.get(f'{prefix}{order_id}/'))

        def check(stage, count):
            for user in (self.boss, self.admin, self.provider, second):
                data = progress_for(user)
                self.assertEqual((data['team_progress']['stage'], data['team_progress']['submitted_count']),
                                 (stage, count))
                self.assertEqual(data['team_progress']['total_count'], 2)

        check('ENTRY', 0)
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/',
                                 {'entry_image': flow.screenshot()}, format='multipart'))
        check('ENTRY', 1)
        self.login(second)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/',
                                 {'entry_image': flow.screenshot()}, format='multipart'))
        check('COMPLETION', 0)
        self.login(second)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/',
                                 {'completion_image': flow.screenshot()}, format='multipart'))
        check('COMPLETION', 1)
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/',
                                 {'completion_image': flow.screenshot()}, format='multipart'))
        for user in (self.boss, self.admin, self.provider, second):
            data = progress_for(user)
            self.assertEqual(data['status'], 'COMPLETED')
            self.assertIsNone(data['team_progress'])
        from wallet.models import ProviderReport, Transaction
        for user in (self.provider, second):
            self.login(user)
            report = ProviderReport.objects.get(order_id=order_id, provider=user)
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                                     {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/'))
            self.login(self.admin)
            self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
        self.assertEqual(ProviderReport.objects.filter(order_id=order_id, status='APPROVED').count(), 2)
        self.assertIsNone(Order.objects.get(pk=order_id).settled_at)
        self.assertFalse(Transaction.objects.filter(order_id=order_id, tx_type='INCOME').exists())

    def test_actual_two_person_entry_and_completion_are_visible_to_each_role(self):
        second = make_provider(display_name='第二陪玩')
        self.accounts[second.pk] = get_or_create_account_for_legacy_user(second)
        profile = second.escort_profile
        profile.account = self.accounts[second.pk]
        profile.save(update_fields=['account'])
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        self.ok(self.recharge())
        self.login(self.boss)
        order_data = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk,
            'provider_ids': [self.accounts[self.provider.pk].pk, self.accounts[second.pk].pk],
            'support_contact_id': self.card.pk, 'client_request_id': 'team-progress-order',
        }, format='json'))
        order = Order.objects.get(pk=order_data['id'])
        expected_ids = [self.accounts[self.provider.pk].pk, self.accounts[second.pk].pk]

        def check(user, stage, flags):
            self.login(user)
            path = f'/api/admin/orders/{order.pk}/' if user == self.admin else f'/api/orders/orders/{order.pk}/'
            data = self.ok(self.client.get(path))
            progress = data['team_progress']
            self.assertEqual(progress['stage'], stage)
            self.assertEqual(progress['submitted_count'], sum(flags))
            self.assertEqual(progress['total_count'], 2)
            self.assertEqual([member['id'] for member in progress['members']], expected_ids)
            self.assertEqual([member['submitted'] for member in progress['members']], flags)
            self.assertTrue(all(set(member) == {'id', 'nickname', 'submitted'} for member in progress['members']))
            return data

        check(self.boss, 'ENTRY', [False, False])
        self.login(self.provider)
        tokens = [issue_account_tokens(self.accounts[user.pk])[0] for user in (self.boss, second)]
        async def start_and_receive():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={token}') for token in tokens]
            try:
                for socket in sockets:
                    self.assertTrue((await socket.connect())[0])
                response = await database_sync_to_async(self.client.post)(
                    f'/api/orders/orders/{order.pk}/start/', {'entry_image': flow.screenshot()}, format='multipart')
                self.ok(response)
                for socket in sockets:
                    event = await socket.receive_json_from()
                    self.assertEqual(event['type'], 'order_status_update')
                    self.assertEqual(event['data']['team_progress']['submitted_count'], 1)
            finally:
                for socket in sockets:
                    await socket.disconnect()
        async_to_sync(start_and_receive)()
        first = check(self.provider, 'ENTRY', [True, False])
        self.assertNotIn('START', first['allowed_actions'])
        second_view = check(second, 'ENTRY', [True, False])
        self.assertIn('START', second_view['allowed_actions'])
        check(self.boss, 'ENTRY', [True, False])
        check(self.admin, 'ENTRY', [True, False])
        self.login(self.provider)
        rows = self.ok(self.client.get('/api/orders/orders/', {'status': 'grabbed'}))
        self.assertEqual(next(row for row in rows if row['id'] == order.pk)['team_progress']['submitted_count'], 1)
        self.login(self.admin)
        rows = self.ok(self.client.get('/api/admin/orders/'))['list']
        self.assertEqual(next(row for row in rows if row['id'] == order.pk)['team_progress']['submitted_count'], 1)
        self.login(second)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': flow.screenshot()}, format='multipart'))
        check(self.boss, 'COMPLETION', [False, False])
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': flow.screenshot()}, format='multipart'))
        check(self.admin, 'COMPLETION', [True, False])
        second_view = check(second, 'COMPLETION', [True, False])
        self.assertIn('COMPLETE', second_view['allowed_actions'])
        self.login(self.boss)
        inbox = self.ok(self.client.get('/api/messages/'))['messages']
        self.assertFalse(any(row['title'] == '订单已完成' and row['related_order_id'] == order.pk for row in inbox))
        self.login(second)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': flow.screenshot()}, format='multipart'))
        self.login(self.boss)
        finished = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        self.assertEqual(finished['status'], 'COMPLETED')
        self.assertIsNone(finished['team_progress'])
        inbox = self.ok(self.client.get('/api/messages/'))['messages']
        notices = [row for row in inbox if row['title'] == '订单已完成' and row['related_order_id'] == order.pk]
        self.assertEqual(len(notices), 1)
        self.assertIn(self.service.name, notices[0]['preview'])
        self.login(second)
        repeated = self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': flow.screenshot()}, format='multipart')
        self.assertNotEqual(repeated.data['code'], 0)
        self.login(self.boss)
        inbox = self.ok(self.client.get('/api/messages/'))['messages']
        self.assertEqual(sum(row['title'] == '订单已完成' and row['related_order_id'] == order.pk for row in inbox), 1)
