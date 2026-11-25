"""Regression coverage for scoped chat reads, refresh notices and images."""
from datetime import timedelta
from io import BytesIO
import random
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import patch
from urllib.parse import urlsplit

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient, APITestCase, APITransactionTestCase

from chat.models import ChatMessage, ChatSession, ChatSessionNotificationOutbox
from chat.notifier import deliver_chat_session_notification
from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from orders.consumers import current_chat_payload
from orders.tests.factories import make_console_user
from stores.context import resolve_store, store_context
from stores.models import Store


STORE_HOSTS = {'shenglang': 'sl.esports.club', 'xinan': 'xa.esports.club'}
CHAT_OVERRIDES = dict(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)


def image_bytes(image_format='PNG', color=(19, 132, 140)):
    stream = BytesIO()
    Image.new('RGB', (3, 3), color).save(stream, format=image_format)
    return stream.getvalue()


def truncated_jpeg():
    return image_bytes('JPEG')[:-1]


def large_png():
    stream = BytesIO()
    pixels = random.Random(21).randbytes(1400 * 1400 * 3)
    Image.frombytes('RGB', (1400, 1400), pixels).save(stream, format='PNG')
    data = stream.getvalue()
    assert len(data) > 5 * 1024 * 1024
    return data


class CaptureLayer:
    def __init__(self):
        self.calls = []

    async def group_send(self, group, event):
        self.calls.append((group, event))


@override_settings(**CHAT_OVERRIDES)
class ChatReadRegressionTest(APITestCase):
    def make_owner(self, username, account_type=ClubAccount.AccountType.BOSS):
        account = ClubAccount.objects.create_account(username=username, account_type=account_type)
        client = APIClient(HTTP_HOST=STORE_HOSTS['shenglang'])
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        return account, client

    def make_staff(self, username):
        user = make_console_user(['chat:view', 'chat:reply'])
        account = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = account
        user.admin_membership.save(update_fields=['account'])
        client = APIClient(HTTP_HOST='admin.esports.club')
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        return account, client

    def claim(self, staff_client, session_id):
        response = staff_client.post(f'/api/admin/chat-sessions/{session_id}/claim/',
                                     {'expected_version': 0}, format='json')
        self.assertEqual((response.status_code, response.data['code']), (200, 0), response.data)

    def test_empty_read_scopes_do_not_clear_any_role_unread(self):
        boss, boss_client = self.make_owner('empty-read-boss')
        provider, provider_client = self.make_owner('empty-read-provider', ClubAccount.AccountType.PROVIDER)
        _, boss_staff = self.make_staff('empty-read-staff-boss')
        _, provider_staff = self.make_staff('empty-read-staff-provider')
        for account, client, staff in ((boss, boss_client, boss_staff), (provider, provider_client, provider_staff)):
            session_id = client.get('/api/chat/session/').data['data']['session']['id']
            message = ChatMessage.objects.create(session_id=session_id, sender_account=account,
                                                 content='尚未查看的消息')
            session = ChatSession.objects.get(pk=session_id)
            session.unread_support = 1
            session.save(update_fields=['unread_support'])
            self.claim(staff, session_id)
            for reader in (client, staff):
                url = ('/api/chat/read/' if reader is client else
                       f'/api/admin/chat-sessions/{session_id}/read/')
                for payload in ({}, {'message_ids': []}, {'through_id': 0}):
                    response = reader.post(url, payload, format='json')
                    if payload:
                        self.assertEqual(response.status_code, 200, response.data)
                    else:
                        self.assertEqual(response.status_code, 400, response.data)
                    session.refresh_from_db()
                    message.refresh_from_db()
                    self.assertFalse(message.is_read)
                    self.assertEqual(session.unread_support, 1)
            response = client.post('/api/chat/read/', {'message_ids': [message.pk]}, format='json')
            self.assertEqual(response.data['data']['unread_user'], 0)

    def test_owner_history_pages_and_duplicate_reads_only_ack_loaded_messages(self):
        boss, client = self.make_owner('history-read-boss')
        _, staff = self.make_staff('history-read-staff')
        session_id = client.get('/api/chat/session/').data['data']['session']['id']
        session = ChatSession.objects.get(pk=session_id)
        rows = [ChatMessage.objects.create(session=session, is_from_support=True, content=f'客服历史 {index}')
                for index in range(55)]
        ChatMessage.objects.filter(pk=rows[-1].pk).update(created_at=timezone.now() - timedelta(days=1))
        session.unread_user = 55
        session.save(update_fields=['unread_user'])
        self.claim(staff, session_id)

        first = client.get('/api/chat/session/').data['data']
        self.assertEqual(len(first['messages']), 50)
        self.assertNotIn(rows[-1].pk, [row['id'] for row in first['messages']])
        loaded = [row['id'] for row in first['messages']]
        for _ in range(2):
            response = client.post('/api/chat/read/', {'message_ids': loaded}, format='json')
            self.assertEqual(response.data['data']['unread_user'], 5)
        older = client.get('/api/chat/session/', {'before_id': first['next_before_id']}).data['data']
        self.assertIn(rows[-1].pk, [row['id'] for row in older['messages']])
        response = client.post('/api/chat/read/', {'message_ids': [row['id'] for row in older['messages']]},
                               format='json')
        self.assertEqual(response.data['data']['unread_user'], 0)
        self.assertEqual(ChatMessage.objects.filter(session=session, is_from_support=True, is_read=True).count(), 55)

    def test_staff_history_cutoff_and_duplicate_reads_keep_backdated_message_unread(self):
        boss, client = self.make_owner('staff-history-boss')
        _, staff = self.make_staff('staff-history-reader')
        session_id = client.get('/api/chat/session/').data['data']['session']['id']
        session = ChatSession.objects.get(pk=session_id)
        rows = [ChatMessage.objects.create(session=session, sender_account=boss, content=f'老板历史 {index}')
                for index in range(55)]
        ChatMessage.objects.filter(pk=rows[-1].pk).update(created_at=timezone.now() - timedelta(days=1))
        session.unread_support = 55
        session.save(update_fields=['unread_support'])
        self.claim(staff, session_id)
        url = f'/api/admin/chat-sessions/{session_id}/messages/'
        first = staff.get(url, {'cursor': 1}).data['data']
        self.assertEqual(len(first['list']), 50)
        self.assertNotIn(rows[-1].pk, [row['id'] for row in first['list']])
        cutoff = max(row['id'] for row in first['list'])
        for _ in range(2):
            response = staff.post(f'/api/admin/chat-sessions/{session_id}/read/', {'through_id': cutoff}, format='json')
            self.assertFalse(ChatMessage.objects.get(pk=rows[-1].pk).is_read)
            self.assertEqual(response.data['data']['unread_support'], 1, response.data)
        rows[-1].refresh_from_db()
        self.assertFalse(rows[-1].is_read)
        older = staff.get(url, {'cursor': 1, 'before_id': first['next_before_id']}).data['data']
        response = staff.post(f'/api/admin/chat-sessions/{session_id}/read/',
                              {'message_ids': [row['id'] for row in older['list']]}, format='json')
        self.assertEqual(response.data['data']['unread_support'], 0)
        self.assertEqual(ChatMessage.objects.filter(session=session, is_from_support=False, is_read=True).count(), 55)


@override_settings(**CHAT_OVERRIDES, CHAT_NOTIFICATION_OUTBOX_ENABLED=True,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class ChatReadRealtimeRegressionTest(APITransactionTestCase):
    def test_staff_read_refreshes_owner_and_committed_owner_can_reread_messages(self):
        boss = ClubAccount.objects.create_account(username='read-refresh-boss', account_type='BOSS')
        user = make_console_user(['chat:view', 'chat:reply'])
        staff = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = staff
        user.admin_membership.save(update_fields=['account'])
        boss_client = APIClient(HTTP_HOST=STORE_HOSTS['shenglang'])
        boss_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss)[0])
        staff_client = APIClient(HTTP_HOST='admin.esports.club')
        staff_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
        session = ChatSession.objects.create(account=boss, unread_support=2, last_message='仅供刷新断言')
        first = ChatMessage.objects.create(session=session, sender_account=boss, content='客服标记前的消息')
        second = ChatMessage.objects.create(session=session, sender_account=boss, content='随后展示的消息')
        claimed = staff_client.post(f'/api/admin/chat-sessions/{session.pk}/claim/',
                                    {'expected_version': 0}, format='json')
        self.assertEqual(claimed.data['code'], 0, claimed.data)
        token = issue_account_tokens(boss)[0]

        async def verify():
            socket = WebsocketCommunicator(application, f'/ws/orders/?token={token}',
                                           headers=[(b'host', STORE_HOSTS['shenglang'].encode())])
            try:
                self.assertTrue((await socket.connect())[0])
                response = await database_sync_to_async(staff_client.post)(
                    f'/api/admin/chat-sessions/{session.pk}/read/', {'through_id': second.pk}, format='json')
                self.assertEqual(response.data['data']['unread_support'], 0, response.data)
                event = await socket.receive_json_from(timeout=3)
                self.assertEqual(event['type'], 'chat_session_update')
                self.assertEqual(event['data']['session_id'], session.pk)
                self.assertNotIn('message', event['data'])
                self.assertNotIn('last_message', event['data']['session'])
                self.assertNotIn('仅供刷新断言', repr(event))
                staff_payload = await current_chat_payload(staff, {'session_id': session.pk})
                self.assertIsNotNone(staff_payload)
                self.assertNotIn('last_message', staff_payload['session'])
                self.assertNotIn('仅供刷新断言', repr(staff_payload))
                refreshed = await database_sync_to_async(boss_client.get)('/api/chat/session/')
                self.assertEqual(refreshed.data['code'], 0, refreshed.data)
                self.assertTrue(all(row['is_read'] for row in refreshed.data['data']['messages']
                                    if row['id'] in (first.pk, second.pk)))
            finally:
                await socket.disconnect()

        async_to_sync(verify)()
        row = ChatSessionNotificationOutbox.objects.filter(session=session).latest('pk')
        self.assertIsNotNone(row.operators_sent_at)
        self.assertIsNotNone(row.account_sent_at)
        self.assertIsNotNone(row.completed_at)
        self.assertEqual(row.account_skipped_reason, '')


@override_settings(**CHAT_OVERRIDES)
class ChatSessionNotificationRegressionTest(TestCase):
    def setUp(self):
        self.boss = ClubAccount.objects.create_account(username='session-notice-boss', account_type='BOSS')
        self.session = ChatSession.objects.create(account=self.boss, last_message='不得进入刷新事件的正文')

    def deliver(self, *, account_id=None, cycle=None):
        row = ChatSessionNotificationOutbox.objects.create(
            session=self.session, recipient_account_id_snapshot=self.boss.pk if account_id is None else account_id,
            recipient_business_cycle=self.boss.business_cycle if cycle is None else cycle,
            next_attempt_at=timezone.now())
        layer = CaptureLayer()
        with patch('chat.notifier.get_channel_layer', return_value=layer):
            self.assertTrue(deliver_chat_session_notification(row.pk))
        row.refresh_from_db()
        return row, layer.calls

    def test_refresh_events_contain_identifiers_not_message_content(self):
        row, calls = self.deliver()
        self.assertEqual([group for group, _ in calls], ['operators', f'account_{self.boss.pk}'])
        self.assertEqual(calls[0][1], {'type': 'chat_session_update', 'data': {'session_id': self.session.pk}})
        self.assertEqual(calls[1][1]['data'], {'session_id': self.session.pk,
                                              'recipient_business_cycle': self.boss.business_cycle})
        self.assertNotIn('正文', repr(calls))
        self.assertNotIn('content', repr(calls))
        self.assertIsNotNone(row.account_sent_at)

    def test_original_cycle_and_current_account_permissions_are_rechecked(self):
        original_cycle = self.boss.business_cycle
        for field, value in (('business_cycle', original_cycle + 1), ('license_membership_active', False),
                             ('is_active', False), ('can_login', False)):
            with self.subTest(field=field):
                ClubAccount.objects.filter(pk=self.boss.pk).update(**{field: value})
                row, calls = self.deliver()
                self.assertEqual([group for group, _ in calls], ['operators'])
                self.assertIsNone(row.account_sent_at)
                self.assertEqual(row.account_skipped_reason, '原收件账号或周期不可核验')
                self.assertIsNotNone(row.completed_at)
                ClubAccount.objects.filter(pk=self.boss.pk).update(business_cycle=original_cycle,
                    license_membership_active=True, is_active=True, can_login=True)

    def test_changed_owner_or_wrong_snapshot_never_targets_the_account_group(self):
        other = ClubAccount.objects.create_account(username='session-notice-other', account_type='BOSS')
        row, calls = self.deliver(account_id=other.pk)
        self.assertEqual([group for group, _ in calls], ['operators'])
        self.assertIsNone(row.account_sent_at)
        self.session.account = other
        self.session.save(update_fields=['account'])
        row, calls = self.deliver()
        self.assertEqual([group for group, _ in calls], ['operators'])
        self.assertIsNone(row.account_sent_at)


@override_settings(**CHAT_OVERRIDES)
class ChatImageRegressionTest(APITestCase):
    def setUp(self):
        media = TemporaryDirectory(prefix='xa-chat-read-image-regression-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        self.boss = ClubAccount.objects.create_account(username='image-rules-boss', account_type='BOSS')
        self.boss_client = self.client_for(self.boss, STORE_HOSTS['shenglang'])
        user = make_console_user(['chat:view', 'chat:reply'])
        self.staff = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = self.staff
        user.admin_membership.save(update_fields=['account'])
        self.staff_client = self.client_for(self.staff, 'admin.esports.club')
        self.session_id = self.boss_client.get('/api/chat/session/').data['data']['session']['id']
        response = self.staff_client.post(f'/api/admin/chat-sessions/{self.session_id}/claim/',
                                          {'expected_version': 0}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)

    def client_for(self, account, host):
        client = APIClient(HTTP_HOST=host)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        return client

    def upload(self, *, staff, data, filename='chat.png', content_type='image/png', client_id=None):
        payload = {'content': '图片回归', 'image': SimpleUploadedFile(filename, data, content_type=content_type)}
        if client_id:
            payload['client_message_id'] = client_id
        client = self.staff_client if staff else self.boss_client
        url = (f'/api/admin/chat-sessions/{self.session_id}/reply/' if staff else '/api/chat/send/')
        return client.post(url, payload, format='multipart')

    def test_owner_and_staff_share_format_size_and_actual_content_validation(self):
        for staff in (False, True):
            for image_format in ('JPEG', 'PNG', 'WEBP'):
                with self.subTest(staff=staff, image_format=image_format):
                    response = self.upload(staff=staff, data=image_bytes(image_format))
                    self.assertEqual((response.status_code, response.data['code']), (200, 0), response.data)
                    self.assertEqual(response.data['data']['content_type'], 'IMAGE')
            invalid = (
                ('gif-as-png', image_bytes('GIF'), '请上传 JPG、PNG 或 WebP 图片'),
                ('text-as-png', b'not an image', '图片内容无效'),
                ('truncated-jpeg', truncated_jpeg(), '图片内容无效'),
                ('oversize', large_png(), '图片不能超过 5MB'),
            )
            for name, data, message in invalid:
                with self.subTest(staff=staff, case=name):
                    before = ChatMessage.objects.count()
                    response = self.upload(staff=staff, data=data)
                    self.assertEqual((response.status_code, response.data['code']), (400, 400), response.data)
                    if name != 'text-as-png':
                        self.assertEqual(response.data['msg'], message)
                    self.assertEqual(ChatMessage.objects.count(), before)

    def test_valid_image_retry_recovers_same_message_and_changed_bytes_conflict(self):
        for staff in (False, True):
            with self.subTest(staff=staff):
                data = image_bytes('PNG', (20, 30, 40))
                client_id = ('staff-image-retry' if staff else 'owner-image-retry')
                first = self.upload(staff=staff, data=data, client_id=client_id)
                self.assertEqual(first.data['code'], 0, first.data)
                replay = self.upload(staff=staff, data=data, client_id=client_id)
                self.assertEqual(replay.data['code'], 0, replay.data)
                self.assertEqual(replay.data['data']['id'], first.data['data']['id'])
                self.assertEqual(ChatMessage.objects.filter(client_message_id=client_id).count(), 1)
                saved = ChatMessage.objects.get(pk=first.data['data']['id'])
                with saved.image.open('rb') as handle:
                    self.assertEqual(handle.read(), data)
                conflict = self.upload(staff=staff, data=image_bytes('PNG', (50, 60, 70)), client_id=client_id)
                self.assertEqual((conflict.status_code, conflict.data['code']), (409, 409), conflict.data)
                self.assertEqual(ChatMessage.objects.filter(client_message_id=client_id).count(), 1)


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated store databases')
@override_settings(**CHAT_OVERRIDES)
class ChatReadImageStoreBoundaryTest(APITestCase):
    databases = {'default', 'store_b'}

    def test_same_ids_keep_read_state_and_private_images_in_their_original_store(self):
        original = resolve_store()
        branch = Store.objects.create(slug='xinan', name='兴安聊天隔离', database_alias='store_b',
                                      status=Store.Status.ACTIVE)
        clients = {}
        message_ids = {}
        media = TemporaryDirectory(prefix='xa-chat-two-store-images-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        for store in (original, branch):
            with store_context(store), override_settings(MEDIA_ROOT=media.name):
                boss = ClubAccount.objects.create_account(pk=94121, username='same-image-boss',
                                                          account_type='BOSS')
                other = ClubAccount.objects.create_account(pk=94122, username='same-image-other',
                                                           account_type='BOSS')
                session = ChatSession.objects.create(pk=94123, account=boss, unread_user=1,
                                                     last_message=store.slug)
                support = ChatMessage.objects.create(pk=94124, session=session, is_from_support=True,
                                                      content=f'{store.slug} 客服消息')
                data = image_bytes('PNG', (10, 20, 30) if store == original else (40, 50, 60))
                picture = ChatMessage.objects.create(pk=94125, session=session, sender_account=boss,
                    content_type=ChatMessage.ContentType.IMAGE, content=store.slug,
                    image=SimpleUploadedFile('proof.png', data, content_type='image/png'))
                client = APIClient(HTTP_HOST=STORE_HOSTS[store.slug])
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss)[0])
                other_client = APIClient(HTTP_HOST=STORE_HOSTS[store.slug])
                other_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(other)[0])
                payload = client.get('/api/chat/session/').data['data']
                image_row = next(row for row in payload['messages'] if row['id'] == picture.pk)
                clients[store.slug] = (client, other_client, image_row['image_url'], data)
                message_ids[store.slug] = support.pk

        first_client, first_other, first_url, first_data = clients['shenglang']
        response = first_client.post('/api/chat/read/', {'through_id': message_ids['shenglang']}, format='json')
        self.assertEqual(response.data['data']['unread_user'], 0)
        with store_context(branch):
            self.assertFalse(ChatMessage.objects.get(pk=message_ids['xinan']).is_read)
        with store_context(original):
            self.assertTrue(ChatMessage.objects.get(pk=message_ids['shenglang']).is_read)

        for slug, (client, other_client, url, data) in clients.items():
            own = client.get(urlsplit(url).path)
            self.assertEqual(own.status_code, 200)
            self.assertEqual(b''.join(own.streaming_content), data)
            own.close()
            other_session = other_client.get('/api/chat/session/')
            self.assertEqual(other_session.data['data']['messages'], [])
            ticket = urlsplit(url).path.rstrip('/').rsplit('/', 1)[-1]
            denied = other_client.post('/api/media/renew/', {'ticket': ticket}, format='json')
            self.assertEqual(denied.status_code, 404)
            other_store = branch.slug if slug == 'shenglang' else original.slug
            cross_store = APIClient(HTTP_HOST=STORE_HOSTS[other_store])
            denied = cross_store.get(urlsplit(url).path)
            self.assertEqual(denied.status_code, 404)

        second_client = clients['xinan'][0]
        response = second_client.post('/api/chat/read/', {'through_id': message_ids['xinan']}, format='json')
        self.assertEqual(response.data['data']['unread_user'], 0)
