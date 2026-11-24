import os
from unittest import skipUnless
from unittest.mock import patch

from django.db import DatabaseError, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from stores import test_platform_withdraw_workflow as fixture
from stores.context import store_context
from stores.models import (PlatformAuditEvent, PlatformWithdrawalAuditReceipt,
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
        def fail_control(_execute, _sql, _params, _many, _context):
            raise DatabaseError('simulated full control outage')
        with connections['default'].execute_wrapper(fail_control):
            result = scan_store.run(self.second.pk)
        self.assertEqual(result['status'], 'CONTROL_UNAVAILABLE')
        self.assertEqual(PlatformWithdrawalAuditScanCursor.objects.get(store=self.second).last_event_id, before)
        self.second.status = Store.Status.DISABLED; self.second.save(update_fields=['status'])
        self.first.initialized_at = timezone.now(); self.first.save(update_fields=['initialized_at'])
        with patch('stores.withdrawal_audit_tasks.scan_store.apply_async') as enqueue, \
                patch('stores.withdrawal_audit_tasks.MAX_STORES', 1):
            self.assertEqual(dispatch.run()['queued'], 1)
            self.assertEqual(dispatch.run()['queued'], 1)
        scheduled = [call.kwargs['args'][0] for call in enqueue.call_args_list]
        self.assertEqual(scheduled, [self.first.pk, self.second.pk])
