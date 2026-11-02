from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from audition.models import AuditionSignup
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership
from orders.tests.factories import make_console_user, make_user
from site_messages.models import Message


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class AuditionReviewWorkflowTest(APITestCase):
    def setUp(self):
        staff = make_console_user(['audition:view', 'audition:edit', 'audition:delete', 'audition:signup_view', 'audition:signup_audit'])
        self.staff = get_or_create_account_for_legacy_user(staff)
        AdminMembership.objects.filter(user=staff).update(account=self.staff)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.staff)[0])
        response = self.client.post('/api/admin/audition-links/', {'title': '隔离试音活动'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.link_id = response.data['data']['id']
        self.token = response.data['data']['provider_token']

    def sign_up(self, suffix):
        provider = make_user(role='PROVIDER', nickname='隔离同名陪玩')
        account = get_or_create_account_for_legacy_user(provider)
        link_legacy_relations(provider, account)
        account.username = f'isolated_audition_{suffix}'
        account.save(update_fields=['username'])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        result = client.post('/api/audition/signup', {'token': self.token, 'contact': f'测试联系方式{suffix}', 'game': '测试游戏'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        return provider, account, client, result.data['data']['id']

    def test_real_signup_filter_review_conflict_and_notification(self):
        provider, account, buyer, signup_id = self.sign_up('one')
        other, other_account, other_client, other_id = self.sign_up('two')
        for keyword in (account.username, account.account_no):
            response = self.client.get('/api/admin/audition-signups/', {'keyword': keyword})
            self.assertEqual([row['id'] for row in response.data['data']['list']], [signup_id])
            row = response.data['data']['list'][0]
            self.assertEqual((row['applicant_no'], row['applicant_username']), (account.account_no, account.username))
        approved = self.client.post(f'/api/admin/audition-signups/{signup_id}/approve/')
        self.assertEqual(approved.data['data']['status'], 'APPROVED')
        duplicate = self.client.post(f'/api/admin/audition-signups/{signup_id}/reject/', {'audit_remark': '重复审核'}, format='json')
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(AuditionSignup.objects.get(pk=signup_id).status, 'APPROVED')
        self.assertEqual(Message.objects.filter(recipient_account=account, title='试音报名通过').count(), 1)
        self.assertFalse(Message.objects.filter(recipient_account=other_account, title='试音报名通过').exists())
        rejected = self.client.post(f'/api/admin/audition-signups/{other_id}/reject/', {'audit_remark': '测试资料不完整'}, format='json')
        self.assertEqual((rejected.data['data']['status'], rejected.data['data']['audit_remark']), ('REJECTED', '测试资料不完整'))
        mine = other_client.get('/api/audition/my-signups')
        self.assertEqual(mine.data['data'][0]['audit_remark'], '测试资料不完整')
        self.assertEqual(self.client.delete(f'/api/admin/audition-links/{self.link_id}/').status_code, 400)
        self.assertEqual(AuditionSignup.objects.filter(link_id=self.link_id).count(), 2)

    def test_audit_requires_permission(self):
        _, _, _, signup_id = self.sign_up('restricted')
        staff = make_console_user(['audition:signup_view'])
        account = get_or_create_account_for_legacy_user(staff)
        AdminMembership.objects.filter(user=staff).update(account=account)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        self.assertEqual(client.post(f'/api/admin/audition-signups/{signup_id}/approve/').status_code, 403)
        self.assertEqual(AuditionSignup.objects.get(pk=signup_id).status, 'PENDING')
