from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep
from unittest import skipUnless
from uuid import uuid4
from unittest.mock import patch

from django.db import DatabaseError, close_old_connections, connection, connections
from django.test import override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from console.payroll_event_barrier import lock_payment_refund_events
from console.payroll_models import PayrollRun
from orders.tests.factories import make_console_user
from stores.context import store_context
from stores.db import transaction
from stores.models import PlatformAuditEvent
from stores.test_platform_store_payroll import PlatformStorePayrollTests


@skipUnless(connection.vendor == 'mysql', 'Requires independently isolated MySQL')
@override_settings(PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True, PAYROLL_REFUND_ACCOUNTING_ENABLED=True)
class PlatformStorePayrollConfirmMySQLTests(PlatformStorePayrollTests):
    test_scoped_read_configure_preview_and_audit = None
    test_platform_creates_plan_and_employee_assignment_with_idempotent_actor = None
    test_platform_confirm_closed_without_barrier_and_refund_accounting = None
    test_store_admin_creation_keeps_real_store_actor = None
    test_store_identity_is_not_platform_identity = None
    test_super_admin_both_stores_and_uncertain_cross_database_result = None

    def setUp(self):
        super().setUp()
        from console.payroll_models import PayrollEventBarrier
        for store in (self.first, self.second):
            with store_context(store):
                PayrollEventBarrier.objects.get_or_create(pk=1)

    def test_confirm_and_duplicate_wait_for_real_refund_writer_then_commit_once(self):
        # The independent test-only settings pin two unique schemas and one
        # temporary loopback MySQL process; refuse any other test connection.
        db = connection.settings_dict
        self.assertEqual(db['USER'], 'xa_test_payroll')
        self.assertEqual(db['HOST'], '127.0.0.1')
        self.assertTrue(db['NAME'].startswith('test_xa_test_payroll_'))
        with store_context(self.second):
            user = make_console_user([])
            employee = get_or_create_account_for_legacy_user(user)
            user.admin_membership.account = employee
            user.admin_membership.save(update_fields=['account'])
        base = '/api/admin/platform/store-payroll/xinan'
        plans = self.client.get(f'{base}/plans').data['data']['list']
        plan = next(item for item in plans if item['name'] == '隔离薪资方案')
        assigned = self.client.post(f'{base}/assignments', {
            'request_id': str(uuid4()), 'month': '2026-11', 'account': employee.pk,
            'plan': plan['id'], 'expected_plan_revision': plan['revision']}, format='json')
        self.assertEqual(assigned.status_code, 200, assigned.data)
        preview = self.client.get(f'{base}/preview', {'month': '2026-11'})
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertTrue(preview.data['data']['can_confirm'], preview.data)
        body = {'request_id': str(uuid4()), 'month': '2026-11', 'fingerprint': preview.data['data']['fingerprint']}
        token = self.client.defaults['HTTP_AUTHORIZATION']
        entered = Event()
        def submit():
            close_old_connections()
            try:
                client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION=token)
                entered.set()
                response = client.post(f'{base}/confirm', body, format='json')
                return response.status_code, response.data
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            with store_context(self.second), transaction.atomic():
                lock_payment_refund_events()
                first = pool.submit(submit)
                self.assertTrue(entered.wait(10))
                self.assertFalse(first.done())
                second = pool.submit(submit)
                deadline = monotonic() + 10
                waiting = False
                while monotonic() < deadline:
                    with connections['store_b'].cursor() as cursor:
                        cursor.execute('SHOW FULL PROCESSLIST')
                        waiting = any('CONSOLE_PAYROLLEVENTBARRIER' in str(row[7]).upper()
                                      and 'FOR UPDATE' in str(row[7]).upper() for row in cursor.fetchall())
                    if waiting:
                        break
                    sleep(0.05)
                self.assertTrue(waiting, 'confirmation did not reach the held MySQL barrier')
                self.assertFalse(first.done())
                self.assertFalse(second.done())
            one, two = first.result(timeout=30), second.result(timeout=30)
        self.assertEqual(one[0], 200, one)
        self.assertEqual(two[0], 200, two)
        self.assertEqual(one[1]['data']['run_id'], two[1]['data']['run_id'])
        with store_context(self.second):
            row = PayrollRun.objects.get(month='2026-11-01')
            self.assertEqual((row.confirmed_by_id, row.confirmed_by_platform_id), (None, self.finance.pk))
            self.assertEqual(str(row.platform_request_id), body['request_id'])
            self.assertEqual(PayrollRun.objects.filter(month='2026-11-01').count(), 1)
        self.assertTrue(PlatformAuditEvent.objects.filter(account=self.finance,
            action='store_payroll.confirm_completed', target_store_slug='xinan').exists())
        invalid = self.client.post(f'{base}/confirm', {**body, 'request_id': str(uuid4())}, format='json')
        self.assertEqual(invalid.status_code, 409, invalid.data)
        with store_context(self.first):
            self.assertFalse(PayrollRun.objects.filter(month='2026-11-01').exists())

        december = self.client.post(f'{base}/assignments', {
            'request_id': str(uuid4()), 'month': '2026-12', 'account': employee.pk,
            'plan': plan['id'], 'expected_plan_revision': plan['revision']}, format='json')
        self.assertEqual(december.status_code, 200, december.data)
        preview = self.client.get(f'{base}/preview', {'month': '2026-12'}).data['data']
        self.assertTrue(preview['can_confirm'], preview)
        december_body = {'request_id': str(uuid4()), 'month': '2026-12', 'fingerprint': preview['fingerprint']}
        from console.models import AdminAuditLog
        audit_create = AdminAuditLog.objects.create
        def fail_tenant_audit(**values):
            if values.get('resource') == 'platform-store-payroll-confirm':
                raise DatabaseError('forced tenant audit failure')
            return audit_create(**values)
        with patch.object(AdminAuditLog.objects, 'create', side_effect=fail_tenant_audit):
            failed = self.client.post(f'{base}/confirm', december_body, format='json')
        self.assertEqual(failed.status_code, 503, failed.data)
        with store_context(self.second):
            self.assertFalse(PayrollRun.objects.filter(month='2026-12-01').exists())
        platform_create = PlatformAuditEvent.objects.create
        def fail_result_audit(**values):
            if values.get('action') == 'store_payroll.confirm_completed':
                raise DatabaseError('forced platform audit failure')
            return platform_create(**values)
        with patch.object(PlatformAuditEvent.objects, 'create', side_effect=fail_result_audit):
            uncertain = self.client.post(f'{base}/confirm', december_body, format='json')
        self.assertEqual(uncertain.status_code, 503, uncertain.data)
        with store_context(self.second):
            self.assertEqual(PayrollRun.objects.filter(month='2026-12-01').count(), 1)
        recovered = self.client.post(f'{base}/confirm', december_body, format='json')
        self.assertEqual(recovered.status_code, 200, recovered.data)
        with store_context(self.second):
            self.assertEqual(PayrollRun.objects.filter(month='2026-12-01').count(), 1)

    @override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, REFUND_DEPOSIT_DEFICIT_ENABLED=True,
                       KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
    def test_platform_confirmation_applies_real_cross_month_refund_deduction(self):
        from datetime import timedelta
        from console import test_payroll_late_activity as fixture_module
        from console.payroll_models import PayrollRefundApplication, PayrollRefundDeduction
        from orders.models import Order, OrderStatusLog
        from orders.tests import test_negotiation_execution
        from orders.tests.factories import make_order
        from wallet.models import Transaction
        from wallet.services import get_wallet

        with store_context(self.second):
            salary = fixture_module.LateActivityPayrollTest(methodName='test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms')
            salary.setUp()
            salary.plan.base_fen = 0
            salary.plan.dispatch_day_bps = salary.plan.dispatch_night_bps = 100
            salary.plan.save(update_fields=['base_fen', 'dispatch_day_bps', 'dispatch_night_bps'])
            salary.assign(salary.original_text)
            flow = test_negotiation_execution.NegotiationExecutionPostingTests(
                methodName='test_posts_boss_refund_wallet_and_cash_deposit_once_in_one_transaction')
            flow.setUp()
            flow.client.defaults['HTTP_HOST'] = 'admin.esports.club'
            Order.objects.filter(pk=flow.order.pk).update(created_at=salary.original_time)
            event = OrderStatusLog.objects.create(order=flow.order, action='CREATE', operator_account=salary.worker)
            OrderStatusLog.objects.filter(pk=event.pk).update(created_at=salary.original_time)
            Transaction.objects.filter(order=flow.order, tx_type='PAY').update(created_at=salary.original_time)
            attributed = salary.client.post('/api/admin/salary-activities/', {'kind': 'DISPATCH',
                'account': salary.worker.pk, 'source_id': flow.order.pk, 'reason': '核实原月派单'}, format='json')
            self.assertEqual(attributed.status_code, 201, attributed.data)
            original = salary.confirm(salary.original_text)
            salary.plan.performance_fen, salary.plan.target_fen = 10000, 10000
            salary.plan.save(update_fields=['performance_fen', 'target_fen'])
            salary.assign(salary.current_text)
            current_order = make_order(flow.boss, amount=500, customer_account=flow.boss_account,
                                       payment_status=Order.PaymentStatus.PAID)
            wallet = get_wallet(user=flow.boss, account=flow.boss_account)
            wallet.balance = 10000
            wallet.save(update_fields=['balance'])
            Transaction.objects.create(wallet=wallet, order=current_order, tx_type='PAY', amount=-500,
                balance_before=10500, balance_after=10000)
            OrderStatusLog.objects.create(order=current_order, action='CREATE', operator_account=salary.worker)
            attributed = salary.client.post('/api/admin/salary-activities/', {'kind': 'DISPATCH',
                'account': salary.worker.pk, 'source_id': current_order.pk, 'reason': '核实本月派单'}, format='json')
            self.assertEqual(attributed.status_code, 201, attributed.data)
            case, proposal = flow.accepted(1000)
            receipt = flow.execute(case, proposal)
            month = salary.current_text
        base = '/api/admin/platform/store-payroll/xinan'
        preview = self.client.get(f'{base}/preview', {'month': month})
        self.assertEqual(preview.status_code, 200, preview.data)
        snapshot = preview.data['data']
        line = next(item for item in snapshot['lines'] if item['account'] == salary.worker.pk)
        self.assertEqual((line['refund_deduction_plan']['gross_fen'], line['total_fen'],
                          line['refund_deduction_plan']['carry_fen']), (5, 0, 5))
        body = {'request_id': str(uuid4()), 'month': month, 'fingerprint': snapshot['fingerprint']}
        confirmed = self.client.post(f'{base}/confirm', body, format='json')
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        self.assertEqual(self.client.post(f'{base}/confirm', body, format='json').status_code, 200)
        with store_context(self.second):
            self.assertEqual(PayrollRefundApplication.objects.get().amount_fen, 5)
            self.assertEqual(PayrollRefundDeduction.objects.get().refund_id, receipt.refund_id)
            self.assertEqual(PayrollRun.objects.get(month=salary.original).snapshot['totals'], original['totals'])
            self.assertEqual(PayrollRun.objects.get(month=salary.current).confirmed_by_platform_id, self.finance.pk)
