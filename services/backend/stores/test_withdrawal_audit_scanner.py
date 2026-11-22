import os
from unittest import skipUnless
from unittest.mock import patch

from django.db import DatabaseError, InterfaceError, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from kombu.exceptions import OperationalError as BrokerOperationalError

from stores import test_platform_withdraw_workflow as fixture
from stores.context import store_context
from stores.models import (PlatformAuditEvent, PlatformWithdrawalAuditDispatchCursor, PlatformWithdrawalAuditReceipt,
                           PlatformWithdrawalAuditScanCursor, Store)
from stores.withdrawal_audit_tasks import dispatch, scan_store
from wallet.models import Transaction, WithdrawalPlatformEvent, WithdrawRequest
from wallet.services import get_wallet


@override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False, LOGIN_OTP_REQUIRED=False,
                   PLATFORM_IDENTITY_ENABLED=True, LICENSE_ACCOUNT_ADMISSION_ENABLED=False,
                   WITHDRAW_AUDIT_AUTO_RECOVERY_ENABLED=True)
class WithdrawalAuditScannerTests(TransactionTestCase):
    databases = {'default', 'store_b'}
    apply = fixture.PlatformWithdrawalWorkflowTest.apply
    initial = fixture.PlatformWithdrawalWorkflowTest.initial
    action = fixture.PlatformWithdrawalWorkflowTest.action

    def setUp(self):
        fixture.PlatformWithdrawalWorkflowTest.setUp(self)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_scans_bounded_batches_and_does_not_replay_money_even_for_disabled_store(self):
        pk = self.apply(); self.initial(pk)
        def fail_audit_insert(execute, sql, params, many, context):
            if sql.lstrip().upper().startswith('INSERT') and 'stores_platformauditevent' in sql.lower():
                raise DatabaseError('simulated control audit write outage')
            return execute(sql, params, many, context)
        with connections['default'].execute_wrapper(fail_audit_insert):
            self.assertEqual(self.action(pk, 'approve', client_request_id='scan-approve').data['code'], 0)
            self.assertEqual(self.action(pk, 'mark-unknown', client_request_id='scan-unknown', reason='人工待核对').data['code'], 0)
            self.assertEqual(self.action(pk, 'paid', client_request_id='scan-paid', payout_reference='synthetic-transfer',
                                         paid_at=timezone.now().isoformat()).data['code'], 0)
        self.second.status = Store.Status.DISABLED
        self.second.save(update_fields=['status'])
        with store_context(self.second):
            events = list(WithdrawalPlatformEvent.objects.filter(withdrawal_id=pk).order_by('pk'))
            self.assertEqual(len(events), 3)
            wallet = get_wallet(account=self.account)
            self.assertEqual((wallet.balance, wallet.frozen_amount), (80000, 0))
        with patch('stores.withdrawal_audit_tasks.MAX_EVENTS', 1):
            for index, event in enumerate(events):
                result = scan_store.run(self.second.pk)
                self.assertEqual((result['status'], result['scanned'], result['recovered']), ('OK', 1, 1))
                self.assertEqual(result['next_after'], event.pk if index < 2 else 0)
            again = scan_store.run(self.second.pk)
            self.assertEqual((again['already_recorded'], again['recovered']), (1, 0))
        self.assertEqual(PlatformWithdrawalAuditReceipt.objects.filter(store=self.second).count(), 3)
        self.assertEqual(PlatformAuditEvent.objects.filter(action__in=['withdraw.approve', 'withdraw.mark-unknown', 'withdraw.paid']).count(), 3)
        self.assertFalse(PlatformWithdrawalAuditReceipt.objects.filter(store=self.first).exists())
        with store_context(self.second):
            row = WithdrawRequest.objects.get(pk=pk); wallet.refresh_from_db()
            self.assertEqual((row.status, wallet.balance, wallet.frozen_amount, row.transaction.status), ('PAID', 80000, 0, 'SUCCESS'))
            self.assertEqual(Transaction.objects.filter(tx_type='WITHDRAW_TAX', amount=1000).count(), 1)

    def test_failure_does_not_move_cursor_and_dispatch_includes_disabled(self):
        pk = self.apply(); self.initial(pk)
        self.action(pk, 'approve', client_request_id='scan-existing')
        initial = scan_store.run(self.second.pk)
        self.assertEqual(initial['status'], 'OK')
        before = PlatformWithdrawalAuditScanCursor.objects.get(store=self.second).last_event_id
        dispatch_cursor = PlatformWithdrawalAuditDispatchCursor.objects.create(pk=1, last_store_id=self.first.pk)
        for error in (DatabaseError, InterfaceError):
            def fail_control(_execute, _sql, _params, _many, _context):
                raise error('simulated full control outage')

            with self.subTest(error=error.__name__):
                with connections['default'].execute_wrapper(fail_control):
                    result = scan_store.run(self.second.pk)
                    dispatched = dispatch.run()
                self.assertEqual(result['status'], 'CONTROL_UNAVAILABLE')
                self.assertEqual(dispatched, {'status': 'CONTROL_UNAVAILABLE', 'queued': 0})
                self.assertEqual(PlatformWithdrawalAuditScanCursor.objects.get(store=self.second).last_event_id, before)
                dispatch_cursor.refresh_from_db()
                self.assertEqual(dispatch_cursor.last_store_id, self.first.pk)

        def fail_claim_lookup(execute, sql, params, many, context):
            if sql.lstrip().upper().startswith('SELECT') and 'stores_storeprovisioningclaim' in sql.lower():
                raise DatabaseError('simulated unavailable control routing claim')
            return execute(sql, params, many, context)

        store_row = Store.objects.filter(pk=self.second.pk)
        store_row.update(database_alias='tenant_xinan')
        try:
            with self.settings(DYNAMIC_STORE_DATABASE_ENABLED=True):
                with connections['default'].execute_wrapper(fail_claim_lookup):
                    result = scan_store.run(self.second.pk)
            self.assertEqual(result, {'status': 'CONTROL_UNAVAILABLE', 'store_id': self.second.pk})
            self.assertEqual(PlatformWithdrawalAuditScanCursor.objects.get(store=self.second).last_event_id, before)
        finally:
            store_row.update(database_alias=self.second.database_alias)

        self.second.status = Store.Status.DISABLED; self.second.save(update_fields=['status'])
        self.first.initialized_at = timezone.now(); self.first.save(update_fields=['initialized_at'])
        dispatch_cursor.last_store_id = 0
        dispatch_cursor.save(update_fields=['last_store_id'])
        with patch('stores.withdrawal_audit_tasks.scan_store.apply_async') as enqueue, \
                patch('stores.withdrawal_audit_tasks.MAX_STORES', 1):
            self.assertEqual(dispatch.run()['queued'], 1)
            self.assertEqual(dispatch.run()['queued'], 1)
        scheduled = [call.kwargs['args'][0] for call in enqueue.call_args_list]
        self.assertEqual(scheduled, [self.first.pk, self.second.pk])

    def test_store_disconnect_preserves_progress_before_retry(self):
        pk = self.apply()
        self.initial(pk)
        self.assertEqual(self.action(pk, 'approve', client_request_id='scan-approved').status_code, 200)

        def fail_audit_insert(execute, sql, params, many, context):
            if sql.lstrip().upper().startswith('INSERT') and 'stores_platformauditevent' in sql.lower():
                raise DatabaseError('simulated control audit write outage')
            return execute(sql, params, many, context)

        with connections['default'].execute_wrapper(fail_audit_insert):
            response = self.action(pk, 'mark-unknown', client_request_id='scan-unknown-connection', reason='等待核实')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['audit_status'], 'PENDING')
        cursor = PlatformWithdrawalAuditScanCursor.objects.create(store=self.second)
        with patch('stores.withdrawal_audit_tasks.MAX_EVENTS', 1):
            first = scan_store.run(self.second.pk)
        self.assertGreater(first['next_after'], 0)
        self.assertEqual(first['already_recorded'], 1)

        for failing_read in (1, 2):
            reads = 0

            def disconnect(execute, sql, params, many, context):
                nonlocal reads
                if sql.lstrip().upper().startswith('SELECT') and 'wallet_withdrawalplatformevent' in sql.lower():
                    reads += 1
                    if reads == failing_read:
                        raise InterfaceError('simulated lost tenant connection')
                return execute(sql, params, many, context)

            with self.subTest(failing_read=failing_read):
                with connections[self.second.database_alias].execute_wrapper(disconnect):
                    result = scan_store.run(self.second.pk)
                self.assertEqual(reads, failing_read)
                self.assertEqual(result, {'status': 'STORE_UNAVAILABLE', 'store_id': self.second.pk})
                cursor.refresh_from_db()
                self.assertEqual(cursor.last_event_id, first['next_after'])
                self.assertEqual(PlatformWithdrawalAuditReceipt.objects.filter(store=self.second).count(), 1)

        recovered = scan_store.run(self.second.pk)
        self.assertEqual((recovered['status'], recovered['recovered'], recovered['next_after']), ('OK', 1, 0))
        self.assertEqual(PlatformWithdrawalAuditReceipt.objects.filter(store=self.second).count(), 2)
        with store_context(self.second):
            row = WithdrawRequest.objects.select_related('transaction').get(pk=pk)
            wallet = get_wallet(account=self.account)
            self.assertEqual((row.status, row.transaction.status, wallet.balance, wallet.frozen_amount),
                             ('PAYMENT_UNKNOWN', 'PENDING', 80000, 20000))

    def test_dispatch_retries_same_batch_after_unconfirmed_publish(self):
        cursor = PlatformWithdrawalAuditDispatchCursor.objects.create(pk=1, last_store_id=self.first.pk)
        third = Store.objects.create(slug='audit-third', name='隔离第三店', database_alias='store_a',
                                     status=Store.Status.ACTIVE, initialized_at=timezone.now())
        fourth = Store.objects.create(slug='audit-fourth', name='隔离第四店', database_alias='store_new',
                                      status=Store.Status.ACTIVE, initialized_at=timezone.now())
        self.second.status = Store.Status.DISABLED
        self.second.save(update_fields=['status'])
        with (patch('stores.withdrawal_audit_tasks.MAX_STORES', 2),
              patch('stores.withdrawal_audit_tasks.scan_store.apply_async',
                    side_effect=[None, BrokerOperationalError('publisher lost its connection')]) as send):
            result = dispatch.run()
        self.assertEqual(result, {'status': 'ENQUEUE_UNCONFIRMED', 'queued': 1, 'enqueue_unknown': [third.pk]})
        self.assertEqual([call.kwargs['args'][0] for call in send.call_args_list], [self.second.pk, third.pk])
        cursor.refresh_from_db()
        self.assertEqual(cursor.last_store_id, self.first.pk)

        with (patch('stores.withdrawal_audit_tasks.MAX_STORES', 2),
              patch('stores.withdrawal_audit_tasks.scan_store.apply_async') as send):
            self.assertEqual(dispatch.run(), {'status': 'OK', 'queued': 2, 'next_after': third.pk})
            self.assertEqual(dispatch.run(), {'status': 'OK', 'queued': 1, 'next_after': 0})
        self.assertEqual([call.kwargs['args'][0] for call in send.call_args_list],
                         [self.second.pk, third.pk, fourth.pk])

    def test_dispatch_reports_confirmed_sends_if_control_cursor_cannot_be_saved(self):
        cursor = PlatformWithdrawalAuditDispatchCursor.objects.create(pk=1, last_store_id=self.first.pk)

        def fail_cursor_update(execute, sql, params, many, context):
            if (sql.lstrip().upper().startswith('UPDATE')
                    and 'stores_platformwithdrawalauditdispatchcursor' in sql.lower()):
                raise InterfaceError('control connection lost after publish')
            return execute(sql, params, many, context)

        with (patch('stores.withdrawal_audit_tasks.scan_store.apply_async') as send,
              connections['default'].execute_wrapper(fail_cursor_update)):
            result = dispatch.run()
        self.assertEqual(result, {'status': 'CONTROL_UNAVAILABLE', 'queued': 1})
        send.assert_called_once_with(args=[self.second.pk])
        cursor.refresh_from_db()
        self.assertEqual(cursor.last_store_id, self.first.pk)
