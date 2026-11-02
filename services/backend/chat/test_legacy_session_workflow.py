"""Historical support sessions remain usable after business account migration."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import close_old_connections
from django.test import override_settings, skipUnlessDBFeature
from rest_framework.test import APIClient, APITransactionTestCase

from chat.models import ChatMessage, ChatSession
from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class LegacySupportSessionTest(APITransactionTestCase):
    def client_for(self, account):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        return client

    def fixture(self):
        legacy = make_user()
        account = get_or_create_account_for_legacy_user(legacy)
        session = ChatSession.objects.create(user=legacy, last_message='历史回复', unread_user=1)
        message = ChatMessage.objects.create(session=session, is_from_support=True, content='历史回复')
        return legacy, account, session, message

    def test_history_is_preserved_on_session_send_and_read_entrypoints(self):
        for entry in ('session', 'send', 'read'):
            with self.subTest(entry=entry):
                legacy, account, session, message = self.fixture()
                client = self.client_for(account)
                if entry == 'session':
                    response = client.get('/api/chat/session/')
                elif entry == 'send':
                    response = client.post('/api/chat/send/', {'content':'继续咨询'}, format='json')
                else:
                    response = client.post('/api/chat/read/', {'through_id':message.pk}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                session.refresh_from_db()
                self.assertEqual(session.account_id, account.pk)
                self.assertEqual(session.user_id, legacy.pk)
                current = client.get('/api/chat/session/').data['data']
                self.assertEqual(current['session']['id'], session.pk)
                self.assertEqual(current['messages'][0]['content'], '历史回复')
                self.assertEqual(current['messages'][0]['is_read'], entry == 'read')
                self.assertEqual(current['session']['unread_user'], 0 if entry == 'read' else 1)

    def test_does_not_reassign_session_bound_to_another_account(self):
        legacy, account, old_session, _ = self.fixture()
        other = get_or_create_account_for_legacy_user(make_user())
        old_session.account = other
        old_session.save(update_fields=['account'])
        result = self.client_for(account).get('/api/chat/session/')
        self.assertEqual(result.data['code'], 0, result.data)
        self.assertNotEqual(result.data['data']['session']['id'], old_session.pk)
        self.assertEqual(result.data['data']['messages'], [])
        old_session.refresh_from_db()
        self.assertEqual(old_session.account_id, other.pk)
        self.assertEqual(old_session.user_id, legacy.pk)

    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_first_sends_share_history_without_duplicate_session(self):
        _, account, session, _ = self.fixture()
        token = issue_account_tokens(account)[0]
        barrier = Barrier(2)

        def send(content):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                barrier.wait(timeout=10)
                return client.post('/api/chat/send/', {'content':content}, format='json').data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, ['并发咨询一', '并发咨询二']))
        self.assertTrue(all(result['code'] == 0 for result in results), results)
        session.refresh_from_db()
        self.assertEqual(session.account_id, account.pk)
        self.assertEqual(ChatSession.objects.filter(account=account).count(), 1)
        self.assertEqual(session.unread_support, 2)
        self.assertEqual(set(session.messages.values_list('content', flat=True)), {'历史回复', '并发咨询一', '并发咨询二'})

    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_new_account_sends_create_exactly_one_session(self):
        account = ClubAccount.objects.create_account(username='new-chat-concurrent', account_type='BOSS')
        token = issue_account_tokens(account)[0]
        barrier = Barrier(2)

        def send(content):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                barrier.wait(timeout=10)
                return client.post('/api/chat/send/', {'content':content}, format='json').data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, ['首次咨询一', '首次咨询二']))
        self.assertTrue(all(result['code'] == 0 for result in results), results)
        session = ChatSession.objects.get(account=account)
        self.assertIsNone(session.user_id)
        self.assertEqual(session.unread_support, 2)
        self.assertEqual(session.messages.count(), 2)
