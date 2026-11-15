"""The dictionary reports evidence, never treating fallback as confirmation."""
import json
from unittest.mock import patch

from django.db import IntegrityError
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.capacity import CONFIG_KEY, UNCONFIGURED
from orders.tests.factories import make_console_user
from wallet.club_rules import DEPOSIT_PERCENT_KEY, DEPOSIT_TARGET_KEY
from wallet.models import SystemConfig
from wallet.settlement_clocks import CONFIG_KEY as CLOCK_KEY
from .models import AdminAuditLog


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class SetupEvidenceTests(APITestCase):
    def client_for(self, permissions):
        user = make_console_user(permissions)
        account = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = account
        user.admin_membership.save(update_fields=['account'])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        return client

    def evidence(self, client):
        response = client.get('/api/admin/dictionaries/')
        self.assertEqual(response.data['code'], 0, response.data)
        self.assertEqual(response.data['data']['setup_evidence_capability'], 'SELECTED_RULES_V1')
        return {row['key']: row for row in response.data['data']['setup_evidence']}

    def test_account_fields_coverage_requires_explicit_effective_configuration(self):
        from orders.models import GameCategory, ServiceCategory
        from orders.tests.factories import make_service
        client = self.client_for(['dictionary:view', 'dictionary:edit'])
        self.assertEqual(self.evidence(client)['order_account_fields']['status'], 'MISSING')
        game = GameCategory.objects.create(name='资料核对游戏')
        first = make_service(name='继承游戏服务', price=1000)
        first.game_category = game; first.save(update_fields=['game_category'])
        second = make_service(name='无游戏服务', price=1000)
        inactive = make_service(name='停用服务', price=1000)
        inactive.is_active = False; inactive.save(update_fields=['is_active'])
        gift = make_service(name='礼物服务', price=1000)
        gift.service_category = ServiceCategory.objects.create(name='礼物测试分类', is_gift=True)
        gift.save(update_fields=['service_category'])
        addon = make_service(name='加购服务', price=1000)
        addon.is_addon = True; addon.save(update_fields=['is_addon'])
        self.assertIn('0/2', self.evidence(client)['order_account_fields']['message'])
        game_url = f'/api/admin/config/order-account-fields/game/{game.pk}/'
        self.assertEqual(client.put(game_url, {'fields': [], 'version': 0}, format='json').status_code, 200)
        self.assertIn('1/2', self.evidence(client)['order_account_fields']['message'])
        service_url = f'/api/admin/config/order-account-fields/service/{second.pk}/'
        self.assertEqual(client.put(service_url, {'fields': ['game_uid'], 'version': 0}, format='json').status_code, 200)
        self.assertEqual(self.evidence(client)['order_account_fields']['status'], 'SAVED')
        # Inheritance without a game is still unconfigured; an empty list is explicit.
        self.assertEqual(client.put(service_url, {'fields': None, 'version': 1}, format='json').status_code, 200)
        self.assertIn('1/2', self.evidence(client)['order_account_fields']['message'])
        self.assertEqual(client.put(service_url, {'fields': [], 'version': 2}, format='json').status_code, 200)
        SystemConfig.objects.filter(key=f'order_account_fields_game_{game.pk}').update(value='bad')
        self.assertEqual(self.evidence(client)['order_account_fields']['status'], 'INVALID')
        # A valid service override does not depend on its game's malformed rule.
        override = f'/api/admin/config/order-account-fields/service/{first.pk}/'
        self.assertEqual(client.put(override, {'fields': [], 'version': 0}, format='json').status_code, 200)
        self.assertEqual(self.evidence(client)['order_account_fields']['status'], 'SAVED')

    def test_read_does_not_create_config_and_permissions_hide_sensitive_status(self):
        client = self.client_for(['dictionary:view'])
        before = list(SystemConfig.objects.order_by('key').values_list('key', 'value'))
        rows = self.evidence(client)
        self.assertEqual(rows['capacity']['status'], 'MISSING')
        self.assertIsNone(rows['capacity']['path'])
        self.assertTrue(all(rows[key]['status'] == 'RESTRICTED' and rows[key]['path'] is None
                            for key in ('settlement_clocks', 'deposit_targets', 'deposit_percent', 'withdraw')))
        self.assertEqual(list(SystemConfig.objects.order_by('key').values_list('key', 'value')), before)
        denied = self.client_for(['deposit_rule:view'])
        self.assertEqual(denied.get('/api/admin/dictionaries/').status_code, 403)

    def test_real_edits_change_evidence_without_calling_it_opening_approval(self):
        client = self.client_for(['dictionary:view', 'dictionary:edit', 'order:view',
                                  'deposit_rule:view', 'deposit_rule:edit', 'withdraw:view', 'withdraw:config'])
        SystemConfig.objects.update_or_create(key=CONFIG_KEY, defaults={'value': UNCONFIGURED})
        self.assertEqual(self.evidence(client)['capacity']['status'], 'MISSING')
        SystemConfig.objects.update_or_create(key=CLOCK_KEY, defaults={'value': json.dumps(
            {'aftersale_hours': 72, 'income_freeze_hours': 72, 'version': 0})})
        self.assertEqual(self.evidence(client)['settlement_clocks']['status'], 'NEEDS_CONFIRMATION')
        response = client.put('/api/admin/config/provider-capacity/', {'max_concurrent_orders': 4, 'version': 0}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        response = client.put('/api/admin/config/settlement-clocks',
                              {'aftersale_hours': 72, 'income_freeze_hours': 72, 'expected_version': 0}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        response = client.put('/api/admin/config/deposit-targets',
                              {'cash_target': 40000, 'earnings_target': 80000, 'expected_version': 0}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        rows = self.evidence(client)
        self.assertEqual([rows[key]['status'] for key in ('capacity', 'settlement_clocks', 'deposit_targets')],
                         ['SAVED', 'SAVED', 'SAVED'])
        self.assertIn('完整开店条件', rows['capacity']['message'])
        SystemConfig.objects.update_or_create(key=DEPOSIT_PERCENT_KEY, defaults={'value': '0'})
        for key, value in [('min_withdraw_amount', '0'), ('withdraw_balance_threshold', '0'), ('withdraw_tax_rate', '0')]:
            SystemConfig.objects.update_or_create(key=key, defaults={'value': value})
        rows = self.evidence(client)
        self.assertEqual(rows['deposit_percent']['status'], 'NEEDS_CONFIRMATION')
        self.assertEqual(rows['withdraw']['status'], 'NEEDS_CONFIRMATION')
        SystemConfig.objects.filter(key=DEPOSIT_TARGET_KEY).update(value=json.dumps({'cash_target': 0, 'earnings_target': 80000, 'version': 1}))
        SystemConfig.objects.filter(key=CLOCK_KEY).update(value='invalid')
        SystemConfig.objects.filter(key='withdraw_tax_rate').update(value='101')
        rows = self.evidence(client)
        self.assertEqual([rows[key]['status'] for key in ('settlement_clocks', 'deposit_targets', 'withdraw')],
                         ['INVALID', 'INVALID', 'INVALID'])
        SystemConfig.objects.filter(key='withdraw_balance_threshold').delete()
        self.assertEqual(self.evidence(client)['withdraw']['status'], 'INVALID')

    def test_confirm_exact_values_authorization_staleness_and_audit(self):
        client = self.client_for(['dictionary:view', 'deposit_rule:view', 'deposit_rule:edit',
                                  'withdraw:view', 'withdraw:config'])
        reader = self.client_for(['dictionary:view', 'withdraw:view'])
        url = '/api/admin/dictionaries/setup-confirmation/withdraw/'
        for key, value in [('min_withdraw_amount', '125'), ('withdraw_balance_threshold', '5000'), ('withdraw_tax_rate', '0')]:
            SystemConfig.objects.update_or_create(key=key, defaults={'value': value})
        self.assertEqual(reader.get(url).status_code, 403)
        self.assertEqual(reader.post(url, {'fingerprint': 'a' * 64}, format='json').status_code, 403)
        preview = client.get(url).data['data']
        self.assertEqual([field['value'] for field in preview['fields']], ['12.5 浪花', '500 浪花', '0%'])
        self.assertEqual(client.post(url, {'fingerprint': preview['fingerprint']}, format='json').status_code, 200)
        self.assertEqual(self.evidence(client)['withdraw']['status'], 'CONFIRMED')
        audit = AdminAuditLog.objects.get(resource='store-setup-confirmation', object_id='withdraw')
        self.assertEqual(audit.request_body, preview)
        self.assertEqual(client.post(url, {'fingerprint': preview['fingerprint']}, format='json').status_code, 200)
        self.assertEqual(AdminAuditLog.objects.filter(resource='store-setup-confirmation', object_id='withdraw').count(), 1)
        row = SystemConfig.objects.get(key='withdraw_tax_rate')
        row.value = '5'; row.save()
        self.assertEqual(self.evidence(client)['withdraw']['status'], 'NEEDS_CONFIRMATION')
        self.assertEqual(client.post(url, {'fingerprint': preview['fingerprint']}, format='json').status_code, 409)
        row.value = '0'; row.save()
        self.assertEqual(self.evidence(client)['withdraw']['status'], 'NEEDS_CONFIRMATION')
        self.assertNotEqual(client.get(url).data['data']['fingerprint'], preview['fingerprint'])

    def test_confirm_percent_zero_and_audit_failure_does_not_confirm(self):
        client = self.client_for(['dictionary:view', 'deposit_rule:view', 'deposit_rule:edit'])
        url = '/api/admin/dictionaries/setup-confirmation/deposit_percent/'
        self.assertEqual(client.get(url).status_code, 409)
        SystemConfig.objects.create(key=DEPOSIT_PERCENT_KEY, value='0')
        preview = client.get(url).data['data']
        self.assertEqual(preview['fields'][0]['value'], '0%')
        with patch('console.config_readiness.AdminAuditLog.objects.create', side_effect=IntegrityError('test rollback')):
            self.assertEqual(client.post(url, {'fingerprint': preview['fingerprint']}, format='json').status_code, 409)
        self.assertEqual(self.evidence(client)['deposit_percent']['status'], 'NEEDS_CONFIRMATION')
        self.assertEqual(client.post(url, {'fingerprint': preview['fingerprint']}, format='json').status_code, 200)
        self.assertEqual(self.evidence(client)['deposit_percent']['status'], 'CONFIRMED')

    def test_confirm_versioned_rules_and_actual_edits_require_reconfirmation(self):
        client = self.client_for(['dictionary:view', 'dictionary:edit', 'order:view',
                                  'deposit_rule:view', 'deposit_rule:edit'])
        values = {
            CONFIG_KEY: {'max_concurrent_orders': 3, 'version': 0},
            CLOCK_KEY: {'aftersale_hours': 72, 'income_freeze_hours': 72, 'version': 0},
            DEPOSIT_TARGET_KEY: {'cash_target': 40000, 'earnings_target': 80000, 'version': 0},
        }
        for key, value in values.items():
            SystemConfig.objects.update_or_create(key=key, defaults={'value': json.dumps(value)})
        cases = [
            ('capacity', ['3 单'], '/api/admin/config/provider-capacity/', {'max_concurrent_orders': 4, 'version': 0}),
            ('settlement_clocks', ['72 小时', '72 小时'], '/api/admin/config/settlement-clocks',
             {'aftersale_hours': 48, 'income_freeze_hours': 72, 'expected_version': 0}),
            ('deposit_targets', ['4000 浪花', '8000 浪花'], '/api/admin/config/deposit-targets',
             {'cash_target': 50000, 'earnings_target': 80000, 'expected_version': 0}),
        ]
        for key, expected, endpoint, update in cases:
            url = f'/api/admin/dictionaries/setup-confirmation/{key}/'
            preview = client.get(url).data['data']
            self.assertEqual([field['value'] for field in preview['fields']], expected)
            self.assertEqual(client.post(url, {'fingerprint': preview['fingerprint']}, format='json').status_code, 200)
            self.assertEqual(self.evidence(client)[key]['status'], 'CONFIRMED')
            self.assertEqual(client.put(endpoint, update, format='json').status_code, 200)
            self.assertEqual(self.evidence(client)[key]['status'], 'SAVED')
            self.assertEqual(client.post(url, {'fingerprint': preview['fingerprint']}, format='json').status_code, 409)
