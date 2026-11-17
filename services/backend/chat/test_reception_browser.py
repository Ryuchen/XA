from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from chat.models import ChatMessage, ChatSession
from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user
from stores.context import resolve_store, store_context


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class ReceptionBrowserTest(LiveServerTestCase):
    def test_claim_and_transfer_hide_messages_from_other_staff(self):
        self.enterContext(store_context(resolve_store()))
        permissions = ['chat:view', 'chat:reply']
        clients = []
        for _ in range(2):
            user = make_console_user(permissions)
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            user.admin_membership.account = account
            user.admin_membership.save(update_fields=['account'])
            client = APIClient(HTTP_HOST='admin.esports.club')
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            clients.append((account, client))
        (a, ac), (b, bc) = clients
        boss = ClubAccount.objects.create_account(username='reception-browser-boss', account_type='BOSS')
        session = ChatSession.objects.create(account=boss, last_message='隔离咨询内容')
        ChatMessage.objects.create(session=session, sender_account=boss, content='隔离咨询内容')
        path = f'/api/admin/chat-sessions/{session.pk}/'
        self.assertEqual(ac.get(path + 'messages/').status_code, 403)
        self.assertEqual(ac.post(path + 'claim/', {'expected_version': 0}, format='json').data['code'], 0)
        self.assertEqual(ac.get(path + 'messages/').data['data']['list'][0]['content'], '隔离咨询内容')
        self.assertEqual(bc.get(path + 'messages/').status_code, 403)
        self.assertEqual(ac.post(path + 'transfer/', {'expected_version': 1, 'account_id': b.pk}, format='json').data['code'], 0)
        self.assertEqual(ac.get(path + 'messages/').status_code, 403)
        self.assertEqual(bc.get(path + 'messages/').data['data']['list'][0]['content'], '隔离咨询内容')