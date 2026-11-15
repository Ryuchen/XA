from datetime import timedelta
from unittest.mock import Mock, patch
from unittest import skipUnless
from types import SimpleNamespace

from django.conf import settings
from django.db import DatabaseError, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from chat.models import ChatMessage, ChatNotificationOutbox, ChatSession
from chat.tasks import drain_chat_notifications
from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user


class Layer:
    def __init__(self, fail=()):
        self.fail = set(fail)
        self.calls = []

    async def group_send(self, group, event):
        self.calls.append((group, event))
        if group in self.fail:
            raise ConnectionError('isolated test failure')


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, CHAT_NOTIFICATION_OUTBOX_ENABLED=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ChatOutboxTest(TransactionTestCase):
    def setUp(self):
        self.boss = ClubAccount.objects.create_account(username='outbox-boss', account_type='BOSS')
        self.boss_client = APIClient()
        self.boss_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.boss)[0])
        user = make_console_user(['chat:view', 'chat:reply'])
        self.staff = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = self.staff; user.admin_membership.save(update_fields=['account'])
        self.staff_client = APIClient()
        self.staff_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.staff)[0])
        self.session = ChatSession.objects.create(account=self.boss)
        self.session.claimed_by = self.staff; self.session.save(update_fields=['claimed_by'])

    def test_partial_channel_failure_retries_only_missing_destination(self):
        layer = Layer(fail={f'account_{self.boss.pk}'})
        payload = {'content': '咨询', 'client_message_id': 'chat-outbox-001'}
        with patch('chat.notifier.get_channel_layer', return_value=layer):
            response = self.boss_client.post('/api/chat/send/', payload, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            row = ChatNotificationOutbox.objects.get()
            self.assertEqual((row.attempts, row.last_error), (1, 'CHANNEL_UNAVAILABLE'))
            self.assertIsNotNone(row.operators_sent_at)
            self.assertIsNotNone(row.session_sent_at)
            self.assertIsNone(row.account_sent_at)
            self.assertIsNone(row.completed_at)
            self.assertTrue(all('content' not in str(event) for _, event in layer.calls))
            replay = self.boss_client.post('/api/chat/send/', payload, format='json')
            self.assertEqual(replay.data['data']['id'], response.data['data']['id'])
            self.assertEqual(ChatNotificationOutbox.objects.count(), 1)
            self.assertEqual(drain_chat_notifications.run(), 0)
            ChatNotificationOutbox.objects.filter(pk=row.pk).update(next_attempt_at=timezone.now()-timedelta(seconds=1))
            layer.fail.clear(); layer.calls.clear()
            self.assertEqual(drain_chat_notifications.run(), 1)
            self.assertEqual([group for group, _ in layer.calls], [f'account_{self.boss.pk}'])
            row.refresh_from_db()
            self.assertIsNotNone(row.completed_at)
            self.assertEqual(row.attempts, 2)
            self.assertEqual(drain_chat_notifications.run(), 0)
        self.session.refresh_from_db()
        self.assertEqual((ChatMessage.objects.count(), self.session.unread_support), (1, 1))

    def test_notification_waits_for_commit_and_rollback_discards_it(self):
        layer = Layer()
        with patch('chat.notifier.get_channel_layer', return_value=layer):
            with transaction.atomic():
                response = self.boss_client.post('/api/chat/send/', {'content': '将回滚', 'client_message_id': 'chat-rollback-01'}, format='json')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(ChatNotificationOutbox.objects.count(), 1)
                self.assertEqual(layer.calls, [])
                transaction.set_rollback(True)
            self.assertFalse(ChatMessage.objects.exists())
            self.assertFalse(ChatNotificationOutbox.objects.exists())
            self.assertEqual(layer.calls, [])

    def test_outbox_write_failure_rolls_back_message_and_unread(self):
        with patch('chat.models.ChatNotificationOutbox.objects.create', side_effect=DatabaseError('test failure')):
            with self.assertRaises(DatabaseError):
                self.boss_client.post('/api/chat/send/', {'content': '写入失败', 'client_message_id': 'outbox-write-fail'}, format='json')
        self.session.refresh_from_db()
        self.assertEqual(self.session.unread_support, 0)
        self.assertFalse(ChatMessage.objects.exists())
        self.assertFalse(ChatNotificationOutbox.objects.exists())

    def test_staff_reply_persists_when_notification_layer_is_down(self):
        layer = Layer(fail={'operators', f'account_{self.boss.pk}'})
        with patch('chat.notifier.get_channel_layer', return_value=layer):
            response = self.staff_client.post(f'/api/admin/chat-sessions/{self.session.pk}/reply/',
                {'content': '客服回复', 'client_message_id': 'staff-outbox-01'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(ChatMessage.objects.get().content, '客服回复')
        self.session.refresh_from_db(); self.assertEqual(self.session.unread_user, 1)
        self.assertEqual(ChatNotificationOutbox.objects.get().last_error, 'CHANNEL_UNAVAILABLE')

    def test_retry_uses_current_reception_permissions(self):
        from chat.notifier import deliver_chat_notification
        from orders.consumers import current_chat_payload
        layer = Layer(fail={'operators', f'account_{self.boss.pk}'})
        with patch('chat.notifier.get_channel_layer', return_value=layer):
            sent = self.boss_client.post('/api/chat/send/', {'content': '私密咨询', 'client_message_id': 'outbox-private-01'}, format='json')
            self.assertEqual(sent.status_code, 200, sent.data)
        replacement_user = make_console_user(['chat:view', 'chat:reply'])
        replacement = get_or_create_account_for_legacy_user(replacement_user)
        replacement_user.admin_membership.account = replacement
        replacement_user.admin_membership.save(update_fields=['account'])
        moved = self.staff_client.post(f'/api/admin/chat-sessions/{self.session.pk}/transfer/',
            {'expected_version': 0, 'account_id': replacement.pk}, format='json')
        self.assertEqual(moved.status_code, 200, moved.data)
        layer.fail.clear(); layer.calls.clear()
        with patch('chat.notifier.get_channel_layer', return_value=layer):
            self.assertTrue(deliver_chat_notification(ChatNotificationOutbox.objects.get().pk))
        operator_event = next(event for group, event in layer.calls if group == 'operators' and event['type'] == 'chat_message')
        self.assertIsNone(current_chat_payload.func(self.staff, operator_event['data'], message=True))
        self.assertEqual(current_chat_payload.func(replacement, operator_event['data'], message=True)['message']['content'], '私密咨询')

    @override_settings(CHAT_NOTIFICATION_OUTBOX_ENABLED=False)
    def test_legacy_delivery_error_does_not_turn_committed_message_into_error(self):
        with patch('chat.notifier.notify_chat_message', side_effect=ConnectionError('test failure')):
            response = self.boss_client.post('/api/chat/send/', {'content': '已保存', 'client_message_id': 'legacy-chat-001'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(ChatMessage.objects.count(), 1)
        self.assertFalse(ChatNotificationOutbox.objects.exists())


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated store databases')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, CHAT_NOTIFICATION_OUTBOX_ENABLED=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ChatOutboxMultiStoreTest(TransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_retry_uses_only_current_store_even_for_same_ids(self):
        from stores.context import current_store, resolve_store, store_context
        from stores.tests_hosts import xinan_store
        first, second = resolve_store(), xinan_store()
        class StoreLayer(Layer):
            async def group_send(self, group, event):
                self.calls.append((current_store().slug, group))
                if self.fail:
                    raise ConnectionError('isolated failure')
        layer = StoreLayer(fail={'all'})
        with patch('chat.notifier.get_channel_layer', return_value=layer):
            for store in (first, second):
                with store_context(store):
                    account = ClubAccount.objects.create_account(pk=77121, username='same-outbox-boss', account_type='BOSS')
                    ChatSession.objects.create(pk=77122, account=account)
                    client = APIClient(HTTP_HOST='sl.esports.club' if store == first else 'xa.esports.club')
                    client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                    response = client.post('/api/chat/send/', {'content': store.slug, 'client_message_id': 'same-outbox-key'}, format='json')
                    self.assertEqual(response.status_code, 200, response.data)
                    ChatNotificationOutbox.objects.update(next_attempt_at=timezone.now()-timedelta(seconds=1))
            layer.fail.clear(); layer.calls.clear()
            with store_context(first):
                self.assertEqual(drain_chat_notifications.run(), 1)
                self.assertIsNotNone(ChatNotificationOutbox.objects.get().completed_at)
            self.assertEqual({store for store, _ in layer.calls}, {'shenglang'})
            with store_context(second):
                self.assertIsNone(ChatNotificationOutbox.objects.get().completed_at)
                layer.calls.clear()
                self.assertEqual(drain_chat_notifications.run(), 1)
                self.assertIsNotNone(ChatNotificationOutbox.objects.get().completed_at)
            self.assertEqual({store for store, _ in layer.calls}, {'xinan'})

    def test_periodic_worker_fans_out_by_store(self):
        from config.celery import app
        from stores.tasks import StoreTask
        from stores.tests_hosts import xinan_store
        xinan_store()
        self.assertEqual(settings.CELERY_BEAT_SCHEDULE['drain-chat-notifications']['task'],
                         'chat.tasks.drain_chat_notifications')
        task = StoreTask(); task.bind(app)
        task.name = 'chat.tasks.drain_chat_notifications'
        task.apply_async = Mock(return_value=SimpleNamespace(id='isolated'))
        task.run = Mock()
        task.push_request(headers={}, called_directly=False)
        try:
            result = task()
            self.assertEqual({item['store'] for item in result['stores']}, {'shenglang', 'xinan'})
            self.assertEqual({call.kwargs['headers']['store_slug'] for call in task.apply_async.call_args_list},
                             {'shenglang', 'xinan'})
            task.run.assert_not_called()
        finally:
            task.pop_request()
