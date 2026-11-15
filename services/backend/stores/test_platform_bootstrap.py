from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from stores.models import PlatformAccount, PlatformAuditEvent, PlatformRoleGrant


class BootstrapPlatformAdminTests(TestCase):
    def bootstrap(self, password='long-bootstrap-password-123'):
        output = StringIO()
        with patch('stores.management.commands.bootstrap_platform_admin.sys.stdin.isatty', return_value=True), \
                patch('stores.management.commands.bootstrap_platform_admin.getpass', side_effect=[password, password]):
            call_command('bootstrap_platform_admin', username='platform-root', phone='13800001111', stdout=output)
        self.assertNotIn(password, output.getvalue())
        return output.getvalue()

    def test_bootstrap_creates_separate_audited_identity_only_once(self):
        self.bootstrap()
        account = PlatformAccount.objects.get(username='platform-root')
        self.assertTrue(account.check_password('long-bootstrap-password-123'))
        self.assertFalse(account.is_phone_verified)
        self.assertTrue(PlatformRoleGrant.objects.filter(account=account, role='SUPER_ADMIN', scope_slug='*').exists())
        self.assertTrue(PlatformAuditEvent.objects.filter(account=account, action='platform.bootstrap').exists())
        with self.assertRaisesMessage(CommandError, '已存在'):
            self.bootstrap()
        self.assertEqual(PlatformAccount.objects.count(), 1)

    def test_noninteractive_bootstrap_rejected_without_creating_account(self):
        with patch('stores.management.commands.bootstrap_platform_admin.sys.stdin.isatty', return_value=False):
            with self.assertRaisesMessage(CommandError, '交互式终端'):
                call_command('bootstrap_platform_admin', username='platform-root', phone='13800001111')
        self.assertFalse(PlatformAccount.objects.exists())
