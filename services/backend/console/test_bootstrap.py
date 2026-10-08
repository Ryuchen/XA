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
        result = self.client.post('/api/admin/auth/login', {'username': 'release_operator', 'password': 'XA-setup-83!river'})
        self.assertEqual(result.data['code'], 0)
        with self.assertRaises(CommandError):
            call_command('bootstrap_console', username='release_operator', stdout=StringIO())
        self.assertEqual(getpass.call_count, 2)
