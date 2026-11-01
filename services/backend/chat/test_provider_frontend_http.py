"""Provider support page exchanges messages with the real staff API."""
import json
import os
import shutil
import subprocess
from pathlib import Path

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider
from chat.models import ChatMessage, ChatSession
from config.asgi import application


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ProviderChatFrontendHTTPTest(LiveServerTestCase):
    def test_provider_page_and_staff_exchange_and_recover(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        provider, staff = make_provider(), make_console_user(['chat:view', 'chat:reply'])
        provider_account, staff_account = [get_or_create_account_for_legacy_user(user) for user in (provider, staff)]
        for user, account in ((provider, provider_account), (staff, staff_account)):
            link_legacy_relations(user, account)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(provider_account)[0])
        self.assertEqual(client.get('/api/chat/unread/').data['data']['unread'], 0)
        self.assertFalse(ChatSession.objects.filter(account=provider_account).exists())
        session_data = client.get('/api/chat/session/').data
        self.assertEqual(session_data['code'], 0, session_data)
        session_id = session_data['data']['session']['id']
        admin = APIClient()
        admin.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        for index in range(51):
            response = admin.post(f'/api/admin/chat-sessions/{session_id}/reply/',
                                  {'content': f'历史客服消息 {index}'}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
        self.assertEqual(client.get('/api/chat/unread/').data['data']['unread'], 51)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/provider-chat-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'providerToken': issue_account_tokens(provider_account)[0],
                              'providerId': provider_account.pk, 'staffToken': issue_account_tokens(staff_account)[0],
                              'sessionId': session_id}), text=True, encoding='utf-8', capture_output=True, timeout=40, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(ChatMessage.objects.filter(session_id=session_id, content='陪玩咨询排班').count(), 1)
        self.assertEqual(ChatMessage.objects.filter(session_id=session_id, content='网络中断后核对').count(), 1)
        self.assertEqual(ChatMessage.objects.filter(session_id=session_id, content='网络中断后重试').count(), 1)
        self.assertEqual(ChatSession.objects.get(pk=session_id).unread_user, 0)
        self.assertEqual(client.get('/api/chat/unread/').data['data']['unread'], 0)
        if os.environ.get('XA_PROVIDER_CHAT_375') == '1':
            browser = subprocess.run([node, str(repo / 'scripts/provider-chat-mobile-375-live.cjs')],
                input=json.dumps({'base': self.live_server_url, 'providerToken': issue_account_tokens(provider_account)[0],
                                  'providerId': provider_account.pk, 'staffToken': issue_account_tokens(staff_account)[0],
                                  'sessionId': session_id}), text=True, encoding='utf-8', capture_output=True, timeout=60, cwd=repo)
            self.assertEqual(browser.returncode, 0, browser.stderr + browser.stdout)
            self.assertEqual(ChatMessage.objects.filter(session_id=session_id, content='小屏验收咨询').count(), 1)


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class ProviderChatRealtimeTest(APITransactionTestCase):
    def test_provider_and_staff_receive_each_others_messages(self):
        provider, staff = make_provider(), make_console_user(['chat:view', 'chat:reply'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (provider, staff)]
        for user, account in zip((provider, staff), accounts):
            link_legacy_relations(user, account)
        staff.admin_membership.account = accounts[1]
        staff.admin_membership.save(update_fields=['account'])
        tokens = [issue_account_tokens(account)[0] for account in accounts]
        clients = [APIClient(), APIClient()]
        for client, token in zip(clients, tokens):
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

        async def verify():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={token}') for token in tokens]
            try:
                for socket in sockets:
                    self.assertTrue((await socket.connect())[0])
                sent = await database_sync_to_async(clients[0].post)(
                    '/api/chat/send/', {'content': '陪玩询问订单'}, format='json')
                self.assertEqual(sent.data['code'], 0, sent.data)
                events = [await socket.receive_json_from(timeout=3) for socket in sockets]
                self.assertTrue(all(event['type'] == 'chat_message' for event in events))
                session_id = events[0]['data']['session_id']
                reply = await database_sync_to_async(clients[1].post)(
                    f'/api/admin/chat-sessions/{session_id}/reply/', {'content': '客服已收到'}, format='json')
                self.assertEqual(reply.data['code'], 0, reply.data)
                events = [await socket.receive_json_from(timeout=3) for socket in sockets]
                self.assertTrue(all(event['data']['message']['content'] == '客服已收到' for event in events))
            finally:
                for socket in sockets:
                    await socket.disconnect()
        async_to_sync(verify)()
