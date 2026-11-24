"""Open provider application pages receive an actual order cancellation through ASGI."""
import os
from unittest import skipUnless

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.db import connection
from django.test import override_settings
from rest_framework.test import APITransactionTestCase

from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from orders.tests.test_team_invitation_detail import TeamInvitationDetailTest


@skipUnless(os.environ.get('XA_TEAM_CANCEL_BROWSER_NETWORK') == '1', 'Requires isolated ASGI and H5 build')
@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_RECHARGE_PROOF=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class TeamCancelBrowserNetworkTest(APITransactionTestCase):
    setUp = TeamInvitationDetailTest.setUp
    login = TeamInvitationDetailTest.login
    ok = TeamInvitationDetailTest.ok
    recharge = TeamInvitationDetailTest.recharge
    invite = TeamInvitationDetailTest.invite

    def test_open_team_pages_update_after_boss_cancels(self):
        self.run_scenario('confirm_cancel')

    def test_declined_invitation_updates_applicant_and_boss(self):
        self.run_scenario('decline')

    def run_scenario(self, scenario):
        database = os.environ.get('XA_ASGI_NETWORK_TEST_DB')
        if not database or connection.settings_dict['NAME'] != database:
            self.fail('An isolated file-backed test database is required')
        self.invite()
        users = (self.boss, self.provider, self.partner)
        tokens = [issue_account_tokens(self.accounts[user.pk])[0] for user in users]
        order_id, application_id = self.order.pk, self.application.pk

        async def verify():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={token}') for token in tokens[1:]]
            for socket in sockets:
                self.assertTrue((await socket.connect())[0])

            async def wait_order_event(socket, status=None):
                for _ in range(8):
                    event = await socket.receive_json_from(timeout=2)
                    if (event['type'] == 'order_status_update' and event['data'].get('id') == order_id
                            and (status is None or event['data'].get('status') == status)):
                        return event
                self.fail(f'订单更新未送达: {order_id}')

            try:
                if scenario == 'decline':
                    declined = await database_sync_to_async(self.client.post)(
                        self.action, {'action': 'WITHDRAW'}, format='json')
                    self.assertEqual(declined.data['code'], 0, declined.data)
                    for socket in sockets:
                        await wait_order_event(socket)
                    self.login(self.boss)
                    detail = self.ok(await database_sync_to_async(self.client.get)(f'/api/orders/orders/{order_id}/'))
                    self.assertFalse(any(item['id'] == application_id for item in detail['applications']))
                    return
                confirmed = await database_sync_to_async(self.client.post)(
                    self.action, {'action': 'CONFIRM_TEAM'}, format='json')
                self.assertEqual(confirmed.data['code'], 0, confirmed.data)
                for socket in sockets:
                    await wait_order_event(socket)
                self.login(self.boss)
                detail = self.ok(await database_sync_to_async(self.client.get)(f'/api/orders/orders/{order_id}/'))
                self.assertTrue(next(item for item in detail['applications'] if item['id'] == application_id)['ready'])
                cancelled = await database_sync_to_async(self.client.post)(
                    f'/api/orders/orders/{order_id}/cancel/', {}, format='json')
                self.assertEqual(cancelled.data['code'], 0, cancelled.data)
                for socket in sockets:
                    await wait_order_event(socket, 'CANCELLED')
            finally:
                for socket in sockets:
                    await socket.disconnect()

        self.login(self.partner)
        async_to_sync(verify)()
