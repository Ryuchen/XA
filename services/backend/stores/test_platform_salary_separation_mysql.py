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
from .models import (PlatformAccount, PlatformAuditEvent, PlatformRoleGrant,
                     PlatformSalaryPayment, PlatformSalarySeparation)
from .platform_auth import issue_tokens


@skipUnless(connection.vendor == 'mysql' and 'store_b' in connections, 'Requires disposable MySQL')
@override_settings(PLATFORM_IDENTITY_ENABLED=True, ALLOWED_HOSTS=['*'])
class PlatformSalarySeparationMySQLTests(TransactionTestCase):
    databases = {'default', 'store_b'}

    def test_payment_and_separation_concurrent_retries_are_idempotent(self):
        require_isolated(self)
        owner = PlatformAccount(username='separation-owner', phone='13800001111', is_phone_verified=True)
        owner.set_password('Synthetic-Password-2026!'); owner.save()
        PlatformRoleGrant.objects.create(account=owner, role='SUPER_ADMIN', scope_slug='*')
        target = PlatformAccount(username='separation-target', phone='13800001112', is_phone_verified=True)
        target.set_password('Synthetic-Password-2026!'); target.save()
        PlatformRoleGrant.objects.create(account=target, role='FINANCE', scope_slug='*')
        token, _ = issue_tokens(owner, 'SMS', owner.phone)
        client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        now = timezone.localdate()
        month = date(now.year - (now.month == 1), 12 if now.month == 1 else now.month - 1, 1).strftime('%Y-%m')
        salary = f'/api/admin/platform/salaries/{target.pk}'
        roster = {'month': month, 'account_ids': [str(target.pk)]}
        self.assertEqual(client.put('/api/admin/platform/salaries/roster', {**roster, 'scope_confirmed': True}, format='json').status_code, 200)
        self.assertEqual(client.put(salary, {'month': month, 'base_fen': 10000, 'performance_fen': 0}, format='json').status_code, 200)
        self.assertEqual(client.post(salary + '/confirm', {'month': month}, format='json').status_code, 200)
        self.assertEqual(client.post('/api/admin/platform/salaries/close', {**roster, 'roster_confirmed': True}, format='json').status_code, 200)
        payment = {'request_id': str(uuid4()), 'account_id': str(target.pk), 'month': month, 'amount_fen': 10000,
                   'reference': 'synthetic-payment-race', 'paid_on': now.isoformat(), 'payment_confirmed': True}

        def compete(url, body):
            barrier = Barrier(2)
            def invoke():
                close_old_connections()
                try:
                    local = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
                    barrier.wait(timeout=10)
                    result = local.post(url, body, format='json')
                    return result.status_code, result.data
                finally:
                    close_old_connections()
            with ThreadPoolExecutor(max_workers=2) as pool:
                a, b = pool.submit(invoke), pool.submit(invoke)
                return a.result(timeout=30), b.result(timeout=30)

        left, right = compete('/api/admin/platform/salary-payments', payment)
        self.assertEqual((left[0], right[0]), (200, 200))
        self.assertEqual(left[1]['data']['id'], right[1]['data']['id'])
        self.assertEqual(PlatformSalaryPayment.objects.count(), 1)
        target.is_active = False; target.save(update_fields=['is_active'])
        separation = {'request_id': str(uuid4()), 'ended_on': now.isoformat(), 'reason': '人工核对离职结清', 'scope_confirmed': True}
        left, right = compete(salary + '/separation', separation)
        self.assertEqual((left[0], right[0]), (200, 200))
        self.assertEqual(left[1]['data']['id'], right[1]['data']['id'])
        self.assertEqual(PlatformSalarySeparation.objects.count(), 1)
        self.assertEqual(PlatformAuditEvent.objects.filter(action='platform_salary.separation').count(), 1)
