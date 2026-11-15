import calendar
from datetime import date
from unittest.mock import patch

from django.db import DatabaseError
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from console.dictionary_models import BusinessDictionaryItem
from console.payroll_models import OperatingCost, PayrollRun
from .context import current_store, resolve_store, store_context
from .models import (PlatformAccount, PlatformAuditEvent, PlatformRoleGrant,
                     PlatformSalaryConfirmation, PlatformSalaryPlan)
from .platform_auth import issue_tokens
from .tests_hosts import xinan_store


@override_settings(PLATFORM_IDENTITY_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class PlatformSalaryTests(TransactionTestCase):
    databases = {'default', 'store_b'}

    def make_actor(self, username, role, scope='*'):
        actor = PlatformAccount(username=username, phone='13800001111', is_phone_verified=True)
        actor.set_password('Synthetic-Password-2026!')
        actor.save()
        PlatformRoleGrant.objects.create(account=actor, role=role, scope_slug=scope)
        token, _ = issue_tokens(actor, 'SMS', actor.phone)
        return actor, APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)

    def setUp(self):
        self.first, self.second = resolve_store(), xinan_store()
        self.owner, self.admin = self.make_actor('pay-owner', 'SUPER_ADMIN')
        self.finance, self.client = self.make_actor('pay-finance', 'FINANCE')
        self.scoped, self.scoped_client = self.make_actor('pay-scoped', 'FINANCE', 'xinan')
        self.month = '2026-11'

    def setup_store_roi(self, store, cost):
        with store_context(store):
            account = ClubAccount.objects.create_account(username='shop-operator', password='test-password', account_type='STAFF')
            category, _ = BusinessDictionaryItem.objects.get_or_create(type='COST_CATEGORY', code='AD_SPEND',
                                                                       defaults={'name': '投流'})
            for day in range(1, calendar.monthrange(2026, 11)[1] + 1):
                OperatingCost.objects.create(day=date(2026, 11, day), category=category,
                    amount_fen=cost if day == 1 else 0, created_by=account)
            PayrollRun.objects.create(month=date(2026, 11, 1), fingerprint='0' * 64,
                                      snapshot={'daily_costs': []}, confirmed_by=account)

    def test_explicit_roster_confirmation_once_and_real_store_aggregation(self):
        for store, cost in ((self.first, 100), (self.second, 200)):
            self.setup_store_roi(store, cost)
        url = f'/api/admin/platform/salaries/{self.finance.pk}'
        self.assertEqual(self.scoped_client.put(url, {'month': self.month, 'base_fen': 100, 'performance_fen': 0}, format='json').status_code, 403)
        self.assertEqual(self.admin.put(f'/api/admin/platform/salaries/{self.scoped.pk}', {
            'month': self.month, 'base_fen': 100, 'performance_fen': 0}, format='json').status_code, 400)
        roster_url = '/api/admin/platform/salaries/roster'
        self.assertEqual(self.admin.put(roster_url, {'month': self.month,
            'account_ids': [str(self.scoped.pk)], 'scope_confirmed': True}, format='json').status_code, 400)
        self.assertEqual(self.admin.put(roster_url, {'month': self.month, 'account_ids': [str(self.finance.pk)],
                                                   'scope_confirmed': True}, format='json').status_code, 200)
        pending = self.client.get('/api/admin/platform/salaries', {'month': self.month}).data['data']
        self.assertEqual(pending['missing_plans'], [str(self.finance.pk)])
        unclosed = self.client.get('/api/admin/platform/ledger', {'month': self.month}).data['data']
        self.assertFalse(unclosed['complete'])
        self.assertIsNone(unclosed['profit_fen'])
        data = {'month': self.month, 'base_fen': 360000, 'performance_fen': 240000}
        self.assertEqual(self.admin.put(url, data, format='json').status_code, 200)
        self.assertEqual(self.client.post(url + '/confirm', {'month': self.month}, format='json').status_code, 200)
        self.assertEqual(self.client.post(url + '/confirm', {'month': self.month}, format='json').status_code, 200)
        self.assertEqual(PlatformSalaryConfirmation.objects.filter(account=self.finance).count(), 1)
        self.assertEqual(self.admin.put(url, data, format='json').status_code, 409)
        close = '/api/admin/platform/salaries/close'
        self.assertEqual(self.admin.post(close, {'month': self.month, 'account_ids': [], 'roster_confirmed': True}, format='json').status_code, 409)
        payload = {'month': self.month, 'account_ids': [str(self.finance.pk)], 'roster_confirmed': True}
        late_finance, _ = self.make_actor('late-finance', 'FINANCE')
        self.assertEqual(self.admin.post(close, payload, format='json').status_code, 409)
        PlatformRoleGrant.objects.filter(account=late_finance).update(is_active=False)
        self.assertEqual(self.admin.post(close, payload, format='json').status_code, 200)
        self.assertEqual(self.admin.post(close, payload, format='json').status_code, 200)
        report = self.client.get('/api/admin/platform/ledger', {'month': self.month}).data['data']
        self.assertTrue(report['complete'], report)
        self.assertEqual(report['platform_salary_confirmed_fen'], 600000)
        self.assertEqual(report['total_cost_fen'], 600300)
        self.assertEqual(report['profit_fen'], -600300)
        self.assertEqual(len(report['stores']), 2)
        self.assertEqual(PlatformAuditEvent.objects.filter(action='platform_salary.confirm').count(), 1)
        self.assertEqual(PlatformSalaryPlan.objects.filter(month=date(2026, 11, 1)).count(), 1)
        from console.payroll_calculation import operating_roi
        def unavailable(first, last):
            if current_store().slug == 'xinan':
                raise DatabaseError('isolated failure')
            return operating_roi(first, last)
        with patch('console.payroll_calculation.operating_roi', side_effect=unavailable):
            incomplete = self.client.get('/api/admin/platform/ledger', {'month': self.month}).data['data']
        self.assertFalse(incomplete['complete'])
        self.assertIsNone(incomplete['profit_fen'])
        self.assertEqual([row['status'] for row in incomplete['stores']], ['OK', 'UNAVAILABLE'])

    def test_missing_pay_cannot_close_and_unknown_roi_cannot_become_zero(self):
        self.setup_store_roi(self.first, 100)
        missing_finance, _ = self.make_actor('missing-finance', 'FINANCE')
        roster = [str(self.finance.pk), str(missing_finance.pk)]
        self.assertEqual(self.admin.put('/api/admin/platform/salaries/roster', {'month': self.month,
            'account_ids': roster, 'scope_confirmed': True}, format='json').status_code, 200)
        url = f'/api/admin/platform/salaries/{self.finance.pk}'
        self.assertEqual(self.client.put(url, {'month': self.month, 'base_fen': 100, 'performance_fen': 0}, format='json').status_code, 200)
        close = self.admin.post('/api/admin/platform/salaries/close', {
            'month': self.month, 'account_ids': [str(self.finance.pk)], 'roster_confirmed': True}, format='json')
        self.assertEqual(close.status_code, 409)
        report = self.client.get('/api/admin/platform/ledger', {'month': self.month}).data['data']
        self.assertFalse(report['complete'])
        self.assertIsNone(report['total_cost_fen'])
        self.assertEqual(report['platform_salary_missing_plans'], [str(missing_finance.pk)])
        self.assertEqual(set(report['platform_salary_unconfirmed_accounts']), set(roster))
