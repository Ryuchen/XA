"""Account-only inboxes for unmapped current identities."""
import os

from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_user
from site_messages.models import Message


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class MessageAccountOnlyLiveTest(LiveServerTestCase):
    def test_admin_sends_to_current_boss_and_provider(self):
        root = os.environ.get('XA_ADMIN_MESSAGE_ROOT')
        provider_root = os.environ.get('XA_PROVIDER_MESSAGE_ROOT')
        if not root or not provider_root:
            self.skipTest('requires current isolated admin and provider builds')
        staff = make_console_user(['message:view', 'message:push'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        link_legacy_relations(staff, staff_account)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        boss = ClubAccount.objects.create_account(username='isolated-current-boss', nickname='隔离同名', account_type='BOSS')
        provider = ClubAccount.objects.create_account(username='isolated-current-provider', nickname='隔离同名', account_type='PROVIDER')
        other = make_user(nickname='隔离同名')
        other_account = get_or_create_account_for_legacy_user(other)
        link_legacy_relations(other, other_account)

        def client_for(account):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            return client

        def inbox(account):
            response = client_for(account).get('/api/messages/')
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']['messages']

        push = client_for(staff_account).post('/api/admin/messages/push/', {
            'title': '隔离业务账号通知', 'preview': '隔离测试摘要',
            'recipient_account_ids': [boss.pk, provider.pk],
            'client_request_id': 'account-only-message'}, format='json')
        self.assertEqual(push.data['code'], 0, push.data)
        self.assertEqual(push.data['data']['count'], 2)
        rows = Message.objects.filter(title='隔离业务账号通知')
        self.assertEqual(rows.count(), 2)
        self.assertEqual(set(rows.values_list('recipient_account_id', flat=True)), {boss.pk, provider.pk})
        self.assertEqual(rows.filter(recipient__isnull=True).count(), 2)
        for account in (boss, provider):
            self.assertEqual(len([item for item in inbox(account) if item['title'] == '隔离业务账号通知']), 1)
        self.assertFalse(any(item['title'] == '隔离业务账号通知' for item in inbox(other_account)))
        provider_notice = rows.get(recipient_account=provider)
        detail = client_for(provider).get(f'/api/messages/{provider_notice.pk}/')
        self.assertEqual(detail.data['code'], 0, detail.data)
        provider_notice.refresh_from_db()
        self.assertTrue(provider_notice.is_read)
        boss_notice = rows.get(recipient_account=boss)
        detail = client_for(boss).get(f'/api/messages/{boss_notice.pk}/')
        self.assertEqual(detail.data['code'], 0, detail.data)
        boss_notice.refresh_from_db()
        self.assertTrue(boss_notice.is_read)