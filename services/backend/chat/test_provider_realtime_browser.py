"""Provider unread recovers after staff replies while the provider is away."""
from django.test import override_settings
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider
from stores.context import resolve_store, store_context


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ProviderChatRealtimeBrowserTest(APITransactionTestCase):
    def test_staff_reply_visible_and_read_then_recovered_after_leaving(self):
        self.enterContext(store_context(resolve_store()))
        provider, staff = make_provider(), make_console_user(['chat:view', 'chat:reply'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (provider, staff)]
        for user, account in zip((provider, staff), accounts):
            link_legacy_relations(user, account)
        staff.admin_membership.account = accounts[1]
        staff.admin_membership.save(update_fields=['account'])
        tokens = [issue_account_tokens(account)[0] for account in accounts]
        client = APIClient(HTTP_HOST='sl.esports.club')
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
        session = client.get('/api/chat/session/').data
        self.assertEqual(session['code'], 0, session)
        session_id = session['data']['session']['id']
        admin = APIClient(HTTP_HOST='admin.esports.club')
        admin.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[1])
        self.assertEqual(admin.post(f'/api/admin/chat-sessions/{session_id}/claim/', {'expected_version': 0}, format='json').data['code'], 0)

        def read_through():
            messages = client.get('/api/chat/session/').data['data']['messages']
            self.assertEqual(client.post('/api/chat/read/', {'through_id': max(row['id'] for row in messages)}, format='json').data['code'], 0)

        self.assertEqual(admin.post(f'/api/admin/chat-sessions/{session_id}/reply/', {'content': '隔离客服实时回复'}, format='json').data['code'], 0)
        self.assertEqual(client.get('/api/chat/unread/').data['data']['unread'], 1)
        read_through()
        self.assertEqual(client.get('/api/chat/unread/').data['data']['unread'], 0)
        self.assertEqual(admin.post(f'/api/admin/chat-sessions/{session_id}/reply/', {'content': '隔离客服离开后回复'}, format='json').data['code'], 0)
        self.assertEqual(client.get('/api/chat/unread/').data['data']['unread'], 1)
        returned = client.get('/api/chat/session/').data['data']['messages']
        self.assertTrue(any(row['content'] == '隔离客服离开后回复' for row in returned))
        read_through()
        self.assertEqual(client.get('/api/chat/unread/').data['data']['unread'], 0)