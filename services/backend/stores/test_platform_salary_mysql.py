from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from wallet.test_deposit_lifecycle_mysql import require_isolated
from .models import PlatformAccount, PlatformAuditEvent, PlatformRoleGrant, PlatformSalaryConfirmation
from .platform_auth import issue_tokens


@skipUnless(connection.vendor == 'mysql' and 'store_b' in connections, 'Requires disposable MySQL')
@override_settings(PLATFORM_IDENTITY_ENABLED=True, ALLOWED_HOSTS=['*'])
class PlatformSalaryMySQLTests(TransactionTestCase):
    databases = {'default', 'store_b'}

    def test_concurrent_repeated_confirmation_is_one_platform_expense(self):
        require_isolated(self)
        actor = PlatformAccount(username='salary-race', phone='13800001111', is_phone_verified=True)
        actor.set_password('Synthetic-Password-2026!'); actor.save()
        PlatformRoleGrant.objects.create(account=actor, role='FINANCE', scope_slug='*')
        token, _ = issue_tokens(actor, 'SMS', actor.phone)
        url = f'/api/admin/platform/salaries/{actor.pk}'
        client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        data = {'month': '2026-11', 'base_fen': 360000, 'performance_fen': 240000}
        self.assertEqual(client.put(url, data, format='json').status_code, 200)
        barrier = Barrier(2)

        def confirm():
            close_old_connections()
            try:
                client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
                barrier.wait(timeout=10)
                return client.post(url + '/confirm', {'month': '2026-11'}, format='json').status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.submit(confirm), pool.submit(confirm)
            self.assertEqual((first.result(timeout=30), second.result(timeout=30)), (200, 200))
        self.assertEqual(PlatformSalaryConfirmation.objects.filter(account=actor).count(), 1)
        self.assertEqual(PlatformAuditEvent.objects.filter(action='platform_salary.confirm').count(), 1)
