"""Opt-in test using only the registered Redis with a unique disposable prefix."""
import asyncio
import os
import socket
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from chat.models import ChatMessage, ChatNotificationOutbox
from chat.notifier import deliver_chat_notification
from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens
from stores.channels import StoreChannelLayer
from stores.context import resolve_store, store_context


@skipUnless(os.environ.get('XA_CHAT_OUTBOX_REDIS_TEST') == '1', 'requires registered isolated Redis')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, CHAT_NOTIFICATION_OUTBOX_ENABLED=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ChatOutboxRedisTest(TransactionTestCase):
    def test_real_redis_recovers_failed_channel_attempts(self):
        import redis
        from channels_redis.core import RedisChannelLayer

        # Fixed registered endpoint; never flush, enumerate, or delete other keys.
        client = redis.Redis(host='127.0.0.1', port=26379, db=0, socket_connect_timeout=2, socket_timeout=2)
        self.assertTrue(client.ping())
        self.addCleanup(client.close)
        prefix = 'xa_outbox_test_' + uuid4().hex
        receiver = StoreChannelLayer(backend='channels_redis.core.RedisChannelLayer',
            config={'hosts': ['redis://127.0.0.1:26379/0'], 'prefix': prefix})
        sender = StoreChannelLayer(backend='channels_redis.core.RedisChannelLayer',
            config={'hosts': ['redis://127.0.0.1:26379/0'], 'prefix': prefix})
        # Reserve, but do not listen on, a local port to produce a real refused connection.
        port = socket.socket()
        port.bind(('127.0.0.1', 0))
        self.addCleanup(port.close)
        class FailingRedisLayer(RedisChannelLayer):
            attempts = 0

            async def group_send(self, group, message):
                self.attempts += 1
                return await super().group_send(group, message)

        failing = FailingRedisLayer(hosts=[f'redis://127.0.0.1:{port.getsockname()[1]}/0'], prefix=prefix)
        store = resolve_store()
        with store_context(store):
            boss = ClubAccount.objects.create_account(username='outbox-redis-boss', account_type='BOSS')
            api = APIClient(HTTP_HOST='sl.esports.club')
            api.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss)[0])

        async def run():
            with store_context(store):
                channel = await receiver.new_channel()
                groups = ('operators', f'account_{boss.pk}')
                try:
                    for group in groups:
                        await receiver.group_add(group, channel)
                    with patch('chat.notifier.get_channel_layer', return_value=failing):
                        response = await database_sync_to_async(api.post)(
                            '/api/chat/send/', {'content': '隔离通道恢复', 'client_message_id': 'redis-outbox-001'}, format='json')
                    self.assertEqual(response.status_code, 200, response.data)
                    row = await database_sync_to_async(ChatNotificationOutbox.objects.get)()
                    self.assertEqual(row.last_error, 'CHANNEL_UNAVAILABLE')
                    self.assertIsNone(row.completed_at)
                    self.assertEqual(failing.attempts, 1)
                    with patch('chat.notifier.get_channel_layer', return_value=sender):
                        self.assertTrue(await database_sync_to_async(deliver_chat_notification)(row.pk))
                    events = [await asyncio.wait_for(receiver.receive(channel), timeout=5) for _ in range(3)]
                    self.assertEqual(sorted(event['type'] for event in events),
                                     ['chat_message', 'chat_message', 'chat_session_update'])
                    message_id = response.data['data']['id']
                    self.assertTrue(all(event['data']['session_id'] for event in events))
                    self.assertTrue(all(event['data']['message']['id'] == message_id
                                        for event in events if event['type'] == 'chat_message'))
                    self.assertEqual(await database_sync_to_async(ChatMessage.objects.count)(), 1)
                    row = await database_sync_to_async(ChatNotificationOutbox.objects.get)(pk=row.pk)
                    self.assertIsNotNone(row.completed_at)
                    self.assertEqual(row.attempts, 2)
                finally:
                    for group in groups:
                        await receiver.group_discard(group, channel)
                    await receiver.backend.close_pools()
                    await sender.backend.close_pools()
                    await failing.close_pools()

        async_to_sync(run)()
