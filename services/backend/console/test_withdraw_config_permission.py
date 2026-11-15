from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog
from orders.tests.factories import make_console_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, LOGIN_OTP_REQUIRED=False)
class WithdrawPermissionSplitTest(APITestCase):
    path = '/api/admin/config/withdraw'

    def client_for(self, permissions):
        user = make_console_user(permissions)
        account = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = account
        user.admin_membership.save(update_fields=['account'])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        return client

    def test_reviewer_cannot_edit_configuration_or_change_values(self):
        reviewer = self.client_for(['withdraw:view', 'withdraw:audit'])
        before = reviewer.get(self.path).data
        denied = reviewer.put(self.path, {'min_amount': 10000, 'tax_rate': 7}, format='json')
        self.assertEqual((denied.status_code, denied.data['code']), (403, 403))
        self.assertEqual(reviewer.get(self.path).data, before)
        self.assertFalse(AdminAuditLog.objects.filter(path=self.path, method='PUT', status_code=200).exists())

    def test_config_editor_can_modify_but_cannot_claim_withdrawals(self):
        editor = self.client_for(['withdraw:config'])
        self.assertEqual(editor.get(self.path).status_code, 200)
        saved = editor.put(self.path, {'min_amount': 10000, 'tax_rate': 7}, format='json')
        self.assertEqual((saved.status_code, saved.data['code']), (200, 0))
        claim = editor.post('/api/admin/withdrawals/1/claim/')
        self.assertEqual(claim.status_code, 403)
