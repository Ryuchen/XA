"""Configuration permission failures are HTTP failures and never persist edits."""
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog
from orders.tests.factories import make_console_user, make_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, LOGIN_OTP_REQUIRED=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ConfigurationPermissionStatusTest(APITestCase):
    CONFIGURATIONS = (
        ('commission', {'rate': 37}),
        ('withdraw', {'min_amount': 15000, 'tax_rate': 3}),
        ('checkin', {'daily_spend_required': 9000, 'makeup_card_spend_required': 19000,
                     'max_makeup_cards': 2}),
    )

    def client_for(self, permissions=None, *, boss=False):
        user = make_user() if boss else make_console_user(permissions)
        account = get_or_create_account_for_legacy_user(user)
        if not boss:
            user.admin_membership.account = account
            user.admin_membership.save(update_fields=['account'])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        return client

    def test_readonly_staff_read_but_receive_http_403_for_put_and_leave_configuration_unchanged(self):
        client = self.client_for(['report:view', 'withdraw:view', 'checkin:view'])
        for name, payload in self.CONFIGURATIONS:
            path = '/api/admin/config/' + name
            with self.subTest(configuration=name):
                before = client.get(path)
                self.assertEqual((before.status_code, before.data['code']), (200, 0))
                denied = client.put(path, payload, format='json')
                self.assertEqual((denied.status_code, denied.data['code']), (403, 403), denied.data)
                after = client.get(path)
                self.assertEqual(after.data, before.data)
                self.assertFalse(AdminAuditLog.objects.filter(path=path, method='PUT').exists())

    def test_authorized_staff_changes_each_configuration_and_readonly_staff_see_committed_values(self):
        writer = self.client_for(['report:audit', 'withdraw:config', 'checkin:edit'])
        reader = self.client_for(['report:view', 'withdraw:view', 'checkin:view'])
        for name, payload in self.CONFIGURATIONS:
            path = '/api/admin/config/' + name
            with self.subTest(configuration=name):
                saved = writer.put(path, payload, format='json')
                self.assertEqual((saved.status_code, saved.data['code']), (200, 0), saved.data)
                result = reader.get(path)
                self.assertEqual((result.status_code, result.data['code']), (200, 0))
                for field, value in payload.items():
                    self.assertEqual(result.data['data'][field], value)

    def test_anonymous_and_boss_sessions_cannot_write_any_configuration(self):
        for client, expected in ((APIClient(), 401), (self.client_for(boss=True), 403)):
            for name, payload in self.CONFIGURATIONS:
                with self.subTest(configuration=name, expected=expected):
                    response = client.put('/api/admin/config/' + name, payload, format='json')
                    self.assertEqual(response.status_code, expected, response.data)
