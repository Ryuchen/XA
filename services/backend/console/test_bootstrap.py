from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from rest_framework.test import APITestCase


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class BootstrapConsoleTest(APITestCase):
    @patch('console.management.commands.bootstrap_console.getpass', return_value='XA-setup-83!river')
    def test_created_business_admin_can_login_and_existing_user_is_preserved(self, getpass):
        call_command('bootstrap_console', username='release_operator', stdout=StringIO())
        from club_accounts.models import ClubAccount
        account = ClubAccount.objects.get(username='release_operator')
        account.phone = '13800001111'
        account.is_phone_verified = True
        account.save(update_fields=['phone', 'is_phone_verified'])
        codes = []
        with patch('users.password_login.send_verification_code',
                   side_effect=lambda phone, code: codes.append(code)):
            first = self.client.post('/api/admin/auth/login',
                {'username': 'release_operator', 'password': 'XA-setup-83!river'}, format='json')
            self.assertTrue(first.data['data']['requiresSms'], first.data)
            result = self.client.post('/api/admin/auth/login',
                {'username': 'release_operator', 'password': 'XA-setup-83!river',
                 'smsChallenge': first.data['data']['smsChallenge'], 'smsCode': codes[-1]}, format='json')
        self.assertEqual(result.data['code'], 0)
        with self.assertRaises(CommandError):
            call_command('bootstrap_console', username='release_operator', stdout=StringIO())
        self.assertEqual(getpass.call_count, 2)
