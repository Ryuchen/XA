"""Store window configuration is versioned and does not enable dispatch."""
from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog
from orders.grab_config import CONFIG_KEY, current_rules, snapshot_for_round
from orders.tests.factories import make_console_user
from stores.db import transaction
from wallet.models import SystemConfig


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class GrabConfigTests(APITestCase):
    def client_for(self, permissions):
        user = make_console_user(permissions)
        account = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = account
        user.admin_membership.save(update_fields=['account'])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        return client

    def test_permissions_validation_versions_and_evidence(self):
        editor = self.client_for(['dictionary:view', 'dictionary:edit', 'order:view'])
        viewer = self.client_for(['order:view'])
        url = '/api/admin/config/grab-window/'
        self.assertEqual(editor.get(url).data['data'], {'window_seconds': None, 'version': 0, 'automatic_dispatch_available': False})
        self.assertFalse(SystemConfig.objects.filter(key=CONFIG_KEY).exists())
        self.assertEqual(viewer.put(url, {'window_seconds': 30, 'version': 0}, format='json').status_code, 403)
        for value in (0, -1, True, 1.5, '30', 86401):
            self.assertEqual(editor.put(url, {'window_seconds': value, 'version': 0}, format='json').status_code, 400)
        response = editor.put(url, {'window_seconds': 30, 'version': 0}, format='json')
        self.assertEqual(response.data['data'], {'window_seconds': 30, 'version': 1, 'automatic_dispatch_available': False})
        self.assertEqual(editor.put(url, {'window_seconds': 40, 'version': 0}, format='json').status_code, 409)
        self.assertEqual(current_rules(), {'window_seconds': 30, 'version': 1})
        audit = AdminAuditLog.objects.get(resource='automatic-grab-window')
        self.assertEqual(audit.request_body['before'], {'window_seconds': None, 'version': 0})
        self.assertEqual(audit.request_body['after']['window_seconds'], 30)
        evidence = {entry['key']: entry for entry in editor.get('/api/admin/dictionaries/').data['data']['setup_evidence']}
        self.assertEqual(evidence['grab_window']['status'], 'SAVED')
        confirmation = editor.get('/api/admin/dictionaries/setup-confirmation/grab_window/').data['data']
        self.assertEqual(confirmation['fields'][0]['value'], '30 秒')
        self.assertEqual(editor.post('/api/admin/dictionaries/setup-confirmation/grab_window/', confirmation, format='json').status_code, 200)

    def test_failed_audit_rolls_back_configuration(self):
        editor = self.client_for(['dictionary:edit'])
        with patch('console.grab_config.AdminAuditLog.objects.create', side_effect=DatabaseError('audit failed')):
            with self.assertRaises(DatabaseError):
                editor.put('/api/admin/config/grab-window/', {'window_seconds': 30, 'version': 0}, format='json')
        self.assertFalse(SystemConfig.objects.filter(key=CONFIG_KEY).exists())

    def test_snapshot_freezes_window_and_delays_rejects_bad_configuration(self):
        editor = self.client_for(['dictionary:edit'])
        url = '/api/admin/config/grab-window/'
        editor.put(url, {'window_seconds': 30, 'version': 0}, format='json')
        now = timezone.now()
        with transaction.atomic():
            first = snapshot_for_round(opened_at=now, original_total_deadline=now + timedelta(seconds=10))
        self.assertEqual(first['deadline'], now + timedelta(seconds=30))
        self.assertEqual(first['total_deadline'], first['deadline'])
        self.assertEqual(first['delays']['GOLD'], 30)
        editor.put(url, {'window_seconds': 60, 'version': 1}, format='json')
        SystemConfig.objects.update_or_create(key='provider_pass_delay_seconds_GOLD', defaults={'value': '20'})
        with transaction.atomic():
            second = snapshot_for_round(opened_at=now, original_total_deadline=now + timedelta(seconds=90))
        self.assertEqual((first['window_seconds'], first['delays']['GOLD']), (30, 30))
        self.assertEqual((second['window_seconds'], second['config_version'], second['delays']['GOLD']), (60, 2, 20))
        SystemConfig.objects.filter(key='provider_pass_delay_seconds_GOLD').update(value='999')
        with transaction.atomic(), self.assertRaises(ValueError):
            snapshot_for_round(opened_at=now, original_total_deadline=now + timedelta(seconds=90))
