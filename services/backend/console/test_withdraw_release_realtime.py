"""Withdrawal release reaches online reviewers and can be recovered on reconnect."""
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import override_settings
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from orders.tests.factories import make_console_user, make_provider
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class WithdrawReleaseRealtimeTest(APITransactionTestCase):
    def test_release_reclaim_reaches_reviewers_and_reconnect_reads_current_owner(self):
        provider = make_provider()
        owner = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = owner
        provider.escort_profile.save(update_fields=['account'])
        wallet = get_wallet(user=provider, account=owner)
        wallet.balance = 50000
        wallet.save(update_fields=['balance'])
        roles = [make_console_user(perms) for perms in (
            ['withdraw:view', 'withdraw:audit'], ['withdraw:view', 'withdraw:audit'],
            ['withdraw:view', 'withdraw:release'], ['dashboard:view'],
        )]
        accounts = [get_or_create_account_for_legacy_user(user) for user in roles]
        for user, account in zip(roles, accounts):
            user.admin_membership.account = account
            user.admin_membership.save(update_fields=['account'])

        def client_for(account):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            return client

        customer = client_for(owner)
        payee = customer.put('/api/wallet/withdraw-accounts/WECHAT/', {
            'payee_account': 'isolated-account', 'payee_name': '隔离测试',
            'expected_id': 0, 'expected_version': 0,
        }, format='json').data['data']
        created = customer.post('/api/wallet/withdraw/', {
            'amount': 20000, 'withdrawal_account_id': payee['id'],
            'expected_payee_version': payee['version'], 'client_request_id': 'release-realtime-test',
        }, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        withdrawal_id = created.data['data']['request']['id']
        url = f'/api/admin/withdrawals/{withdrawal_id}/'
        reviewer, other, manager = (client_for(account) for account in accounts[:3])
        claimed = reviewer.post(url + 'claim/').data['data']
        tokens = [issue_account_tokens(account)[0] for account in accounts]

        async def verify():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={tokens[index]}')
                       for index in (0, 0, 1, 2, 3)]
            for socket in sockets:
                self.assertTrue((await socket.connect())[0])
            offline = sockets.pop(1)
            await offline.disconnect()
            try:
                response = await database_sync_to_async(manager.post)(url + 'release-claim/', {
                    'confirmed_unpaid': True, 'reason': '隔离测试已核实未打款',
                    'expected_claimed_by_account': claimed['claimed_by_account'],
                    'expected_claimed_at': claimed['claimed_at'],
                }, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                for socket in sockets[:3]:
                    event = await socket.receive_json_from(timeout=2)
                    self.assertEqual(event['type'], 'withdraw_status_update')
                    self.assertEqual(event['data'], {'withdraw_id': withdrawal_id, 'account_id': owner.pk, 'status': 'PENDING'})
                self.assertTrue(await sockets[3].receive_nothing(timeout=0.2))
                released = await database_sync_to_async(reviewer.get)(url)
                self.assertIsNone(released.data['data']['claimed_by_account'])

                # The disconnected device misses the release event and must
                # recover current ownership through the same real HTTP API.
                reconnected = WebsocketCommunicator(application, f'/ws/orders/?token={tokens[0]}')
                self.assertTrue((await reconnected.connect())[0])
                try:
                    response = await database_sync_to_async(other.post)(url + 'claim/')
                    self.assertEqual(response.data['code'], 0, response.data)
                    for socket in sockets[:3] + [reconnected]:
                        event = await socket.receive_json_from(timeout=2)
                        self.assertEqual(event['data']['withdraw_id'], withdrawal_id)
                    self.assertTrue(await sockets[3].receive_nothing(timeout=0.2))
                    current = await database_sync_to_async(reviewer.get)(url)
                    self.assertEqual(current.data['data']['claimed_by_account'], accounts[1].pk)
                    self.assertFalse(current.data['data']['claim_is_mine'])
                    rejected = await database_sync_to_async(reviewer.post)(url + 'approve/', {
                        'payout_reference': 'ISOLATED-NO-PAYOUT',
                    }, format='json')
                    self.assertEqual(rejected.status_code, 409)
                finally:
                    await reconnected.disconnect()
            finally:
                for socket in sockets:
                    await socket.disconnect()

        async_to_sync(verify)()
        wallet.refresh_from_db()
        self.assertEqual((wallet.balance, wallet.frozen_amount), (30000, 20000))
