from datetime import timedelta
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from .models import AuditionLink


class WechatInvitationEntryTests(APITestCase):
    def setUp(self):
        self.owner = get_or_create_account_for_legacy_user(get_user_model().objects.create_user(username='invitation-owner', role='CUSTOMER'))
        self.other = get_or_create_account_for_legacy_user(get_user_model().objects.create_user(username='invitation-other', role='CUSTOMER'))
        self.link = AuditionLink.objects.create(title='隔离测试试音', boss_account=self.owner)

    def enter(self, account=None):
        self.client.credentials()
        if account:
            token, _ = issue_account_tokens(account)
            self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        return self.client.get('/api/audition/enter', {'token': self.link.boss_token, 'role': 'boss'})

    def test_wechat_authenticated_owner_can_enter_without_receiving_new_token(self):
        response = self.enter(self.owner)
        self.assertEqual(response.data['code'], 0)
        self.assertTrue(response.data['data']['matched'])
        self.assertNotIn('token', response.data['data'])

    def test_another_account_cannot_use_invitation_to_switch_identity(self):
        self.assertEqual(self.enter(self.other).status_code, 403)

    def test_anonymous_invitation_does_not_log_in(self):
        self.assertIn(self.enter().status_code, (401, 403))

    def test_inactive_or_expired_link_cannot_enter(self):
        self.link.expire_at = timezone.now() - timedelta(seconds=1)
        self.link.save(update_fields=['expire_at'])
        self.assertEqual(self.enter(self.owner).status_code, 410)
