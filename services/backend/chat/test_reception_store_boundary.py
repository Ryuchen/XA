from unittest import skipUnless
from django.conf import settings
from django.test import override_settings
from rest_framework.test import APITestCase, APIClient
from chat.models import ChatAssignmentEvent, ChatMessage, ChatSession
from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_order, make_service
from stores.context import resolve_store, store_context
from stores.models import Store


STORE_HOSTS = {'shenglang': 'sl.esports.club', 'xinan': 'xa.esports.club'}


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
@skipUnless('store_b' in settings.DATABASES, 'requires two isolated store databases')
class ReceptionStoreBoundaryTest(APITestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_same_session_assignment_history_is_scoped_to_each_store(self):
        original = resolve_store()
        branch = Store.objects.create(slug='xinan', name='兴安测试', database_alias='store_b', status=Store.Status.ACTIVE)
        clients = {}
        for store in (original, branch):
            with store_context(store):
                user = make_console_user(['chat:view', 'chat:reply', 'chat:supervise'])
                staff = get_or_create_account_for_legacy_user(user)
                user.admin_membership.account = staff
                user.admin_membership.save(update_fields=['account'])
                boss = ClubAccount.objects.create_account(username='history-boss', account_type='BOSS')
                ChatSession.objects.create(pk=87123, account=boss)
                client = APIClient(HTTP_HOST=STORE_HOSTS[store.slug])
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
                clients[store.slug] = client
                result = client.post('/api/admin/chat-sessions/87123/claim/',
                                     {'expected_version': 0, 'reason': store.slug}, format='json')
                self.assertEqual(result.status_code, 200, getattr(result, 'data', result.content))
        for store in (original, branch):
            with store_context(store):
                response = clients[store.slug].get('/api/admin/chat-sessions/87123/assignment-history/')
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual([row['reason'] for row in response.data['data']['list']], [store.slug])

    def test_same_session_id_cannot_cross_store(self):
        original = resolve_store()
        branch = Store.objects.create(slug='xinan', name='兴安测试', database_alias='store_b', status=Store.Status.ACTIVE)
        clients = {}
        for store in (original, branch):
            with store_context(store):
                user = make_console_user(['chat:view', 'chat:reply'])
                staff = get_or_create_account_for_legacy_user(user)
                user.admin_membership.account = staff
                user.admin_membership.save(update_fields=['account'])
                boss = ClubAccount.objects.create_account(username='same-boss', account_type='BOSS')
                session = ChatSession.objects.create(pk=87123, account=boss, last_message=store.slug)
                ChatMessage.objects.create(session=session, sender_account=boss, content=store.slug)
                token = issue_account_tokens(staff)[0]
                client = APIClient(HTTP_HOST=STORE_HOSTS[store.slug])
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                clients[store.slug] = client
        with store_context(branch):
            response = clients[branch.slug].post('/api/admin/chat-sessions/87123/claim/', {'expected_version': 0}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            response = clients[branch.slug].get('/api/admin/chat-sessions/87123/messages/')
            self.assertEqual([row['content'] for row in response.data['data']['list']], ['xinan'])
        with store_context(original):
            session = ChatSession.objects.get(pk=87123)
            self.assertIsNone(session.claimed_by_id)
            self.assertEqual(session.claim_version, 0)
            self.assertFalse(ChatAssignmentEvent.objects.filter(session=session).exists())
            self.assertEqual(clients[original.slug].get('/api/admin/chat-sessions/87123/messages/').status_code, 403)

    def test_same_order_number_and_account_id_use_current_store(self):
        original = resolve_store()
        branch = Store.objects.create(slug='xinan', name='兴安测试', database_alias='store_b', status=Store.Status.ACTIVE)
        clients = {}
        for store in (original, branch):
            with store_context(store):
                boss = ClubAccount.objects.create_account(pk=88121, username='same-card-boss', account_type='BOSS')
                service = make_service(name=f'{store.slug}-服务')
                make_order(None, service=service, customer_account=boss, pk=88122, order_no='ORD' + 'A' * 20)
                token = issue_account_tokens(boss)[0]
                client = APIClient(HTTP_HOST=STORE_HOSTS[store.slug])
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                clients[store.slug] = client
        with store_context(branch):
            wrong_store = clients[original.slug].post('/api/chat/send/', {
                'content': '错店咨询', 'order_ref': 'ORD' + 'A' * 20, 'client_message_id': 'wrong-store-chat',
            }, HTTP_HOST=STORE_HOSTS[branch.slug], format='json')
            self.assertEqual(wrong_store.status_code, 401)
            self.assertFalse(ChatMessage.objects.filter(related_order_id=88122).exists())
            result = clients[branch.slug].post('/api/chat/send/', {'content': '兴安咨询', 'order_ref': 'ORD' + 'A' * 20, 'client_message_id': 'same-chat-retry'}, format='json')
            self.assertEqual(result.status_code, 200, result.data)
            self.assertEqual(result.data['data']['order_card']['service_name'], 'xinan-服务')
        with store_context(original):
            self.assertFalse(ChatMessage.objects.filter(related_order_id=88122).exists())
            payload = {'content': '声浪咨询', 'order_ref': 'ORD' + 'A' * 20, 'client_message_id': 'same-chat-retry'}
            result = clients[original.slug].post('/api/chat/send/', payload, format='json')
            self.assertEqual(result.status_code, 200, result.data)
            self.assertEqual(result.data['data']['order_card']['service_name'], 'shenglang-服务')
            replay = clients[original.slug].post('/api/chat/send/', payload, format='json')
            self.assertEqual(replay.data['data']['id'], result.data['data']['id'])
        with store_context(branch):
            replay = clients[branch.slug].post('/api/chat/send/', {'content': '兴安咨询', 'order_ref': 'ORD' + 'A' * 20, 'client_message_id': 'same-chat-retry'}, format='json')
            self.assertEqual(replay.data['data']['order_card']['service_name'], 'xinan-服务')
            self.assertEqual(ChatMessage.objects.count(), 1)
