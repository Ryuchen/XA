"""Native chat only marks messages actually loaded, even with backdated history."""
from datetime import timedelta

from django.test import LiveServerTestCase, override_settings
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from chat.models import ChatMessage
from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user
from stores.context import resolve_store, store_context


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class BossChatReadScopeHTTPTest(LiveServerTestCase):
    def test_backdated_high_id_stays_unread_until_loaded(self):
        self.run_scope(51, True)

    def test_loaded_history_over_200_messages_is_acknowledged_in_batches(self):
        self.run_scope(205, False)

    def run_scope(self, count, backdate):
        self.enterContext(store_context(resolve_store()))
        boss = ClubAccount.objects.create_account(username='read-scope-boss', account_type='BOSS')
        user = make_console_user(['chat:view', 'chat:reply'])
        staff = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = staff
        user.admin_membership.save(update_fields=['account'])
        token, refresh = issue_account_tokens(boss)
        customer = APIClient(HTTP_HOST='sl.esports.club')
        customer.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        session = customer.get('/api/chat/session/').data['data']['session']
        admin = APIClient(HTTP_HOST='admin.esports.club')
        admin.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
        self.assertEqual(admin.post(f"/api/admin/chat-sessions/{session['id']}/claim/", {'expected_version': 0}, format='json').data['code'], 0)
        ids = []
        for number in range(count):
            result = admin.post(f"/api/admin/chat-sessions/{session['id']}/reply/", {'content': f'隔离客服回复 {number}'}, format='json').data
            self.assertEqual(result['code'], 0, result)
            ids.append(result['data']['id'])
        backdated = ids[-2] if backdate else ids[0]
        if backdate:
            ChatMessage.objects.filter(pk=backdated).update(created_at=timezone.now() - timedelta(days=1))
        first = customer.get('/api/chat/session/').data['data']
        loaded = [row['id'] for row in first['messages']]
        self.assertEqual(len(loaded), 50)
        self.assertTrue(first['has_more'])
        self.assertNotIn(backdated, loaded)
        self.assertEqual(customer.post('/api/chat/read/', {'message_ids': loaded}, format='json').data['data']['unread_user'], count - 50)
        self.assertEqual(customer.get('/api/chat/session/').data['data']['session']['unread_user'], count - 50)
        latest = customer.get('/api/chat/session/').data['data']
        customer.post('/api/chat/read/', {'message_ids': [row['id'] for row in latest['messages']]}, format='json')
        self.assertEqual(customer.get('/api/chat/session/').data['data']['session']['unread_user'], count - 50)
        before = first['next_before_id']
        while before is not None:
            older = customer.get('/api/chat/session/', {'before_id': before}).data['data']
            loaded.extend(row['id'] for row in older['messages'])
            before = older['next_before_id']
        self.assertIn(backdated, loaded)
        batches = [loaded[start:start + 200] for start in range(0, len(loaded), 200)]
        for batch in batches:
            customer.post('/api/chat/read/', {'message_ids': batch}, format='json')
        self.assertTrue(all(len(batch) <= 200 for batch in batches))
        if count > 200:
            self.assertTrue(any(len(batch) == 200 for batch in batches))
        self.assertEqual(customer.get('/api/chat/session/').data['data']['session']['unread_user'], 0)


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class BossChatReadScopeTest(TestCase):
    def test_ids_are_bounded_to_own_session_and_legacy_through_still_works(self):
        self.enterContext(store_context(resolve_store()))
        boss = ClubAccount.objects.create_account(username='read-scope-owner', account_type='BOSS')
        other = ClubAccount.objects.create_account(username='read-scope-other', account_type='BOSS')
        own = APIClient(HTTP_HOST='sl.esports.club'); own.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss)[0])
        stranger = APIClient(HTTP_HOST='sl.esports.club'); stranger.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(other)[0])
        own_session = own.get('/api/chat/session/').data['data']['session']['id']
        other_session = stranger.get('/api/chat/session/').data['data']['session']['id']
        own_message = ChatMessage.objects.create(session_id=own_session, is_from_support=True, content='本人的客服回复')
        other_message = ChatMessage.objects.create(session_id=other_session, is_from_support=True, content='另一账号的客服回复')
        result = own.post('/api/chat/read/', {'message_ids': [other_message.pk]}, format='json')
        self.assertEqual(result.data['code'], 0)
        other_message.refresh_from_db(); own_message.refresh_from_db()
        self.assertFalse(other_message.is_read)
        self.assertFalse(own_message.is_read)
        self.assertEqual(own.post('/api/chat/read/', {'message_ids': list(range(1, 202))}, format='json').status_code, 400)
        self.assertEqual(own.post('/api/chat/read/', {'message_ids': [own_message.pk], 'through_id': own_message.pk}, format='json').status_code, 400)
        self.assertEqual(own.post('/api/chat/read/', {'through_id': own_message.pk}, format='json').data['code'], 0)
        own_message.refresh_from_db()
        self.assertTrue(own_message.is_read)