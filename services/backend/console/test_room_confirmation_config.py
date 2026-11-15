from unittest.mock import patch

from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog
from orders.room_confirmation_config import CONFIG_KEY, current_rules, required_rules
from orders.tests.factories import make_console_user
from wallet.models import SystemConfig


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class RoomConfirmationConfigTest(APITestCase):
    def client_for(self, permissions):
        user = make_console_user(permissions)
        account = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = account; user.admin_membership.save(update_fields=['account'])
        return APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(account)[0])

    def test_version_permissions_and_audit(self):
        editor = self.client_for(['dictionary:edit'])
        reader = self.client_for(['dictionary:view'])
        url = '/api/admin/config/room-confirmation/'
        self.assertEqual(reader.get(url).data['data'], {'minutes': None, 'version': 0})
        with self.assertRaises(ValueError):
            required_rules()
        self.assertEqual(reader.put(url, {'minutes': 30, 'version': 0}, format='json').status_code, 403)
        for value in (True, 0, -1, 43201, 1.5):
            self.assertEqual(editor.put(url, {'minutes': value, 'version': 0}, format='json').status_code, 400)
        self.assertEqual(editor.put(url, {'minutes': 30, 'version': 0}, format='json').data['data']['version'], 1)
        self.assertEqual(editor.put(url, {'minutes': 30, 'version': 0}, format='json').status_code, 409)
        self.assertEqual(editor.put(url, {'minutes': 60, 'version': 1}, format='json').data['data']['version'], 2)
        self.assertEqual(current_rules(), {'minutes': 60, 'version': 2})
        self.assertEqual(AdminAuditLog.objects.filter(resource='room-confirmation-deadline').count(), 2)
        from console.config_readiness import selected_configuration_evidence
        entries = {row['key']: row for row in selected_configuration_evidence({'dictionary:edit'})}
        self.assertEqual(entries['room_confirmation']['status'], 'SAVED')
        self.assertTrue(entries['room_confirmation']['can_confirm'])

    def test_invalid_data_and_audit_failure_do_not_silently_default(self):
        editor = self.client_for(['dictionary:edit'])
        url = '/api/admin/config/room-confirmation/'
        with patch('console.room_confirmation_config.AdminAuditLog.objects.create', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                editor.put(url, {'minutes': 30, 'version': 0}, format='json')
        self.assertFalse(SystemConfig.objects.filter(key=CONFIG_KEY).exists())
        SystemConfig.objects.create(key=CONFIG_KEY, value='broken')
        self.assertEqual(editor.get(url).status_code, 409)
        self.assertEqual(editor.put(url, {'minutes': 30, 'version': 0}, format='json').status_code, 409)
