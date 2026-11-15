"""Reservation deadline configuration is store-local, versioned and audited."""
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog
from orders.tests.factories import make_console_user
from wallet.models import SystemConfig
from orders.reservation_deadlines import CONFIG_KEY, WINDOW_DEFAULTS


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class ReservationDeadlineConfigTest(APITestCase):
    def client_for(self, permissions):
        user = make_console_user(permissions)
        account = get_or_create_account_for_legacy_user(user)
        link_legacy_relations(user, account)
        user.admin_membership.account = account
        user.admin_membership.save(update_fields=['account'])
        return APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])

    def test_configuration_requires_edit_permission_and_versions_every_change(self):
        editor = self.client_for(['dictionary:view', 'dictionary:edit'])
        viewer = self.client_for(['dictionary:view'])
        url = '/api/admin/config/reservation-deadlines/'
        self.assertEqual(editor.get(url).data['data'], {'confirmation_minutes': None, 'payment_minutes': None, 'version': 0, **WINDOW_DEFAULTS})
        value = {'confirmation_minutes': 30, 'payment_minutes': 60, 'version': 0, **WINDOW_DEFAULTS}
        self.assertEqual(viewer.put(url, value, format='json').status_code, 403)
        for invalid in (True, 0, -1, 43201, 1.5):
            self.assertEqual(editor.put(url, {**value, 'confirmation_minutes': invalid}, format='json').status_code, 400)
        for bad in ({'slot_minutes': 7}, {'min_duration_minutes': 16},
                    {'max_duration_minutes': 10}, {'max_advance_days': 0}):
            self.assertEqual(editor.put(url, {**value, **bad}, format='json').status_code, 400)
        self.assertFalse(SystemConfig.objects.filter(key=CONFIG_KEY).exists())
        result = editor.put(url, value, format='json')
        self.assertEqual((result.status_code, result.data['data']['version']), (200, 1))
        self.assertEqual(editor.put(url, value, format='json').status_code, 409)
        result = editor.put(url, {**value, 'confirmation_minutes': 15, 'version': 1}, format='json')
        self.assertEqual(result.data['data']['version'], 2)
        self.assertEqual(AdminAuditLog.objects.filter(resource='reservation-deadlines').count(), 2)

    def test_invalid_persisted_configuration_is_not_treated_as_a_default(self):
        editor = self.client_for(['dictionary:view', 'dictionary:edit'])
        SystemConfig.objects.create(key=CONFIG_KEY, value='broken')
        url = '/api/admin/config/reservation-deadlines/'
        self.assertEqual(editor.get(url).status_code, 409)
        self.assertEqual(editor.put(url, {'confirmation_minutes': 30, 'payment_minutes': 60, 'version': 0, **WINDOW_DEFAULTS},
                                    format='json').status_code, 409)
