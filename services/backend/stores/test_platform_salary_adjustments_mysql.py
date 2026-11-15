from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from unittest import skipUnless
from uuid import uuid4

from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from wallet.test_deposit_lifecycle_mysql import require_isolated
from .models import PlatformAccount, PlatformAuditEvent, PlatformRoleGrant, PlatformSalaryAdjustment
from .platform_auth import issue_tokens


@skipUnless(connection.vendor == 'mysql' and 'store_b' in connections, 'Requires disposable MySQL')
@override_settings(PLATFORM_IDENTITY_ENABLED=True, ALLOWED_HOSTS=['*'])
class PlatformSalaryAdjustmentMySQLTests(TransactionTestCase):
    databases = {'default', 'store_b'}

    def test_concurrent_same_request_registers_one_adjustment(self):
        require_isolated(self)
        actor = PlatformAccount(username='adjustment-race', phone='13800001111', is_phone_verified=True)
        actor.set_password('Synthetic-Password-2026!'); actor.save()
        PlatformRoleGrant.objects.create(account=actor, role='FINANCE', scope_slug='*')
        token, _ = issue_tokens(actor, 'SMS', actor.phone)
        client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        now = timezone.localdate()
        previous = date(now.year - (now.month == 1), 12 if now.month == 1 else now.month - 1, 1)
        month = previous.strftime('%Y-%m')
        roster = {'month': month, 'account_ids': [str(actor.pk)]}
        url = f'/api/admin/platform/salaries/{actor.pk}'
        self.assertEqual(client.put('/api/admin/platform/salaries/roster', {**roster, 'scope_confirmed': True}, format='json').status_code, 200)
        self.assertEqual(client.put(url, {'month': month, 'base_fen': 10000, 'performance_fen': 0}, format='json').status_code, 200)
        self.assertEqual(client.post(url + '/confirm', {'month': month}, format='json').status_code, 200)
        self.assertEqual(client.post('/api/admin/platform/salaries/close', {**roster, 'roster_confirmed': True}, format='json').status_code, 200)
        data = {'request_id': str(uuid4()), 'account_id': str(actor.pk), 'original_month': month,
                'kind': 'DEBIT', 'amount_fen': 15000, 'reason': '业绩退款核对', 'deduction_source': 'REFUND_COMMISSION'}
        barrier = Barrier(2)

        def submit():
            close_old_connections()
            try:
                local = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
                barrier.wait(timeout=10)
                result = local.post('/api/admin/platform/salary-adjustments', data, format='json')
                return result.status_code, result.data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.submit(submit), pool.submit(submit)
            left, right = first.result(timeout=30), second.result(timeout=30)
        self.assertEqual((left[0], right[0]), (200, 200))
        self.assertEqual(left[1]['data']['id'], right[1]['data']['id'])
        self.assertEqual(PlatformSalaryAdjustment.objects.count(), 1)
        self.assertEqual(PlatformAuditEvent.objects.filter(action='platform_salary.adjust').count(), 1)
