"""Platform setup writes use real store rules and preserve identity boundaries."""
import json
from unittest.mock import patch

from django.db import DatabaseError, IntegrityError
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from console.config_readiness import selected_configuration_evidence
from console.models import AdminAuditLog
from orders.capacity import CONFIG_KEY
from stores.context import resolve_store, store_context
from stores.models import PlatformAccount, PlatformAuditEvent, PlatformRoleGrant, Store
from stores.platform_auth import issue_tokens
from wallet.models import SystemConfig
from wallet.club_rules import DEPOSIT_TARGET_KEY, DEPOSIT_PERCENT_KEY
from wallet.models import MIN_WITHDRAW_AMOUNT_KEY


@override_settings(PLATFORM_IDENTITY_ENABLED=True, ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False)
class PlatformSetupTests(TransactionTestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.original = resolve_store()
        self.branch = Store.objects.create(slug='setup-platform-branch', name='待配置隔离店',
            entity_prefix='PS', database_alias='store_b', status=Store.Status.CONFIGURING, initialized_at=timezone.now())
        self.actor, self.client = self.platform('setup-platform-owner', 'SUPER_ADMIN')
        self.url = f'/api/admin/platform/store-management/{self.branch.pk}/setup'

    def platform(self, username, role, scope='*'):
        account = PlatformAccount(username=username, phone='13800001111', is_phone_verified=True)
        account.set_password('Synthetic-Platform-Password-2026!'); account.save()
        PlatformRoleGrant.objects.create(account=account, role=role, scope_slug=scope)
        token, _ = issue_tokens(account, 'SMS', account.phone)
        return account, APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)

    def test_approved_price_references_show_only_the_selected_store_current_values(self):
        from orders.models import GameCategory
        from orders.tests.factories import make_service
        key = 'xa-price-817e8f1e23d0dd5ba034ab1c'
        for store, price in ((self.original, 500), (self.branch, 700)):
            with store_context(store):
                game = GameCategory.objects.create(name='三角洲行动')
                make_service(name=f'{store.slug} 自定房卡', price=price, catalog_key=key,
                             game_category=game, platform='PC', security_level='ANY', is_addon=True)
        for store, price in ((self.original, 500), (self.branch, 700)):
            url = f'/api/admin/platform/store-management/{store.pk}/setup'
            result = self.client.get(url)
            self.assertEqual(result.status_code, 200, result.data)
            references = {row['catalog_key']: row for row in result.data['data']['catalog_price_references']}
            self.assertEqual((references[key]['status'], references[key]['reference_price'],
                              references[key]['current']['price']), ('FOUND', 400, price))
            missing = references['xa-price-048fb2e82e25ca1ad3fd98fc']
            self.assertEqual((missing['status'], missing['reference_price'], missing['current']),
                             ('NOT_CREATED', 10800, None))
        with store_context(self.branch):
            from orders.models import ServiceItem
            ServiceItem.objects.filter(catalog_key=key).update(is_addon=False)
        result = self.client.get(self.url).data['data']['catalog_price_references']
        row = next(item for item in result if item['catalog_key'] == key)
        self.assertEqual((row['status'], row['current']['price']), ('IDENTITY_REVIEW', 700))
        _, finance = self.platform('price-reference-finance', 'FINANCE')
        self.assertEqual(finance.get(self.url).status_code, 403)

    def test_grab_window_is_store_local_versioned_and_audited(self):
        from orders.grab_config import CONFIG_KEY as GRAB_KEY, current_rules
        self.assertEqual(self.client.get(self.url).data['data']['grab_window'], {'window_seconds': None, 'version': 0})
        _, finance = self.platform('grab-finance', 'FINANCE')
        self.assertEqual(finance.put(self.url + '/grab-window', {'window_seconds': 30, 'version': 0}, format='json').status_code, 403)
        result = self.client.put(self.url + '/grab-window', {'window_seconds': 30, 'version': 0}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        with store_context(self.branch):
            self.assertEqual(current_rules(), {'window_seconds': 30, 'version': 1})
            audit = AdminAuditLog.objects.get(resource='automatic-grab-window')
            self.assertEqual(audit.request_body['platform_account_id'], str(self.actor.pk))
            operation = audit.request_body['operation_id']
        self.assertTrue(PlatformAuditEvent.objects.filter(account=self.actor, action='setup.grab_window_result', detail__operation_id=operation).exists())
        with store_context(self.original):
            self.assertFalse(SystemConfig.objects.filter(key=GRAB_KEY).exists())
        self.assertEqual(self.client.put(self.url + '/grab-window', {'window_seconds': 40, 'version': 0}, format='json').status_code, 409)
        rows = {row['key']: row for row in self.client.get(self.url).data['data']['evidence']}
        snapshot = rows['grab_window']['snapshot']
        result = self.client.post(self.url + '/confirm/grab_window', {'fingerprint': snapshot['fingerprint']}, format='json')
        self.assertEqual(result.status_code, 200, result.data)

    def test_reservation_deadlines_are_store_local_visible_and_audited(self):
        from orders.reservation_deadlines import current_rules
        data = self.client.get(self.url).data['data']
        self.assertEqual(data['reservation_deadlines']['version'], 0)
        entries = {item['key']: item for item in data['evidence']}
        self.assertEqual(entries['reservation_deadlines']['status'], 'MISSING')
        url = self.url + '/reservation-deadlines'
        value = {'confirmation_minutes': 30, 'payment_minutes': 60, 'version': 0}
        _, finance = self.platform('reservation-finance', 'FINANCE')
        self.assertEqual(finance.put(url, value, format='json').status_code, 403)
        result = self.client.put(url, value, format='json')
        self.assertEqual((result.status_code, result.data['data']['version']), (200, 1))
        self.assertEqual(self.client.put(url, value, format='json').status_code, 409)
        with store_context(self.branch):
            self.assertEqual(current_rules(), {**value, 'version': 1})
            audit = AdminAuditLog.objects.get(resource='reservation-deadlines')
            self.assertEqual(audit.request_body['platform_account_id'], str(self.actor.pk))
        with store_context(self.original):
            self.assertEqual(current_rules()['version'], 0)
        entries = {item['key']: item for item in self.client.get(self.url).data['data']['evidence']}
        self.assertEqual(entries['reservation_deadlines']['status'], 'SAVED')

    def test_room_confirmation_is_independent_authorized_and_audited(self):
        from orders.room_confirmation_config import current_rules
        self.assertEqual(self.client.get(self.url).data['data']['room_confirmation'], {'minutes': None, 'version': 0})
        url = self.url + '/room-confirmation'
        _, finance = self.platform('room-confirmation-finance', 'FINANCE')
        self.assertEqual(finance.put(url, {'minutes': 30, 'version': 0}, format='json').status_code, 403)
        result = self.client.put(url, {'minutes': 30, 'version': 0}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(self.client.put(url, {'minutes': 40, 'version': 0}, format='json').status_code, 409)
        with store_context(self.branch):
            self.assertEqual(current_rules(), {'minutes': 30, 'version': 1})
            audit = AdminAuditLog.objects.get(resource='room-confirmation-deadline')
            operation = audit.request_body['operation_id']
            self.assertEqual(audit.request_body['platform_account_id'], str(self.actor.pk))
        with store_context(self.original):
            self.assertEqual(current_rules(), {'minutes': None, 'version': 0})
        self.assertTrue(PlatformAuditEvent.objects.filter(action='setup.room_confirmation_result',
            detail__operation_id=operation).exists())
        evidence = {row['key']: row for row in self.client.get(self.url).data['data']['evidence']}
        self.assertEqual(evidence['room_confirmation']['status'], 'SAVED')
        result = self.client.post(self.url + '/confirm/room_confirmation',
            {'fingerprint': evidence['room_confirmation']['snapshot']['fingerprint']}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(next(row for row in self.client.get(self.url).data['data']['evidence']
                              if row['key'] == 'room_confirmation')['status'], 'CONFIRMED')

    @override_settings(POINTS_USAGE_SWITCHES_ENABLED=True)
    def test_platform_points_setup_is_authorized_isolated_versioned_and_audited(self):
        from engagement.points_usage import CONFIG_KEY as USAGE_KEY, current_usage
        from orders.points_redemption_config import CONFIG_KEY as TERMS_KEY, current_rules
        data = self.client.get(self.url).data['data']
        self.assertEqual(data['points_terms']['version'], 0)
        self.assertTrue(data['points_usage_enforcement_available'])
        self.assertEqual(data['points_usage']['version'], 0)
        terms = {'group_point_hundredths': 10000, 'group_coin_units': 30,
                 'max_ratio_basis_points': 5000, 'version': 0}
        switches = {'redemption_enabled': True, 'shop_enabled': True, 'lottery_enabled': False,
                    'issuance_enabled': True, 'expected_issuance_enabled': True, 'version': 0}
        _, finance = self.platform('points-finance', 'FINANCE')
        self.assertEqual(finance.put(self.url + '/points-terms', terms, format='json').status_code, 403)
        self.assertEqual(finance.put(self.url + '/points-usage', switches, format='json').status_code, 403)
        self.assertEqual(self.client.put(self.url + '/points-terms', terms, format='json').status_code, 200)
        self.assertEqual(self.client.put(self.url + '/points-usage', switches, format='json').status_code, 200)
        self.assertEqual(self.client.put(self.url + '/points-terms', terms, format='json').status_code, 409)
        self.assertEqual(self.client.put(self.url + '/points-usage', switches, format='json').status_code, 409)
        with store_context(self.branch):
            self.assertEqual(current_rules()['group_coin_units'], 30)
            self.assertFalse(current_usage()[1]['lottery_enabled'])
            for resource, action in (('points-redemption-terms', 'points_terms'), ('points-usage-switches', 'points_usage')):
                audit = AdminAuditLog.objects.get(resource=resource)
                self.assertIsNone(audit.operator_account_id)
                self.assertEqual(audit.request_body['platform_account_id'], str(self.actor.pk))
                operation = audit.request_body['operation_id']
                self.assertTrue(PlatformAuditEvent.objects.filter(account=self.actor, action=f'setup.{action}_result',
                                detail__operation_id=operation).exists())
        with store_context(self.original):
            self.assertFalse(SystemConfig.objects.filter(key__in=[USAGE_KEY, TERMS_KEY]).exists())

    @override_settings(POINTS_USAGE_SWITCHES_ENABLED=True)
    def test_platform_points_failed_audit_rolls_back_store_and_reports_unknown(self):
        from orders.points_redemption_config import current_rules
        terms = {'group_point_hundredths': 10000, 'group_coin_units': 30,
                 'max_ratio_basis_points': 5000, 'version': 0}
        with patch('stores.platform_store_setup.AdminAuditLog.objects.create', side_effect=DatabaseError('audit')):
            result = self.client.put(self.url + '/points-terms', terms, format='json')
        self.assertEqual((result.status_code, result.data['errorCode']), (503, 'SETUP_RESULT_UNCERTAIN'))
        with store_context(self.branch):
            self.assertEqual(current_rules()['version'], 0)
        self.assertTrue(PlatformAuditEvent.objects.filter(action='setup.points_terms_uncertain').exists())

    def test_capacity_write_and_confirmation_reuse_store_rules_and_identify_platform_actor(self):
        with store_context(self.original):
            self.assertFalse(SystemConfig.objects.filter(key=CONFIG_KEY).exists())
        data = self.client.get(self.url).data['data']
        self.assertEqual((data['store']['slug'], data['capacity']['version']), (self.branch.slug, 0))
        changed = self.client.put(self.url + '/capacity', {'max_concurrent_orders': 4, 'version': 0}, format='json')
        self.assertEqual(changed.status_code, 200, changed.data)
        with store_context(self.branch):
            self.assertIn('"max_concurrent_orders":4', SystemConfig.objects.get(key=CONFIG_KEY).value)
            audit = AdminAuditLog.objects.get(resource='provider-capacity')
            self.assertIsNone(audit.operator_account_id)
            self.assertEqual(audit.request_body['platform_account_id'], str(self.actor.pk))
            operation = audit.request_body['operation_id']
        self.assertTrue(PlatformAuditEvent.objects.filter(account=self.actor, action='setup.capacity_result',
                         detail__operation_id=operation).exists())
        with store_context(self.original):
            self.assertFalse(SystemConfig.objects.filter(key=CONFIG_KEY).exists())
        stale = self.client.put(self.url + '/capacity', {'max_concurrent_orders': 8, 'version': 0}, format='json')
        self.assertEqual(stale.status_code, 409, stale.data)
        self.assertTrue(PlatformAuditEvent.objects.filter(account=self.actor, action='setup.capacity_failed').exists())
        rows = {row['key']: row for row in self.client.get(self.url).data['data']['evidence']}
        snapshot = rows['capacity']['snapshot']
        confirmed = self.client.post(self.url + '/confirm/capacity', {'fingerprint': snapshot['fingerprint']}, format='json')
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        with store_context(self.branch):
            evidence = {row['key']: row for row in selected_configuration_evidence({'dictionary:view'})}
            self.assertEqual(evidence['capacity']['status'], 'CONFIRMED')
            confirmation = AdminAuditLog.objects.get(resource='store-setup-confirmation', object_id='capacity')
            self.assertEqual(confirmation.request_body['actor_kind'], 'platform')
            self.assertIsNone(confirmation.operator_account_id)
        self.assertEqual(self.client.put(self.url + '/capacity', {'max_concurrent_orders': 5, 'version': 1}, format='json').status_code, 200)
        self.assertEqual(self.client.post(self.url + '/confirm/capacity', {'fingerprint': snapshot['fingerprint']}, format='json').status_code, 409)

    @override_settings(VIP_MONTHLY_DECISIONS_ENABLED=True)
    def test_monthly_cutover_is_store_local_one_time_and_audited(self):
        from coupons.models import VipMonthlyCutover
        self.assertEqual(self.client.get(self.url).data['data']['monthly_cutover']['configured'], False)
        _, finance = self.platform('monthly-finance', 'FINANCE')
        self.assertEqual(finance.post(self.url + '/monthly-cutover', {'confirm': True}, format='json').status_code, 403)
        self.assertEqual(self.client.post(self.url + '/monthly-cutover', {'confirm': False}, format='json').status_code, 400)
        result = self.client.post(self.url + '/monthly-cutover', {'confirm': True}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        with store_context(self.branch):
            marker = VipMonthlyCutover.objects.get(pk=1)
            audit = AdminAuditLog.objects.get(resource='vip-monthly-cutover')
            self.assertEqual((marker.platform_actor_id, audit.request_body['operation_id']), (str(self.actor.pk), marker.operation_id))
            stamp = marker.activated_at
        self.assertTrue(PlatformAuditEvent.objects.filter(account=self.actor, action='setup.monthly_cutover_result',
                         detail__operation_id=marker.operation_id).exists())
        self.assertEqual(self.client.post(self.url + '/monthly-cutover', {'confirm': True}, format='json').status_code, 409)
        with store_context(self.branch):
            self.assertEqual(VipMonthlyCutover.objects.get(pk=1).activated_at, stamp)
        with store_context(self.original):
            self.assertFalse(VipMonthlyCutover.objects.exists())

    @override_settings(VIP_MONTHLY_DECISIONS_ENABLED=True)
    def test_monthly_cutover_audit_failure_rolls_back_marker(self):
        from coupons.models import VipMonthlyCutover
        with patch('stores.platform_store_setup.AdminAuditLog.objects.create', side_effect=IntegrityError('audit failure')):
            response = self.client.post(self.url + '/monthly-cutover', {'confirm': True}, format='json')
        self.assertNotEqual(response.status_code, 200)
        with store_context(self.branch):
            self.assertFalse(VipMonthlyCutover.objects.exists())

    @override_settings(VIP_MONTHLY_DECISIONS_ENABLED=False)
    def test_monthly_cutover_cannot_start_without_new_rule(self):
        from coupons.models import VipMonthlyCutover
        response = self.client.post(self.url + '/monthly-cutover', {'confirm': True}, format='json')
        self.assertEqual(response.status_code, 409)
        with store_context(self.branch):
            self.assertFalse(VipMonthlyCutover.objects.exists())

    def test_authority_and_inactive_or_uninitialized_store(self):
        _, finance = self.platform('setup-finance', 'FINANCE')
        _, scoped = self.platform('setup-scoped-super', 'SUPER_ADMIN', scope=self.branch.slug)
        for client in (finance, scoped):
            self.assertEqual(client.get(self.url).status_code, 403)
            self.assertEqual(client.put(self.url + '/capacity', {'max_concurrent_orders': 4, 'version': 0}, format='json').status_code, 403)
            self.assertEqual(client.put(self.url + '/clocks', {'aftersale_hours': 48, 'income_freeze_hours': 24, 'expected_version': 0}, format='json').status_code, 403)
            self.assertEqual(client.put(self.url + '/deposit/targets', {'cash_target': 50000, 'earnings_target': 90000, 'expected_version': 0}, format='json').status_code, 403)
            self.assertEqual(client.put(self.url + '/withdraw', {'min_amount': 1000, 'balance_threshold': 5000, 'tax_rate': 2, 'fingerprint': 'a' * 64}, format='json').status_code, 403)
        self.branch.initialized_at = None; self.branch.status = Store.Status.PROVISIONING
        self.branch.save(update_fields=['initialized_at', 'status'])
        self.assertEqual(self.client.get(self.url).status_code, 409)
        self.assertEqual(self.client.put(self.url + '/capacity', {'max_concurrent_orders': 4, 'version': 0}, format='json').status_code, 409)

    def test_platform_clocks_use_shared_versions_and_real_platform_audit(self):
        payload = {'aftersale_hours': 48, 'income_freeze_hours': 24, 'expected_version': 0}
        result = self.client.put(self.url + '/clocks', payload, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['data']['version'], 1)
        self.assertEqual(self.client.put(self.url + '/clocks', payload, format='json').status_code, 409)
        self.assertEqual(self.client.put(self.url + '/clocks', {**payload, 'aftersale_hours': True}, format='json').status_code, 400)
        with store_context(self.branch):
            audit = AdminAuditLog.objects.get(resource='settlement-clocks')
            self.assertIsNone(audit.operator_account_id)
            self.assertEqual(audit.request_body['platform_account_id'], str(self.actor.pk))
            self.assertEqual(audit.request_body['after']['aftersale_hours'], 48)
            self.assertTrue(PlatformAuditEvent.objects.filter(account=self.actor, action='setup.clocks_result',
                detail__operation_id=audit.request_body['operation_id']).exists())
        data = self.client.get(self.url).data['data']
        self.assertEqual((data['clocks']['aftersale_hours'], data['clocks']['income_freeze_hours']), (48, 24))
        with patch('stores.platform_store_setup.AdminAuditLog.objects.create', side_effect=IntegrityError('audit failure')):
            result = self.client.put(self.url + '/clocks', {'aftersale_hours': 10, 'income_freeze_hours': 10,
                                                          'expected_version': 1}, format='json')
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.client.get(self.url).data['data']['clocks']['aftersale_hours'], 48)

    def test_platform_confirms_other_configurations_using_their_real_snapshots(self):
        values = {
            'settlement_clock_rules': json.dumps({'aftersale_hours': 72, 'income_freeze_hours': 72, 'version': 1}),
            'provider_deposit_targets': json.dumps({'cash_target': 40000, 'earnings_target': 80000, 'version': 1}),
            'provider_deposit_income_percent': '0',
            'min_withdraw_amount': '1000', 'withdraw_balance_threshold': '5000', 'withdraw_tax_rate': '0',
        }
        with store_context(self.branch):
            for key, value in values.items():
                SystemConfig.objects.update_or_create(key=key, defaults={'value': value})
        rows = {row['key']: row for row in self.client.get(self.url).data['data']['evidence']}
        for key in ('settlement_clocks', 'deposit_targets', 'deposit_percent', 'withdraw'):
            response = self.client.post(self.url + '/confirm/' + key,
                {'fingerprint': rows[key]['snapshot']['fingerprint']}, format='json')
            self.assertEqual(response.status_code, 200, (key, response.data))
        rows = {row['key']: row for row in self.client.get(self.url).data['data']['evidence']}
        self.assertTrue(all(rows[key]['status'] == 'CONFIRMED'
                            for key in ('settlement_clocks', 'deposit_targets', 'deposit_percent', 'withdraw')))
        with store_context(self.branch):
            self.assertEqual(AdminAuditLog.objects.filter(resource='store-setup-confirmation',
                             request_body__actor_kind='platform').count(), 4)

    def test_platform_deposit_updates_keep_store_isolation_and_reject_old_snapshots(self):
        initial = self.client.get(self.url).data['data']['deposit']
        self.assertEqual(initial['percent_state'], 'MISSING')
        payload = {'cash_target': 50000, 'earnings_target': 90000, 'expected_version': initial['targets']['version']}
        changed = self.client.put(self.url + '/deposit/targets', payload, format='json')
        self.assertEqual(changed.status_code, 200, changed.data)
        self.assertEqual(self.client.put(self.url + '/deposit/targets', payload, format='json').status_code, 409)
        change_percent = {'deposit_percent': 25, 'fingerprint': initial['percent_fingerprint']}
        self.assertEqual(self.client.put(self.url + '/deposit/percent', change_percent, format='json').status_code, 200)
        self.assertEqual(self.client.put(self.url + '/deposit/percent', change_percent, format='json').status_code, 409)
        fresh = self.client.get(self.url).data['data']['deposit']
        with store_context(self.branch):
            row = SystemConfig.objects.get(key=DEPOSIT_PERCENT_KEY)
            row.value = '50'; row.save(update_fields=['value', 'updated_at'])
            row.value = '25'; row.save(update_fields=['value', 'updated_at'])
            self.assertEqual(AdminAuditLog.objects.filter(resource__in=['deposit-targets', 'deposit-percent'],
                operator_account__isnull=True, request_body__platform_account_id=str(self.actor.pk)).count(), 2)
        self.assertEqual(self.client.put(self.url + '/deposit/percent', {'deposit_percent': 80,
            'fingerprint': fresh['percent_fingerprint']}, format='json').status_code, 409)
        with store_context(self.original):
            self.assertFalse(SystemConfig.objects.filter(key__in=[DEPOSIT_TARGET_KEY, DEPOSIT_PERCENT_KEY]).exists())
        with patch('stores.platform_store_setup.AdminAuditLog.objects.create', side_effect=IntegrityError('audit failure')):
            result = self.client.put(self.url + '/deposit/targets', {**payload, 'cash_target': 60000, 'expected_version': 1}, format='json')
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.client.get(self.url).data['data']['deposit']['targets']['cash_target'], 50000)

    def test_invalid_deposit_percent_is_identified_before_repair(self):
        with store_context(self.branch):
            SystemConfig.objects.create(key=DEPOSIT_PERCENT_KEY, value='invalid')
        deposit = self.client.get(self.url).data['data']['deposit']
        self.assertEqual((deposit['percent_state'], deposit['targets']['deposit_percent']), ('INVALID', 60))
        response = self.client.put(self.url + '/deposit/percent', {'deposit_percent': 60,
                                   'fingerprint': deposit['percent_fingerprint']}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.client.get(self.url).data['data']['deposit']['percent_state'], 'VALID')

    def test_platform_withdrawal_uses_shared_lock_and_checks_full_configuration_identity(self):
        initial = self.client.get(self.url).data['data']['withdrawal']
        payload = {'min_amount': 1000, 'balance_threshold': 5000, 'tax_rate': 2,
                   'fingerprint': initial['fingerprint']}
        result = self.client.put(self.url + '/withdraw', payload, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(self.client.put(self.url + '/withdraw', payload, format='json').status_code, 409)
        self.assertEqual(self.client.put(self.url + '/withdraw', {**payload, 'tax_rate': True}, format='json').status_code, 400)
        current = self.client.get(self.url).data['data']['withdrawal']
        with store_context(self.branch):
            row = SystemConfig.objects.get(key=MIN_WITHDRAW_AMOUNT_KEY)
            row.value = '2000'; row.save(update_fields=['value', 'updated_at'])
            row.value = '1000'; row.save(update_fields=['value', 'updated_at'])
            audit = AdminAuditLog.objects.get(resource='withdraw-rules')
            self.assertIsNone(audit.operator_account_id)
            self.assertEqual(audit.request_body['platform_account_id'], str(self.actor.pk))
        self.assertEqual(self.client.put(self.url + '/withdraw', {**payload, 'fingerprint': current['fingerprint']}, format='json').status_code, 409)
        current = self.client.get(self.url).data['data']['withdrawal']
        with patch('stores.platform_store_setup.AdminAuditLog.objects.create', side_effect=IntegrityError('audit failure')):
            result = self.client.put(self.url + '/withdraw', {**payload, 'min_amount': 3000, 'fingerprint': current['fingerprint']}, format='json')
        self.assertEqual(result.status_code, 503)
        self.assertEqual(self.client.get(self.url).data['data']['withdrawal']['rules']['min_amount'], 1000)
        with store_context(self.original):
            self.assertFalse(SystemConfig.objects.filter(key=MIN_WITHDRAW_AMOUNT_KEY).exists())

    def test_withdrawal_preview_never_pairs_old_rules_with_new_fingerprint(self):
        from wallet.withdraw_rules import withdrawal_setup_snapshot
        with patch('wallet.withdraw_rules.withdraw_config_fingerprint', side_effect=['old', 'new', 'new', 'new']), \
             patch('wallet.withdraw_rules.withdraw_rule_snapshot', side_effect=[{'min_amount': 1}, {'min_amount': 2}]):
            self.assertEqual(withdrawal_setup_snapshot(), {'rules': {'min_amount': 2}, 'fingerprint': 'new'})
        with patch('wallet.withdraw_rules.withdraw_config_fingerprint', side_effect=['a', 'b'] * 3), \
             patch('wallet.withdraw_rules.withdraw_rule_snapshot', return_value={'min_amount': 1}):
            with self.assertRaises(ValueError):
                withdrawal_setup_snapshot()

    def test_target_audit_failure_rolls_back_and_platform_result_failure_is_uncertain(self):
        with patch('console.capacity_config.AdminAuditLog.objects.create', side_effect=IntegrityError('audit failure')):
            result = self.client.put(self.url + '/capacity', {'max_concurrent_orders': 4, 'version': 0}, format='json')
        self.assertEqual(result.status_code, 409)
        with store_context(self.branch):
            self.assertFalse(SystemConfig.objects.filter(key=CONFIG_KEY).exists())
        real_create = PlatformAuditEvent.objects.create
        def record(**kwargs):
            if kwargs['action'] == 'setup.capacity_result':
                raise DatabaseError('platform audit failure')
            return real_create(**kwargs)
        with patch('stores.platform_store_setup.PlatformAuditEvent.objects.create', side_effect=record):
            result = self.client.put(self.url + '/capacity', {'max_concurrent_orders': 4, 'version': 0}, format='json')
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.data['errorCode'], 'SETUP_RESULT_UNCERTAIN')
        with store_context(self.branch):
            self.assertTrue(SystemConfig.objects.filter(key=CONFIG_KEY).exists())
            self.assertTrue(AdminAuditLog.objects.filter(resource='provider-capacity').exists())
        self.assertTrue(PlatformAuditEvent.objects.filter(account=self.actor, action='setup.capacity_requested').exists())
        self.assertTrue(PlatformAuditEvent.objects.filter(account=self.actor, action='setup.capacity_uncertain').exists())
