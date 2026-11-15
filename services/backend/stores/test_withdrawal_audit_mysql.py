from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import DatabaseError, close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from stores.context import store_context
from stores.models import PlatformAuditEvent, PlatformWithdrawalAuditReceipt
from stores import test_platform_withdraw_workflow as fixture
from wallet.models import Transaction, WithdrawalPlatformEvent, WithdrawRequest
from wallet.services import get_wallet
from wallet.test_deposit_lifecycle_mysql import require_isolated


@skipUnless(connection.vendor == 'mysql' and 'store_b' in connections, 'Requires disposable MySQL')
@override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False, LOGIN_OTP_REQUIRED=False,
                   PLATFORM_IDENTITY_ENABLED=True, LICENSE_ACCOUNT_ADMISSION_ENABLED=False)
class WithdrawalAuditMySQLTests(TransactionTestCase):
    databases = {'default', 'store_b'}
    apply = fixture.PlatformWithdrawalWorkflowTest.apply
    initial = fixture.PlatformWithdrawalWorkflowTest.initial
    action = fixture.PlatformWithdrawalWorkflowTest.action

    def setUp(self):
        require_isolated(self)
        fixture.PlatformWithdrawalWorkflowTest.setUp(self)

    def test_concurrent_recovery_only_one_audit_no_money_replay(self):
        pk = self.apply(); self.initial(pk)
        def fail_audit_insert(execute, sql, params, many, context):
            if sql.lstrip().upper().startswith('INSERT') and 'stores_platformauditevent' in sql.lower():
                raise DatabaseError('simulated control audit write outage')
            return execute(sql, params, many, context)
        with connections['default'].execute_wrapper(fail_audit_insert):
            created = self.action(pk, 'approve', client_request_id='audit-concurrent-original')
        self.assertEqual(created.data['code'], 0, created.data)
        self.assertEqual(created.data['data']['audit_status'], 'PENDING')
        with store_context(self.second):
            event = WithdrawalPlatformEvent.objects.get(withdrawal_id=pk, action='approve')
        url = f'/api/admin/platform/withdrawals/xinan/{pk}/audit-recover'
        data = {'event_id': event.pk, 'client_request_id': 'audit-concurrent-original'}
        with connections['default'].execute_wrapper(fail_audit_insert):
            self.assertEqual(self.f.post(url, data, format='json').status_code, 503)
        barrier = Barrier(2)
        def recover():
            close_old_connections()
            try:
                client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + self.finance_token)
                barrier.wait(timeout=10)
                result = client.post(url, data, format='json')
                return result.status_code, result.data
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            left, right = pool.submit(recover), pool.submit(recover)
            a, b = left.result(timeout=30), right.result(timeout=30)
        self.assertEqual((a[0], b[0]), (200, 200))
        self.assertEqual(sorted([a[1]['data']['recovered'], b[1]['data']['recovered']]), [False, True])
        self.assertEqual(PlatformWithdrawalAuditReceipt.objects.filter(store=self.second, tenant_event_id=event.pk).count(), 1)
        self.assertEqual(PlatformAuditEvent.objects.filter(action='withdraw.approve', target_id=str(pk)).count(), 1)
        with store_context(self.second):
            row = WithdrawRequest.objects.get(pk=pk); wallet = get_wallet(account=self.account)
            self.assertEqual((row.status, wallet.balance, wallet.frozen_amount, row.transaction.status),
                             ('PLATFORM_APPROVED', 80000, 20000, 'PENDING'))
            self.assertEqual(WithdrawalPlatformEvent.objects.filter(withdrawal_id=pk).count(), 1)
            self.assertFalse(Transaction.objects.filter(tx_type='WITHDRAW_TAX').exists())
        self.assertEqual(self.f.get(f'/api/admin/platform/withdrawals/shenglang/{pk}/audit-recover').status_code, 404)
