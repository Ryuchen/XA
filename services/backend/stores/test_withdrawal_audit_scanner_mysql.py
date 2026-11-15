from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import DatabaseError, close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings

from stores import test_platform_withdraw_workflow as fixture
from stores.context import store_context
from stores.models import PlatformAuditEvent, PlatformWithdrawalAuditReceipt
from stores.withdrawal_audit_tasks import scan_store
from wallet.models import WithdrawalPlatformEvent, WithdrawRequest
from wallet.services import get_wallet
from wallet.test_deposit_lifecycle_mysql import require_isolated


@skipUnless(connection.vendor == 'mysql' and 'store_b' in connections, 'Requires disposable MySQL')
@override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False, LOGIN_OTP_REQUIRED=False,
                   PLATFORM_IDENTITY_ENABLED=True, LICENSE_ACCOUNT_ADMISSION_ENABLED=False,
                   WITHDRAW_AUDIT_AUTO_RECOVERY_ENABLED=True)
class WithdrawalAuditScannerMySQLTests(TransactionTestCase):
    databases = {'default', 'store_b'}
    apply = fixture.PlatformWithdrawalWorkflowTest.apply
    initial = fixture.PlatformWithdrawalWorkflowTest.initial
    action = fixture.PlatformWithdrawalWorkflowTest.action

    def setUp(self):
        require_isolated(self)
        fixture.PlatformWithdrawalWorkflowTest.setUp(self)

    def test_concurrent_scans_recover_one_audit_from_actual_control_write_failure(self):
        pk = self.apply(); self.initial(pk)
        def fail_audit_insert(execute, sql, params, many, context):
            if sql.lstrip().upper().startswith('INSERT') and 'stores_platformauditevent' in sql.lower():
                raise DatabaseError('simulated control audit write outage')
            return execute(sql, params, many, context)
        with connections['default'].execute_wrapper(fail_audit_insert):
            result = self.action(pk, 'approve', client_request_id='scanner-race')
        self.assertEqual(result.data['data']['audit_status'], 'PENDING')
        barrier = Barrier(2)
        def scan():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return scan_store.run(self.second.pk)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.submit(scan), pool.submit(scan)
            results = [first.result(timeout=30), second.result(timeout=30)]
        self.assertEqual([row['status'] for row in results], ['OK', 'OK'])
        self.assertEqual(sum(row['recovered'] for row in results), 1)
        self.assertEqual(PlatformWithdrawalAuditReceipt.objects.filter(store=self.second).count(), 1)
        self.assertEqual(PlatformAuditEvent.objects.filter(action='withdraw.approve', target_id=str(pk)).count(), 1)
        with store_context(self.second):
            request = WithdrawRequest.objects.get(pk=pk); wallet = get_wallet(account=self.account)
            self.assertEqual((request.status, wallet.balance, wallet.frozen_amount), ('PLATFORM_APPROVED', 80000, 20000))
            self.assertEqual(WithdrawalPlatformEvent.objects.filter(withdrawal_id=pk).count(), 1)
