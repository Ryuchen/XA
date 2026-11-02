"""A provider can read older notifications while new messages arrive."""
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership
from orders.tests.factories import make_console_user, make_provider
from site_messages.models import Message


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class MessagePaginationWorkflowTest(APITestCase):
    def setUp(self):
        self.provider = make_provider()
        self.account = get_or_create_account_for_legacy_user(self.provider)
        link_legacy_relations(self.provider, self.account)
        self.reader = APIClient()
        self.reader.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
        staff = make_console_user(['message:view', 'message:push'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        AdminMembership.objects.filter(user=staff).update(account=staff_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])

    def test_provider_opens_older_messages_after_new_admin_notice(self):
        Message.objects.bulk_create([
            Message(recipient=self.provider, recipient_account=self.account, type='ORDER', title=f'订单通知 {n}')
            for n in range(55)
        ])
        # Equal timestamps still have a stable ordering by primary key.
        Message.objects.filter(recipient_account=self.account).update(created_at=timezone.now())
        first = self.reader.get('/api/messages/', {'page_size': 50}).data['data']
        self.assertEqual((len(first['messages']), first['total'], first['unread'], first['has_more']), (50, 55, 55, True))
        sent = self.client.post('/api/admin/messages/push/', {
            'audience': 'CUSTOM', 'recipient_account_ids': [self.account.pk],
            'title': '新订单提醒', 'preview': '新的待处理订单',
        }, format='json')
        self.assertEqual(sent.data['code'], 0, sent.data)
        older = self.reader.get('/api/messages/', {'page_size': 50, 'before': first['next_before']}).data['data']
        ids = [item['id'] for item in first['messages'] + older['messages']]
        self.assertEqual((len(ids), len(set(ids)), older['total'], older['has_more']), (55, 55, 56, False))
        opened = self.reader.get(f"/api/messages/{older['messages'][-1]['id']}/")
        self.assertEqual(opened.data['code'], 0, opened.data)
        self.assertEqual(self.reader.get('/api/messages/unread-count/').data['data']['unread'], 55)
        latest = self.reader.get('/api/messages/', {'page_size': 50}).data['data']
        self.assertEqual(latest['messages'][0]['title'], '新订单提醒')
        legacy = self.reader.get('/api/messages/', {'page_size': 50, 'page': 2}).data['data']
        self.assertEqual(len(legacy['messages']), 6)
