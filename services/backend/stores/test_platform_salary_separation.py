from datetime import date
from uuid import uuid4

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (PlatformAccount, PlatformAuditEvent, PlatformRoleGrant, PlatformSalaryAdjustment,
                     PlatformSalaryAdjustmentWriteOff, PlatformSalaryConfirmation, PlatformSalaryPayment,
                     PlatformSalarySeparation)
from .platform_auth import issue_tokens


@override_settings(PLATFORM_IDENTITY_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class PlatformSalarySeparationTests(TestCase):
    def setUp(self):
        self.owner = PlatformAccount(username='separation-owner', phone='13800001111', is_phone_verified=True)
        self.owner.set_password('Synthetic-Password-2026!'); self.owner.save()
        PlatformRoleGrant.objects.create(account=self.owner, role='SUPER_ADMIN', scope_slug='*')
        self.staff = PlatformAccount(username='separation-finance', phone='13800001112', is_phone_verified=True)
        self.staff.set_password('Synthetic-Password-2026!'); self.staff.save()
        PlatformRoleGrant.objects.create(account=self.staff, role='FINANCE', scope_slug='*')
        token, _ = issue_tokens(self.owner, 'SMS', self.owner.phone)
        self.client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        self.current = timezone.localdate().replace(day=1)
        self.previous = date(self.current.year - (self.current.month == 1),
                             12 if self.current.month == 1 else self.current.month - 1, 1)
        self.salary_url = f'/api/admin/platform/salaries/{self.staff.pk}'
        self.separation_url = self.salary_url + '/separation'

    def salary(self, month, value):
        month = month.strftime('%Y-%m')
        roster = {'month': month, 'account_ids': [str(self.staff.pk)]}
        result = self.client.put('/api/admin/platform/salaries/roster', {**roster, 'scope_confirmed': True}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        result = self.client.put(self.salary_url, {'month': month, 'base_fen': value, 'performance_fen': 0}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        result = self.client.post(self.salary_url + '/confirm', {'month': month}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        result = self.client.post('/api/admin/platform/salaries/close', {**roster, 'roster_confirmed': True}, format='json')
        self.assertEqual(result.status_code, 200, result.data)

    def adjustment(self, source):
        return self.client.post('/api/admin/platform/salary-adjustments', {'request_id': str(uuid4()),
            'account_id': str(self.staff.pk), 'original_month': self.previous.strftime('%Y-%m'),
            'kind': 'DEBIT', 'amount_fen': 15000, 'reason': '退款提成核对', 'deduction_source': source}, format='json')

    def separation(self, request_id):
        return self.client.post(self.separation_url, {'request_id': str(request_id),
            'ended_on': timezone.localdate().isoformat(), 'reason': '核对离职与线下工资记录', 'scope_confirmed': True}, format='json')

    def test_unpaid_blocks_until_manual_payments_then_refund_debt_written_off_once(self):
        self.salary(self.previous, 20000)
        self.assertEqual(self.adjustment('REFUND_COMMISSION').status_code, 200)
        self.salary(self.current, 10000)
        self.staff.is_active = False; self.staff.save(update_fields=['is_active'])
        request_id = uuid4()
        before = self.separation(request_id)
        self.assertEqual(before.status_code, 409, before.data)
        self.assertEqual(before.data['data']['unpaid_salaries'][0]['unpaid_fen'], 20000)
        self.assertEqual(PlatformSalarySeparation.objects.count(), 0)
        self.assertEqual(PlatformSalaryAdjustmentWriteOff.objects.count(), 0)
        payment_url = '/api/admin/platform/salary-payments'
        payment = {'request_id': str(uuid4()), 'account_id': str(self.staff.pk),
                   'month': self.previous.strftime('%Y-%m'), 'amount_fen': 12000,
                   'reference': 'synthetic-transfer-1', 'paid_on': timezone.localdate().isoformat(), 'payment_confirmed': True}
        first = self.client.post(payment_url, payment, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(self.client.post(payment_url, payment, format='json').data['data']['id'], first.data['data']['id'])
        self.assertEqual(self.client.post(payment_url, {**payment, 'amount_fen': 12001}, format='json').status_code, 409)
        self.assertEqual(self.separation(request_id).status_code, 409)
        second = {**payment, 'request_id': str(uuid4()), 'amount_fen': 8000, 'reference': 'synthetic-transfer-2'}
        self.assertEqual(self.client.post(payment_url, second, format='json').status_code, 200)
        self.assertEqual(self.client.post(payment_url, {**second, 'request_id': str(uuid4()), 'reference': 'extra-transfer'}, format='json').status_code, 409)
        done = self.separation(request_id)
        self.assertEqual(done.status_code, 200, done.data)
        self.assertEqual(self.separation(request_id).data['data']['id'], done.data['data']['id'])
        self.assertEqual(PlatformSalaryPayment.objects.count(), 2)
        self.assertEqual(PlatformSalaryAdjustmentWriteOff.objects.get().amount_fen, 5000)
        self.assertEqual(PlatformAuditEvent.objects.filter(action='platform_salary.separation').count(), 1)
        original = PlatformSalaryConfirmation.objects.get(account=self.staff, month=self.previous)
        self.assertEqual(original.net_fen, 20000)
        adjustment = PlatformSalaryAdjustment.objects.get(account=self.staff)
        self.assertEqual(adjustment.applications.get().amount_fen, 10000)
        self.assertEqual(self.client.post('/api/admin/platform/salary-adjustments', {
            'request_id': str(uuid4()), 'account_id': str(self.staff.pk), 'original_month': self.previous.strftime('%Y-%m'),
            'kind': 'DEBIT', 'amount_fen': 1, 'reason': '退款提成', 'deduction_source': 'REFUND_COMMISSION'}, format='json').status_code, 409)

    def test_disabled_alone_does_not_complete_or_write_off_other_deductions(self):
        self.salary(self.previous, 0)
        self.assertEqual(self.adjustment('OTHER').status_code, 200)
        self.staff.is_active = False; self.staff.save(update_fields=['is_active'])
        self.assertFalse(PlatformSalarySeparation.objects.filter(account=self.staff).exists())
        result = self.separation(uuid4())
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.data['data']['other_outstanding_adjustments'][0]['remaining_fen'], 15000)
        self.assertFalse(PlatformSalaryAdjustmentWriteOff.objects.exists())
