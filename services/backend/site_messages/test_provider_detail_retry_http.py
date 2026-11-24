"""Staff sends a real isolated notification; provider recovers from a dropped read."""
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership
from orders.tests.factories import make_console_user, make_provider
from site_messages.models import Message
from stores.context import resolve_store, store_context


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ProviderDetailRetryHTTPTest(LiveServerTestCase):
    def test_staff_push_and_provider_retries_detail(self):
        self.enterContext(store_context(resolve_store()))
        provider, staff = make_provider(), make_console_user(['message:view', 'message:push'])
        account, operator = [get_or_create_account_for_legacy_user(user) for user in (provider, staff)]
        for user, linked in ((provider, account), (staff, operator)):
            link_legacy_relations(user, linked)
        AdminMembership.objects.filter(user=staff).update(account=operator)
        client = APIClient(HTTP_HOST='admin.esports.club')
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(operator)[0])
        response = client.post('/api/admin/messages/push/', {'recipient_account_ids': [account.pk],
            'title': '隔离通知验收', 'preview': '请查看消息'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        message = Message.objects.get(recipient_account=account, title='隔离通知验收')
        reader = APIClient(HTTP_HOST='sl.esports.club')
        reader.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        listed = reader.get('/api/messages/').data
        self.assertEqual(listed['code'], 0, listed)
        self.assertFalse(next(row for row in listed['data']['messages'] if row['id'] == message.pk)['is_read'])
        detail = reader.get(f'/api/messages/{message.pk}/').data
        self.assertEqual(detail['code'], 0, detail)
        self.assertEqual(detail['data']['title'], '隔离通知验收')
        message.refresh_from_db()
        self.assertTrue(message.is_read)