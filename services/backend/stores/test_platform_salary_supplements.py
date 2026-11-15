from uuid import uuid4
from django.utils import timezone

from .models import PlatformSalaryAdjustmentWriteOff, PlatformSalaryConfirmation, PlatformSalarySeparation, PlatformSalarySupplement
from .test_platform_salary_separation import PlatformSalarySeparationTests


class PlatformSalarySupplementTests(PlatformSalarySeparationTests):
    def test_late_supplement_is_separate_cost_and_never_implicitly_paid(self):
        self.salary(self.previous, 10000)
        self.assertEqual(self.adjustment('REFUND_COMMISSION').status_code, 200)
        payment = {'request_id': str(uuid4()), 'account_id': str(self.staff.pk),
                   'month': self.previous.strftime('%Y-%m'), 'amount_fen': 10000,
                   'reference': 'original-wage-transfer', 'paid_on': timezone.localdate().isoformat(), 'payment_confirmed': True}
        self.assertEqual(self.client.post('/api/admin/platform/salary-payments', payment, format='json').status_code, 200)
        self.staff.is_active = False; self.staff.save(update_fields=['is_active'])
        self.assertEqual(self.separation(uuid4()).status_code, 200)
        original = PlatformSalaryConfirmation.objects.get(account=self.staff, month=self.previous)
        separation = PlatformSalarySeparation.objects.get(account=self.staff)
        self.assertEqual(PlatformSalaryAdjustmentWriteOff.objects.get(separation=separation).amount_fen, 15000)
        data = {'request_id': str(uuid4()), 'account_id': str(self.staff.pk),
                'original_month': self.previous.strftime('%Y-%m'), 'basis_reference': 'missed-wage-evidence-1',
                'reason': '离职核算后发现漏发工资', 'amount_fen': 5000}
        url = '/api/admin/platform/salary-supplements'
        first = self.client.post(url, data, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        supplement = first.data['data']
        self.assertEqual((supplement['paid_fen'], supplement['unpaid_fen']), (0, 5000))
        self.assertEqual(supplement['recognition_month'], self.current.strftime('%Y-%m'))
        self.assertEqual(self.client.post(url, data, format='json').data['data']['id'], supplement['id'])
        self.assertEqual(self.client.post(url, {**data, 'amount_fen': 5001}, format='json').status_code, 409)
        self.assertEqual(self.client.post(url, {**data, 'request_id': str(uuid4())}, format='json').status_code, 409)
        roster = {'month': self.current.strftime('%Y-%m'), 'account_ids': []}
        self.assertEqual(self.client.put('/api/admin/platform/salaries/roster', {**roster, 'scope_confirmed': True}, format='json').status_code, 200)
        self.assertEqual(self.client.post('/api/admin/platform/salaries/close', {**roster, 'roster_confirmed': True}, format='json').status_code, 200)
        ledger = self.client.get('/api/admin/platform/ledger', {'month': self.current.strftime('%Y-%m')}).data['data']
        self.assertEqual((ledger['platform_salary_supplement_fen'], ledger['platform_salary_confirmed_fen']), (5000, 5000))
        second = self.client.post(url, {**data, 'request_id': str(uuid4()), 'original_month': None,
                                       'basis_reference': 'other-missing-wage-evidence'}, format='json')
        self.assertEqual(second.status_code, 200, second.data)
        self.assertNotEqual(second.data['data']['recognition_month'], self.current.strftime('%Y-%m'))
        payment_url = f"{url}/{supplement['id']}/payments"
        late_payment = {'request_id': str(uuid4()), 'amount_fen': 3000, 'reference': 'original-wage-transfer',
                        'paid_on': timezone.localdate().isoformat(), 'payment_confirmed': True}
        self.assertEqual(self.client.post(payment_url, late_payment, format='json').status_code, 409)
        late_payment['reference'] = 'late-wage-transfer-1'
        result = self.client.post(payment_url, late_payment, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(self.client.post(payment_url, late_payment, format='json').data['data']['id'], result.data['data']['id'])
        over = {**late_payment, 'request_id': str(uuid4()), 'reference': 'late-wage-transfer-2', 'amount_fen': 2001}
        self.assertEqual(self.client.post(payment_url, over, format='json').status_code, 409)
        rest = {**over, 'amount_fen': 2000}
        self.assertEqual(self.client.post(payment_url, rest, format='json').status_code, 200)
        first_record = PlatformSalarySupplement.objects.get(pk=supplement['id'])
        self.assertEqual(first_record.payments.count(), 2)
        original.refresh_from_db(); separation.refresh_from_db()
        self.assertEqual(original.net_fen, 10000)
        self.assertEqual(PlatformSalarySeparation.objects.filter(pk=separation.pk).count(), 1)
        self.assertEqual(PlatformSalaryAdjustmentWriteOff.objects.get(separation=separation).amount_fen, 15000)
