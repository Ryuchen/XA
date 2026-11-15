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
from .models import (PlatformAccount, PlatformAuditEvent, PlatformRoleGrant, PlatformSalarySupplement,
                     PlatformSalarySupplementPayment)
from .platform_auth import issue_tokens


@skipUnless(connection.vendor == 'mysql' and 'store_b' in connections, 'Requires disposable MySQL')
@override_settings(PLATFORM_IDENTITY_ENABLED=True, ALLOWED_HOSTS=['*'])
class PlatformSalarySupplementMySQLTests(TransactionTestCase):
    databases = {'default', 'store_b'}

    def test_concurrent_supplement_and_payment_retry_each_record_once(self):
        require_isolated(self)
        owner = PlatformAccount(username='supplement-owner', phone='13800001111', is_phone_verified=True)
        owner.set_password('Synthetic-Password-2026!'); owner.save()
        PlatformRoleGrant.objects.create(account=owner, role='SUPER_ADMIN', scope_slug='*')
        target = PlatformAccount(username='supplement-target', phone='13800001112', is_phone_verified=True)
        target.set_password('Synthetic-Password-2026!'); target.save()
        PlatformRoleGrant.objects.create(account=target, role='FINANCE', scope_slug='*')
        token, _ = issue_tokens(owner, 'SMS', owner.phone)
        client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        today = timezone.localdate()
        previous = date(today.year - (today.month == 1), 12 if today.month == 1 else today.month - 1, 1).strftime('%Y-%m')
        roster = {'month': previous, 'account_ids': [str(target.pk)]}
        url = f'/api/admin/platform/salaries/{target.pk}'
        self.assertEqual(client.put('/api/admin/platform/salaries/roster', {**roster, 'scope_confirmed': True}, format='json').status_code, 200)
        self.assertEqual(client.put(url, {'month': previous, 'base_fen': 0, 'performance_fen': 0}, format='json').status_code, 200)
        self.assertEqual(client.post(url + '/confirm', {'month': previous}, format='json').status_code, 200)
        self.assertEqual(client.post('/api/admin/platform/salaries/close', {**roster, 'roster_confirmed': True}, format='json').status_code, 200)
        target.is_active = False; target.save(update_fields=['is_active'])
        self.assertEqual(client.post(url + '/separation', {'request_id': str(uuid4()), 'ended_on': today.isoformat(),
            'reason': '人工核对离职结清', 'scope_confirmed': True}, format='json').status_code, 200)

        def compete(path, data):
            barrier = Barrier(2)
            def submit():
                close_old_connections()
                try:
                    local = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
                    barrier.wait(timeout=10)
                    result = local.post(path, data, format='json')
                    return result.status_code, result.data
                finally:
                    close_old_connections()
            with ThreadPoolExecutor(max_workers=2) as pool:
                a, b = pool.submit(submit), pool.submit(submit)
                return a.result(timeout=30), b.result(timeout=30)

        data = {'request_id': str(uuid4()), 'account_id': str(target.pk), 'original_month': previous,
                'basis_reference': 'synthetic-missed-wage-proof', 'reason': '离职后发现漏发', 'amount_fen': 5000}
        left, right = compete('/api/admin/platform/salary-supplements', data)
        self.assertEqual((left[0], right[0]), (200, 200))
        self.assertEqual(left[1]['data']['id'], right[1]['data']['id'])
        self.assertEqual(PlatformSalarySupplement.objects.count(), 1)
        self.assertEqual(PlatformAuditEvent.objects.filter(action='platform_salary.supplement').count(), 1)
        payment = {'request_id': str(uuid4()), 'amount_fen': 5000, 'reference': 'synthetic-supplement-payment',
                   'paid_on': today.isoformat(), 'payment_confirmed': True}
        left, right = compete('/api/admin/platform/salary-supplements/' + left[1]['data']['id'] + '/payments', payment)
        self.assertEqual((left[0], right[0]), (200, 200))
        self.assertEqual(left[1]['data']['id'], right[1]['data']['id'])
        self.assertEqual(PlatformSalarySupplementPayment.objects.count(), 1)
        self.assertEqual(PlatformAuditEvent.objects.filter(action='platform_salary.supplement_payment').count(), 1)
