from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from club_accounts.models import ClubAccount, LegacyAccountMap
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership
from orders.tests.factories import make_console_user, make_user
from site_messages.models import Message


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class MessageRecipientWorkflowTest(APITestCase):
    def setUp(self):
        staff = make_console_user(['message:view', 'message:push'])
        admin = get_or_create_account_for_legacy_user(staff)
        AdminMembership.objects.filter(user=staff).update(account=admin)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(admin)[0])
        self.first = make_user(nickname='同名测试账号')
        self.second = make_user(nickname='同名测试账号')
        self.account = get_or_create_account_for_legacy_user(self.first)
        self.other = get_or_create_account_for_legacy_user(self.second)
        link_legacy_relations(self.first, self.account)
        link_legacy_relations(self.second, self.other)
        self.colliding_user = make_user(pk=45678)
        self.unmapped = ClubAccount.objects.create(pk=45678, username='unmapped_synthetic', nickname='同名测试账号', account_type='BOSS')

    def test_real_recipient_search_delivery_and_read(self):
        result = self.client.get('/api/admin/options/message-recipients/', {'keyword': '同名测试账号', 'page_size': 100})
        options = {item['id']: item for item in result.data['data']['list']}
        self.assertTrue(options[self.account.pk]['recipient_mapped'])
        self.assertTrue(options[self.other.pk]['recipient_mapped'])
        self.assertFalse(options[self.unmapped.pk]['recipient_mapped'])
        sent = self.client.post('/api/admin/messages/push/', {'audience': 'CUSTOM', 'recipient_account_ids': [self.account.pk],
                                'title': '隔离核对通知', 'preview': '摘要', 'detail': '完整内容'}, format='json')
        self.assertEqual(sent.data['data']['count'], 1)
        msg = Message.objects.get(title='隔离核对通知')
        self.assertEqual((msg.recipient_id, msg.recipient_account_id), (self.first.pk, self.account.pk))
        self.assertFalse(Message.objects.filter(recipient_id=self.second.pk, title='隔离核对通知').exists())
        listed = self.client.get('/api/admin/messages/')
        row = next(item for item in listed.data['data']['list'] if item['id'] == msg.pk)
        self.assertEqual((row['recipient_no'], row['recipient_username'], row['is_read']), (self.account.account_no, self.account.username, False))
        buyer = APIClient()
        buyer.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
        opened = buyer.get(f'/api/messages/{msg.pk}/')
        self.assertEqual(opened.data['code'], 0, opened.data)
        msg.refresh_from_db()
        self.assertTrue(msg.is_read)

    def test_unmapped_collision_or_disabled_account_never_partially_sends(self):
        response = self.client.post('/api/admin/messages/push/', {'title': '不得发送',
            'recipient_account_ids': [self.account.pk, self.unmapped.pk]}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Message.objects.filter(title='不得发送').exists())
        self.assertFalse(LegacyAccountMap.objects.filter(account=self.unmapped).exists())
        self.account.can_login = False
        self.account.save(update_fields=['can_login'])
        response = self.client.post('/api/admin/messages/push/', {'title': '不得发送',
            'recipient_account_ids': [self.account.pk]}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Message.objects.filter(title='不得发送').exists())

    def test_without_push_permission_cannot_search_or_send(self):
        staff = make_console_user(['message:view'])
        account = get_or_create_account_for_legacy_user(staff)
        AdminMembership.objects.filter(user=staff).update(account=account)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        self.assertEqual(client.get('/api/admin/options/message-recipients/', {'keyword': '同名'}).status_code, 403)
        self.assertEqual(client.post('/api/admin/messages/push/', {'title': '不得发送', 'recipient_account_ids': [self.account.pk]}, format='json').status_code, 403)
