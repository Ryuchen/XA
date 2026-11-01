"""An actual staff handoff is delivered consistently to all five roles."""
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import override_settings
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from orders.models import OrderProvider
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_provider


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class TransferSocketWorkflowTest(APITransactionTestCase):
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge

    def test_legacy_transferred_provider_can_view_own_retained_share(self):
        teammate = make_provider(display_name='留队陪玩')
        incoming = make_provider(display_name='转入陪玩')
        for user in (teammate, incoming):
            self.accounts[user.pk] = get_or_create_account_for_legacy_user(user)
            user.escort_profile.account = self.accounts[user.pk]
            user.escort_profile.save(update_fields=['account'])
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        self.ok(self.recharge())
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk,
            'provider_ids': [self.accounts[self.provider.pk].pk, self.accounts[teammate.pk].pk],
            'support_contact_id': self.card.pk, 'client_request_id': 'legacy-transferred-share',
        }, format='json'))['id']
        for user in (self.provider, teammate):
            self.login(user)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/',
                                     {'entry_image': flow.screenshot()}, format='multipart'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/orders/{order_id}/transfer/', {
            'transfers': [{'old_provider_id': self.accounts[self.provider.pk].pk,
                           'new_provider_id': self.accounts[incoming.pk].pk,
                           'split_type': 'PERCENT', 'split_value': 40, 'penalty_amount': 0}],
            'reason': '历史份额验收',
        }, format='json'))
        old_share = OrderProvider.objects.get(order_id=order_id, provider=self.provider)
        retained = old_share.provider_income
        # Reproduce a historic share predating the business-account backfill.
        old_share.provider_account = None
        old_share.save(update_fields=['provider_account'])
        self.login(self.provider)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))
        self.assertIs(detail['my_assignment_released'], True)
        self.assertEqual(detail['expected_income'], retained)
        rows = self.ok(self.client.get('/api/orders/orders/', {'status': 'in_progress'}))
        row = next(row for row in rows if row['id'] == order_id)
        self.assertIs(row['my_assignment_released'], True)
        self.assertEqual(row['expected_income'], retained)
        other = make_provider(display_name='不相关陪玩')
        other_account = get_or_create_account_for_legacy_user(other)
        # A stored business account takes precedence over the old user ID.
        old_share.provider_account = other_account
        old_share.save(update_fields=['provider_account'])
        self.assertEqual(self.client.get(f'/api/orders/orders/{order_id}/').status_code, 404)
        rows = self.ok(self.client.get('/api/orders/orders/', {'status': 'in_progress'}))
        self.assertFalse(any(row['id'] == order_id for row in rows))

    def test_staff_assignment_reaches_both_boss_devices_and_assigned_team(self):
        teammate = make_provider(display_name='双陪队友')
        outsider = make_provider(display_name='无关陪玩')
        for user in (teammate, outsider):
            account = get_or_create_account_for_legacy_user(user)
            self.accounts[user.pk] = account
            user.escort_profile.account = account
            user.escort_profile.save(update_fields=['account'])
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        self.ok(self.recharge())
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'dispatch_route': 'STAFF',
            'support_contact_id': self.card.pk, 'client_request_id': 'socket-staff-assignment',
        }, format='json'))['id']
        users = (self.boss, self.boss, self.provider, teammate, self.admin, outsider)
        tokens = [issue_account_tokens(self.accounts[user.pk])[0] for user in users]
        staff_client = APIClient()
        staff_client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[-2])

        async def verify():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={token}') for token in tokens]
            try:
                for socket in sockets:
                    self.assertTrue((await socket.connect())[0])
                response = await database_sync_to_async(staff_client.post)(
                    f'/api/admin/orders/{order_id}/assign/',
                    {'provider_ids': [self.accounts[self.provider.pk].pk, self.accounts[teammate.pk].pk]},
                    format='json')
                self.ok(response)
                for socket in sockets[:-1]:
                    for _ in range(8):
                        event = await socket.receive_json_from(timeout=3)
                        if event['type'] == 'order_status_update' and event['data'].get('id') == order_id:
                            break
                    else:
                        self.fail('未收到客服派单状态')
                    self.assertEqual(event['data']['status'], 'GRABBED')
                    self.assertEqual({row['id'] for row in event['data']['participants']},
                                     {self.accounts[self.provider.pk].pk, self.accounts[teammate.pk].pk})
                self.assertTrue(await sockets[-1].receive_nothing(timeout=0.3))
            finally:
                for socket in sockets:
                    await socket.disconnect()
        async_to_sync(verify)()

    def test_staff_handoff_updates_boss_both_devices_and_all_providers(self):
        teammate = make_provider(display_name='留队陪玩')
        incoming = make_provider(display_name='转入陪玩')
        for user in (teammate, incoming):
            account = get_or_create_account_for_legacy_user(user)
            self.accounts[user.pk] = account
            user.escort_profile.account = account
            user.escort_profile.save(update_fields=['account'])
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        self.ok(self.recharge())
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk,
            'provider_ids': [self.accounts[self.provider.pk].pk, self.accounts[teammate.pk].pk],
            'support_contact_id': self.card.pk, 'client_request_id': 'socket-team-handoff',
        }, format='json'))['id']
        for provider in (self.provider, teammate):
            self.login(provider)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/',
                                     {'entry_image': flow.screenshot()}, format='multipart'))

        users = (self.boss, self.boss, self.provider, teammate, incoming, self.admin)
        tokens = [issue_account_tokens(self.accounts[user.pk])[0] for user in users]
        staff_client = APIClient()
        staff_client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[-1])

        async def verify():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={token}') for token in tokens]
            try:
                for socket in sockets:
                    self.assertTrue((await socket.connect())[0])
                response = await database_sync_to_async(staff_client.post)(
                    f'/api/admin/orders/{order_id}/transfer/', {
                        'transfers': [{'old_provider_id': self.accounts[self.provider.pk].pk,
                                       'new_provider_id': self.accounts[incoming.pk].pk,
                                       'split_type': 'PERCENT', 'split_value': 40, 'penalty_amount': 0}],
                        'reason': '多角色实时转单验收',
                    }, format='json')
                self.ok(response)
                for user, socket in zip(users, sockets):
                    for _ in range(8):
                        event = await socket.receive_json_from(timeout=3)
                        if event['type'] == 'order_status_update' and event['data'].get('id') == order_id:
                            break
                    else:
                        self.fail(f'{user.pk} 未收到订单变化')
                    data = event['data']
                    self.assertEqual(data['status'], 'IN_SERVICE')
                    self.assertEqual({row['id'] for row in data['participants']},
                                     {self.accounts[teammate.pk].pk, self.accounts[incoming.pk].pk})
                    if user in (self.provider, teammate, incoming):
                        self.assertEqual(data['my_assignment_released'], user == self.provider)
            finally:
                for socket in sockets:
                    await socket.disconnect()
        async_to_sync(verify)()
