from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import DatabaseError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog
from console.points_redemption_config import save_points_terms
from orders.points_redemption_config import CONFIG_KEY, current_rules
from orders.points_redemption_math import quote_points_redemption
from orders.tests.factories import make_console_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from wallet.models import SystemConfig

URL = '/api/admin/config/points-redemption/'
TERMS = {'group_point_hundredths': 10000, 'group_coin_units': 30,
         'max_ratio_basis_points': 5000, 'version': 0}


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class PointsConfigApiTests(APITestCase):
    def client_for(self, permissions):
        user = make_console_user(permissions)
        account = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = account
        user.admin_membership.save(update_fields=['account'])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        return client

    def test_permissions_versioning_audit_and_no_implicit_enable(self):
        editor = self.client_for(['dictionary:edit'])
        viewer = self.client_for(['dictionary:view'])
        denied = self.client_for(['engagement:view'])
        self.assertEqual(denied.get(URL).status_code, 403)
        initial = viewer.get(URL).data['data']
        self.assertEqual(initial['version'], 0)
        self.assertIsNone(initial['group_coin_units'])
        self.assertFalse(initial['redemption_available'])
        self.assertFalse(SystemConfig.objects.filter(key=CONFIG_KEY).exists())
        self.assertEqual(viewer.put(URL, TERMS, format='json').status_code, 403)
        result = editor.put(URL, TERMS, format='json')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data['data']['version'], 1)
        self.assertFalse(result.data['data']['redemption_available'])
        self.assertEqual(editor.put(URL, TERMS, format='json').status_code, 409)
        audit = AdminAuditLog.objects.get(resource='points-redemption-terms')
        self.assertEqual(audit.request_body['before']['version'], 0)
        self.assertEqual(audit.request_body['after']['group_coin_units'], 30)
        result = editor.put(URL, {**TERMS, 'group_coin_units': 20, 'version': 1}, format='json')
        self.assertEqual(result.data['data']['version'], 2)
        self.assertEqual(current_rules()['group_coin_units'], 20)

    def test_invalid_inputs_and_corrupt_stored_data_fail_closed(self):
        client = self.client_for(['dictionary:edit'])
        for changes in ({'group_point_hundredths': 0}, {'group_point_hundredths': True},
                        {'group_coin_units': 31}, {'max_ratio_basis_points': 10000},
                        {'max_ratio_basis_points': -1}, {'version': -1}, {'group_coin_units': '30'},
                        {'redemption_available': True}):
            with self.subTest(changes=changes):
                self.assertEqual(client.put(URL, {**TERMS, **changes}, format='json').status_code, 400)
        SystemConfig.objects.create(key=CONFIG_KEY, value='{"version":1}')
        self.assertEqual(client.get(URL).status_code, 409)
        self.assertEqual(client.put(URL, {**TERMS, 'version': 1}, format='json').status_code, 409)

    def test_audit_failure_rolls_back_terms(self):
        client = self.client_for(['dictionary:edit'])
        with patch('console.points_redemption_config.AdminAuditLog.objects.create', side_effect=DatabaseError('audit')):
            with self.assertRaises(DatabaseError):
                client.put(URL, TERMS, format='json')
        self.assertFalse(SystemConfig.objects.filter(key=CONFIG_KEY).exists())


@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class PointsConfigStoreIsolationTests(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_terms_and_calculation_are_scoped_to_each_store(self):
        first, second = resolve_store(), xinan_store()
        outcomes = []
        for store, coin_units in ((first, 30), (second, 20)):
            with store_context(store):
                rules = save_points_terms(**{**TERMS, 'group_coin_units': coin_units}, audit=lambda *_: None)
                quote = quote_points_redemption(payable_before_points_units=1000,
                    available_point_hundredths=10000, selected_point_hundredths=10000,
                    **{k: rules[k] for k in ('group_point_hundredths', 'group_coin_units', 'max_ratio_basis_points')})
                outcomes.append((current_rules()['version'], quote.discount_units))
        self.assertEqual(outcomes, [(1, 30), (1, 20)])
        with store_context(first):
            self.assertEqual(current_rules()['group_coin_units'], 30)
