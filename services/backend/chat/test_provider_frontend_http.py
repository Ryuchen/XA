"""Provider support page exchanges messages with the real staff API."""
import tempfile
from urllib.parse import urlsplit

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from chat.models import ChatMessage, ChatSession
from chat.test_image_upload_http import broken_png_crc, png_bytes
from config.asgi import application
from engagement.models import PointFundingLot
from stores.context import resolve_store, store_context


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ProviderChatFrontendHTTPTest(LiveServerTestCase):
    def test_provider_order_context_reaches_staff(self):
        self.enterContext(store_context(resolve_store()))
        media = tempfile.TemporaryDirectory(prefix='xa-provider-order-chat-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss, provider, staff = make_user(), make_provider(deposit_mode='EARNINGS'), make_console_user(['chat:view', 'chat:reply', 'order:view'])
        boss_account, provider_account, staff_account = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, staff)]
        for user, account in ((boss, boss_account), (provider, provider_account), (staff, staff_account)):
            link_legacy_relations(user, account)
        provider.escort_profile.account = provider_account
        provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        wallet = make_wallet(boss, balance=50000)
        wallet.account = boss_account
        wallet.save(update_fields=['account'])
        PointFundingLot.objects.create(account=boss_account, source_key='provider-order-chat-prepaid', remaining=50000)
        service = make_service(name='隔离订单咨询服务', price=10000)
        provider.escort_profile.service_items.add(service)
        boss_token = issue_account_tokens(boss_account)[0]
        provider_token = issue_account_tokens(provider_account)[0]
        staff_token = issue_account_tokens(staff_account)[0]
        client = APIClient(HTTP_HOST='sl.esports.club')
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
        result = client.post('/api/orders/create/', {'service_id': service.pk, 'provider_id': provider_account.pk,
            'client_request_id': 'provider-order-chat-context'}, format='json').data
        self.assertEqual(result['code'], 0, result)
        order = result['data']
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + provider_token)
        available = client.post('/api/users/escorts/status/', {'status': 'AVAILABLE'}, format='json').data
        self.assertEqual(available['code'], 0, available)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
        second = client.post('/api/orders/create/', {'service_id': service.pk, 'provider_id': provider_account.pk,
            'client_request_id': 'provider-order-chat-context-second'}, format='json').data
        self.assertEqual(second['code'], 0, second)
        order_no = order['order_no']
        provider_client = APIClient(HTTP_HOST='sl.esports.club')
        provider_client.credentials(HTTP_AUTHORIZATION='Bearer ' + provider_token)
        text = provider_client.post('/api/chat/send/', {'content': f'订单号 {order_no}\n订单咨询测试',
            'order_ref': str(order['id'])}, format='json').data
        self.assertEqual(text['code'], 0, text)
        image = SimpleUploadedFile('chat.png', png_bytes(), content_type='image/png')
        image_message = provider_client.post('/api/chat/send/', {'content': f'订单号 {order_no}\n图片情况',
            'order_ref': str(order['id']), 'image': image}, format='multipart').data
        self.assertEqual(image_message['code'], 0, image_message)
        session = provider_client.get('/api/chat/session/').data
        self.assertEqual(session['code'], 0, session)
        session_id = session['data']['session']['id']
        admin = APIClient(HTTP_HOST='admin.esports.club')
        admin.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        self.assertEqual(admin.post(f'/api/admin/chat-sessions/{session_id}/claim/', {'expected_version': 0}, format='json').data['code'], 0)
        listed = admin.get(f'/api/admin/chat-sessions/{session_id}/messages/').data
        self.assertEqual(listed['code'], 0, listed)
        rows = listed['data']['list']
        self.assertTrue(any(row['content_type'] == 'TEXT' and order_no in row['content'] for row in rows))
        image_row = next(row for row in rows if row['content_type'] == 'IMAGE' and order_no in row['content'] and '图片情况' in row['content'])
        self.assertTrue(image_row['image_url'])
        image_response = admin.get(urlsplit(image_row['image_url']).path)
        self.assertEqual(image_response.status_code, 200)
        self.assertTrue(b''.join(image_response.streaming_content))
        image_response.close()
        orders = admin.get('/api/admin/orders/', {'order_no_exact': order_no}).data
        self.assertEqual(orders['code'], 0, orders)
        self.assertEqual(len(orders['data']['list']), 1)
        self.assertEqual(orders['data']['list'][0]['order_no'], order_no)
        message = ChatMessage.objects.get(session__account=provider_account, content__contains='订单咨询测试')
        self.assertIn(order['order_no'], message.content)

    def test_provider_page_and_staff_exchange_and_recover(self):
        self.enterContext(store_context(resolve_store()))
        provider, staff = make_provider(), make_console_user(['chat:view', 'chat:reply'])
        provider_account, staff_account = [get_or_create_account_for_legacy_user(user) for user in (provider, staff)]
        for user, account in ((provider, provider_account), (staff, staff_account)):
            link_legacy_relations(user, account)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        client = APIClient(HTTP_HOST='sl.esports.club')
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(provider_account)[0])
        self.assertEqual(client.get('/api/chat/unread/').data['data']['unread'], 0)
        self.assertFalse(ChatSession.objects.filter(account=provider_account).exists())
        session_data = client.get('/api/chat/session/').data
        self.assertEqual(session_data['code'], 0, session_data)
        session_id = session_data['data']['session']['id']
        admin = APIClient(HTTP_HOST='admin.esports.club')
        admin.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        self.assertEqual(admin.post(f'/api/admin/chat-sessions/{session_id}/claim/', {'expected_version': 0}, format='json').data['code'], 0)
        for index in range(51):
            response = admin.post(f'/api/admin/chat-sessions/{session_id}/reply/',
                                  {'content': f'历史客服消息 {index}'}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
        self.assertEqual(client.get('/api/chat/unread/').data['data']['unread'], 51)
        first = client.get('/api/chat/session/').data
        self.assertTrue(any(row['content'] == '历史客服消息 50' for row in first['data']['messages']))
        earliest = min(row['id'] for row in first['data']['messages'])
        previous = client.get('/api/chat/session/', {'before_id': earliest}).data
        self.assertTrue(any(row['content'] == '历史客服消息 0' for row in previous['data']['messages']))
        self.assertEqual(client.post('/api/chat/send/', {'content': '陪玩咨询排班',
            'client_message_id': 'provider-chat-live-001'}, format='json').data['code'], 0)
        self.assertEqual(admin.post(f'/api/admin/chat-sessions/{session_id}/reply/',
            {'content': '客服已收到，稍后核对', 'client_message_id': 'staff-chat-live-001'}, format='json').data['code'], 0)
        for content, client_id in (('网络中断后核对', 'provider-chat-recover-001'), ('网络中断后重试', 'provider-chat-recover-002')):
            self.assertEqual(client.post('/api/chat/send/', {'content': content, 'client_message_id': client_id}, format='json').data['code'], 0)
            status = client.get('/api/chat/message-status/', {'client_message_id': client_id}).data
            self.assertEqual(status['data']['status'], 'FOUND')
            self.assertEqual(client.post('/api/chat/send/', {'content': content, 'client_message_id': client_id}, format='json').data['code'], 0)
        latest = client.get('/api/chat/session/').data
        through_id = max(row['id'] for row in latest['data']['messages'])
        self.assertEqual(client.post('/api/chat/read/', {'through_id': through_id}, format='json').data['code'], 0)
        self.assertEqual(ChatMessage.objects.filter(session_id=session_id, content='陪玩咨询排班').count(), 1)
        self.assertEqual(ChatMessage.objects.filter(session_id=session_id, content='网络中断后核对').count(), 1)
        self.assertEqual(ChatMessage.objects.filter(session_id=session_id, content='网络中断后重试').count(), 1)
        self.assertEqual(ChatSession.objects.get(pk=session_id).unread_user, 0)
        self.assertEqual(client.get('/api/chat/unread/').data['data']['unread'], 0)
        with tempfile.TemporaryDirectory(prefix='xa-provider-chat-images-') as media_root, override_settings(MEDIA_ROOT=media_root):
            broken_file = SimpleUploadedFile('corrupt.png', broken_png_crc(), content_type='image/png')
            refusal = client.post('/api/chat/send/', {'content': '排班截图，麻烦核对', 'image': broken_file}, format='multipart').data
            self.assertEqual(refusal['code'], 400)
            self.assertEqual(refusal['msg'], '图片内容无效')
            self.assertEqual(client.post('/api/chat/send/', {'content': '小屏验收咨询'}, format='json').data['code'], 0)
            valid = SimpleUploadedFile('chat.png', png_bytes(), content_type='image/png')
            self.assertEqual(client.post('/api/chat/send/', {'content': '排班截图，麻烦核对', 'image': valid}, format='multipart').data['code'], 0)
            lost = SimpleUploadedFile('chat.png', png_bytes(), content_type='image/png')
            self.assertEqual(client.post('/api/chat/send/', {'content': '网络中断后的截图', 'image': lost,
                'client_message_id': 'provider-chat-image-001'}, format='multipart').data['code'], 0)
            self.assertEqual(client.get('/api/chat/message-status/', {'client_message_id': 'provider-chat-image-001'}).data['data']['status'], 'FOUND')
            self.assertEqual(admin.post(f'/api/admin/chat-sessions/{session_id}/reply/', {'content': '小屏客服回复'}, format='json').data['code'], 0)
            listed = admin.get(f'/api/admin/chat-sessions/{session_id}/messages/').data
            image_row = next(row for row in listed['data']['list'] if row['content_type'] == 'IMAGE' and row['content'] == '排班截图，麻烦核对')
            self.assertTrue(image_row['image_url'])
            image_response = admin.get(urlsplit(image_row['image_url']).path)
            self.assertEqual(image_response.status_code, 200)
            self.assertTrue(b''.join(image_response.streaming_content))
            image_response.close()
        self.assertEqual(ChatMessage.objects.filter(session_id=session_id, content='小屏验收咨询').count(), 1)
        self.assertEqual(ChatMessage.objects.filter(session_id=session_id, content_type='IMAGE', is_from_support=False).count(), 2)
        self.assertTrue(ChatMessage.objects.filter(session_id=session_id, content_type='IMAGE', content='排班截图，麻烦核对').exists())
        self.assertEqual(ChatMessage.objects.filter(session_id=session_id, content_type='IMAGE', content='网络中断后的截图').count(), 1)


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
        clients = [APIClient(HTTP_HOST='sl.esports.club'), APIClient(HTTP_HOST='admin.esports.club')]
        for client, token in zip(clients, tokens):
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

        async def verify():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={token}',
                headers=[(b'host', host.encode())]) for token, host in zip(tokens, ('sl.esports.club', 'admin.esports.club'))]
            try:
                for socket in sockets:
                    self.assertTrue((await socket.connect())[0])
                sent = await database_sync_to_async(clients[0].post)(
                    '/api/chat/send/', {'content': '陪玩询问订单'}, format='json')
                self.assertEqual(sent.data['code'], 0, sent.data)
                events = [await socket.receive_json_from(timeout=3) for socket in sockets]
                self.assertEqual([event['type'] for event in events], ['chat_message', 'chat_session_update'])
                session_id = events[0]['data']['session_id']
                self.assertEqual(events[1]['data']['session_id'], session_id)
                if 'session' in events[1]['data']:
                    self.assertNotIn('last_message', events[1]['data']['session'])
                self.assertNotIn('message', events[1]['data'])
                claim = await database_sync_to_async(clients[1].post)(
                    f'/api/admin/chat-sessions/{session_id}/claim/', {'expected_version': 0}, format='json')
                self.assertEqual(claim.data['code'], 0, claim.data)
                self.assertEqual((await sockets[1].receive_json_from(timeout=3))['type'], 'chat_session_update')
                self.assertEqual((await sockets[0].receive_json_from(timeout=3))['type'], 'chat_session_update')
                reply = await database_sync_to_async(clients[1].post)(
                    f'/api/admin/chat-sessions/{session_id}/reply/', {'content': '客服已收到'}, format='json')
                self.assertEqual(reply.data['code'], 0, reply.data)
                received = []
                for socket in sockets:
                    rows = [await socket.receive_json_from(timeout=3)]
                    if rows[0]['type'] != 'chat_message':
                        rows.append(await socket.receive_json_from(timeout=3))
                    for event in rows:
                        if event['type'] == 'chat_session_update':
                            self.assertEqual(event['data']['session_id'], session_id)
                            self.assertNotIn('message', event['data'])
                    received.append(next(event for event in rows if event['type'] == 'chat_message'))
                self.assertTrue(all(event['data']['message']['content'] == '客服已收到' for event in received), received)
            finally:
                for socket in sockets:
                    await socket.disconnect()
        async_to_sync(verify)()