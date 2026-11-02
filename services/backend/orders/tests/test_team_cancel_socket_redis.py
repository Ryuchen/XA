"""Cancellation of a real team application over the isolated Redis channel layer."""
import os
from unittest import skipUnless

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import override_settings
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from orders.tests.test_team_invitation_detail import TeamInvitationDetailTest


REDIS_URL = os.environ.get('XA_TEAM_CANCEL_REDIS_URL', '')


@skipUnless(REDIS_URL, 'Requires an isolated Redis channel layer')
@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_RECHARGE_PROOF=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, CHANNEL_LAYERS={'default': {
                       'BACKEND': 'channels_redis.core.RedisChannelLayer',
                       'CONFIG': {'hosts': [REDIS_URL], 'prefix': 'xa_team_cancel_integration'},
                   }})
class TeamCancelSocketRedisTest(APITransactionTestCase):
    setUp = TeamInvitationDetailTest.setUp
    login = TeamInvitationDetailTest.login
    ok = TeamInvitationDetailTest.ok
    recharge = TeamInvitationDetailTest.recharge
    invite = TeamInvitationDetailTest.invite

    def test_both_members_receive_cancel_and_can_reload_the_outcome(self):
        self.invite()
        tokens = [issue_account_tokens(self.accounts[user.pk])[0]
                  for user in (self.boss, self.provider, self.partner)]
        boss = APIClient()
        boss.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])

        async def verify():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={token}') for token in tokens[1:]]
            try:
                for socket in sockets:
                    self.assertTrue((await socket.connect())[0])
                response = await database_sync_to_async(boss.post)(
                    f'/api/orders/orders/{self.order.pk}/cancel/', {}, format='json')
                self.ok(response)
                for socket in sockets:
                    for _ in range(10):
                        event = await socket.receive_json_from(timeout=4)
                        if event['type'] == 'order_status_update' and event['data'].get('id') == self.order.pk:
                            break
                    else:
                        self.fail('未收到订单状态更新')
                    self.assertEqual(event['data']['status'], 'CANCELLED')
                for token in tokens[1:]:
                    client = APIClient()
                    client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                    detail = self.ok(await database_sync_to_async(client.get)(self.path))
                    self.assertEqual(detail['closed_reason'], '订单已取消，本次报名结束。')
                    self.assertFalse(detail['can_confirm'])
            finally:
                for socket in sockets:
                    await socket.disconnect()

        async_to_sync(verify)()
