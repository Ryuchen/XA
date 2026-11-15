from datetime import date
from uuid import uuid4

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (PlatformAccount, PlatformAuditEvent, PlatformRoleGrant, PlatformSalaryAdjustment,
                     PlatformSalaryConfirmation)
from .platform_auth import issue_tokens
from .platform_salary_adjustments import next_month


@override_settings(PLATFORM_IDENTITY_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class PlatformSalaryAdjustmentTests(TestCase):
    def setUp(self):
        self.actor = PlatformAccount(username='platform-finance-adjust', phone='13800001111', is_phone_verified=True)
        self.actor.set_password('Synthetic-Password-2026!'); self.actor.save()
        PlatformRoleGrant.objects.create(account=self.actor, role='FINANCE', scope_slug='*')
        token, _ = issue_tokens(self.actor, 'SMS', self.actor.phone)
        self.client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        self.current = timezone.localdate().replace(day=1)
        self.original = date(self.current.year - (self.current.month == 1),
                             12 if self.current.month == 1 else self.current.month - 1, 1)
        self.url = f'/api/admin/platform/salaries/{self.actor.pk}'

    def open_period(self, month, base):
        value = month.strftime('%Y-%m')
        roster = {'month': value, 'account_ids': [str(self.actor.pk)], 'scope_confirmed': True}
        result = self.client.put('/api/admin/platform/salaries/roster', roster, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        result = self.client.put(self.url, {'month': value, 'base_fen': base, 'performance_fen': 0}, format='json')
        self.assertEqual(result.status_code, 200, result.data)

    def confirm(self, month):
        result = self.client.post(self.url + '/confirm', {'month': month.strftime('%Y-%m')}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        return result.data['data']

    def close(self, month):
        result = self.client.post('/api/admin/platform/salaries/close', {
            'month': month.strftime('%Y-%m'), 'account_ids': [str(self.actor.pk)], 'roster_confirmed': True,
        }, format='json')
        self.assertEqual(result.status_code, 200, result.data)

    def test_adjustments_carry_forward_and_preserve_original_snapshot(self):
        self.open_period(self.original, 20000)
        original = self.confirm(self.original)
        self.close(self.original)
        request_id = str(uuid4())
        data = {'request_id': request_id, 'account_id': str(self.actor.pk),
                'original_month': self.original.strftime('%Y-%m'), 'kind': 'DEBIT',
                'amount_fen': 15000, 'reason': '已确认提成核对后应扣减', 'deduction_source': 'REFUND_COMMISSION'}
        url = '/api/admin/platform/salary-adjustments'
        first = self.client.post(url, data, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data['data']['due_month'], self.current.strftime('%Y-%m'))
        repeated = self.client.post(url, data, format='json')
        self.assertEqual(repeated.data['data']['id'], first.data['data']['id'])
        self.assertEqual(self.client.post(url, {**data, 'amount_fen': 16000}, format='json').status_code, 409)
        credit = self.client.post(url, {**data, 'request_id': str(uuid4()), 'kind': 'CREDIT', 'deduction_source': '',
                                       'amount_fen': 2000, 'reason': '遗漏业绩补发'}, format='json')
        self.assertEqual(credit.status_code, 200, credit.data)
        self.open_period(self.current, 10000)
        current = self.confirm(self.current)
        self.assertEqual((current['adjustment_increase_fen'], current['adjustment_deduction_fen'], current['net_fen']),
                         (2000, 12000, 0))
        self.assertEqual(self.confirm(self.current)['net_fen'], 0)
        adjustment = PlatformSalaryAdjustment.objects.get(request_id=request_id)
        self.assertEqual(sum(row.amount_fen for row in adjustment.applications.all()), 12000)
        self.close(self.current)
        following = next_month(self.current)
        self.open_period(following, 8000)
        result = self.confirm(following)
        self.assertEqual((result['adjustment_increase_fen'], result['adjustment_deduction_fen'], result['net_fen']), (0, 3000, 5000))
        self.assertEqual(sum(row.amount_fen for row in adjustment.applications.all()), 15000)
        saved = PlatformSalaryConfirmation.objects.get(account=self.actor, month=self.original)
        self.assertEqual((original['net_fen'], saved.net_fen, saved.base_fen), (20000, 20000, 20000))
        self.assertEqual(PlatformAuditEvent.objects.filter(action='platform_salary.adjust').count(), 2)

    def test_unclosed_origin_and_unknown_month_cannot_be_adjusted(self):
        data = {'request_id': str(uuid4()), 'account_id': str(self.actor.pk),
                'original_month': self.original.strftime('%Y-%m'), 'kind': 'DEBIT',
                'amount_fen': 1, 'reason': '核对', 'deduction_source': 'REFUND_COMMISSION'}
        self.assertEqual(self.client.post('/api/admin/platform/salary-adjustments', data, format='json').status_code, 409)
