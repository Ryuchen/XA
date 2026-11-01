"""Online staff gain and lose live dashboard subscriptions as roles change."""
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from orders.tests.factories import make_console_user, make_provider
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class DashboardSubscriptionWorkflowTest(TransactionTestCase):
    def test_online_permission_grant_refreshes_withdrawal_queue_without_reconnect(self):
        provider = make_provider()
        owner = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = owner
        provider.escort_profile.save(update_fields=['account'])
        wallet = get_wallet(user=provider, account=owner)
        wallet.balance = 60000
        wallet.save(update_fields=['balance'])
        staff = make_console_user(['dashboard:view'])
        account = get_or_create_account_for_legacy_user(staff)
        membership = staff.admin_membership
        membership.account = account
        membership.save(update_fields=['account'])
        role = membership.roles.get()
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(owner)[0])
        staff_client = APIClient()
        staff_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        bound = client.put('/api/wallet/withdraw-accounts/WECHAT/', {
            'payee_account': 'isolated-account', 'payee_name': '本地样例', 'expected_id': 0, 'expected_version': 0,
        }, format='json')
        self.assertEqual(bound.data['code'], 0, bound.data)
        payee = bound.data['data']
        token = issue_account_tokens(account)[0]

        async def verify():
            socket = WebsocketCommunicator(application, f'/ws/orders/?token={token}')
            self.assertTrue((await socket.connect())[0])
            try:
                role.permissions = ['dashboard:view', 'withdraw:view']
                await database_sync_to_async(role.save)(update_fields=['permissions'])
                await socket.send_json_to({'type': 'ping'})
                self.assertEqual(await socket.receive_json_from(), {'type': 'pong'})
                response = await database_sync_to_async(client.post)('/api/wallet/withdraw/', {
                    'amount': 20000, 'withdrawal_account_id': payee['id'],
                    'expected_payee_version': payee['version'], 'client_request_id': 'subscription-grant',
                }, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                event = await socket.receive_json_from(timeout=2)
                self.assertEqual(event['type'], 'withdraw_status_update')
                self.assertEqual(event['data']['withdraw_id'], response.data['data']['request']['id'])
                snapshot = await database_sync_to_async(staff_client.get)('/api/admin/dashboard/stats')
                self.assertEqual(snapshot.data['data']['pending_withdraw_total'], 1)
                role.permissions = ['dashboard:view']
                await database_sync_to_async(role.save)(update_fields=['permissions'])
                await socket.send_json_to({'type': 'ping'})
                self.assertEqual(await socket.receive_json_from(), {'type': 'pong'})
                response = await database_sync_to_async(client.post)('/api/wallet/withdraw/', {
                    'amount': 20000, 'withdrawal_account_id': payee['id'],
                    'expected_payee_version': payee['version'], 'client_request_id': 'subscription-revoke',
                }, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                self.assertTrue(await socket.receive_nothing(timeout=0.2))
                snapshot = await database_sync_to_async(staff_client.get)('/api/admin/dashboard/stats')
                self.assertIsNone(snapshot.data['data']['pending_withdraw_total'])
            finally:
                await socket.disconnect()
            role.permissions = ['dashboard:view', 'withdraw:view']
            await database_sync_to_async(role.save)(update_fields=['permissions'])
            reconnected = WebsocketCommunicator(application, f'/ws/orders/?token={token}')
            self.assertTrue((await reconnected.connect())[0])
            try:
                snapshot = await database_sync_to_async(staff_client.get)('/api/admin/dashboard/stats')
                self.assertEqual(snapshot.data['data']['pending_withdraw_total'], 2)
                response = await database_sync_to_async(client.post)('/api/wallet/withdraw/', {
                    'amount': 20000, 'withdrawal_account_id': payee['id'],
                    'expected_payee_version': payee['version'], 'client_request_id': 'subscription-reconnect',
                }, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                event = await reconnected.receive_json_from(timeout=2)
                self.assertEqual(event['type'], 'withdraw_status_update')
                self.assertEqual(event['data']['withdraw_id'], response.data['data']['request']['id'])
                snapshot = await database_sync_to_async(staff_client.get)('/api/admin/dashboard/stats')
                self.assertEqual(snapshot.data['data']['pending_withdraw_total'], 3)
            finally:
                await reconnected.disconnect()
        async_to_sync(verify)()
