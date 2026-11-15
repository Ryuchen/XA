"""Real aftersale completions serialize with payroll confirmation."""
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep
from unittest import skipUnless
from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from common.mysql_test_support import require_disposable_mysql
from console import test_payroll_late_activity as salary_fixture
from console.payroll_calculation import aftersale_cycles
from console.payroll_event_barrier import lock_payroll_events
from console.payroll_models import PayrollEventBarrier
from orders.models import AfterSale, Order, SubsidyAgreementExecution
from orders.negotiation_proposals import decide
from orders.tests import test_subsidy_agreement
from orders.tests.factories import make_console_user, make_order, make_user
from orders.tests.test_addon_configuration import staff_client
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.models import Store


@skipUnless(connection.vendor == 'mysql', 'Requires disposable MySQL row locks')
@override_settings(PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True, PAYROLL_REFUND_ACCOUNTING_ENABLED=True,
    LICENSE_ACCOUNT_ADMISSION_ENABLED=False, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'],
    VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_NEGOTIATION_ENABLED=True, NEGOTIATION_PROPOSAL_ENABLED=True,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class PayrollAfterSaleBarrierMySQLTest(TransactionTestCase):
    def setUp(self):
        require_disposable_mysql(connection)
        Store.objects.get_or_create(slug='shenglang', defaults={'name':'测试门店', 'entity_prefix':'SL',
            'database_alias':'default', 'status':Store.Status.ACTIVE})
        self.store = resolve_store()
        with store_context(self.store):
            PayrollEventBarrier.objects.get_or_create(pk=1)
            self.salary = salary_fixture.LateActivityPayrollTest(
                methodName='test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms')
            self.salary.setUp()
            self.salary.plan.aftersale_fee_fen = 1000
            self.salary.plan.save(update_fields=['aftersale_fee_fen'])
            self.salary.assign(self.salary.current_text)

    def _post_during_payroll(self, order_id, case_id, client, url, payload):
        def post():
            close_old_connections()
            try:
                with store_context(self.store):
                    response = client.post(url, payload, format='json')
                    return response.status_code, response.data
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with store_context(self.store), transaction.atomic():
                lock_payroll_events()
                pending = pool.submit(post)
                deadline, found = monotonic() + 10, False
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute('SHOW FULL PROCESSLIST')
                        found = any(row[3] == connection.settings_dict['NAME'] and row[4] == 'Query'
                            and 'CONSOLE_PAYROLLEVENTBARRIER' in str(row[7] or '').upper()
                            and 'LOCK IN SHARE MODE' in str(row[7] or '').upper() for row in cursor.fetchall())
                    if found:
                        break
                    if pending.done():
                        self.fail(f'Completion returned before barrier: {pending.result()}')
                    sleep(0.05)
                self.assertTrue(found, 'Completion did not wait for payroll barrier')
                Order.objects.select_for_update(nowait=True).get(pk=order_id)
                case = AfterSale.objects.select_for_update(nowait=True).get(pk=case_id)
                self.assertEqual(case.status, 'OPEN')
            return pending.result(timeout=20)

    def _verify_stale_preview_and_attribution(self, case, preview):
        stale = self.salary.client.post('/api/admin/payroll/confirm/', {'month':self.salary.current_text,
            'fingerprint':preview['fingerprint']}, format='json')
        self.assertEqual(stale.status_code, 409, stale.data)
        case.refresh_from_db()
        cycles = aftersale_cycles(case)
        self.assertEqual(len(cycles), 1)
        missing = self.salary.preview(self.salary.current_text)
        self.assertFalse(missing['can_confirm'])
        self.assertEqual(missing['unattributed']['aftersale']['count'], 1)
        assigned = self.salary.client.post('/api/admin/salary-activities/', {'kind':'AFTERSALE',
            'account':self.salary.worker.pk, 'source_id':case.pk, 'cycle_key':cycles[0]['cycle_key'],
            'reason':'核实售后归属'}, format='json')
        self.assertEqual(assigned.status_code, 201, assigned.data)
        result = self.salary.confirm(self.salary.current_text)
        self.assertEqual(result['totals']['aftersale_commission_fen'], 1000)

    def test_staff_resolution_is_counted_once_after_wait(self):
        with store_context(self.store):
            buyer = make_user()
            account = get_or_create_account_for_legacy_user(buyer)
            order = make_order(buyer, customer_account=account, status=Order.Status.IN_SERVICE)
            case = AfterSale.objects.create(order=order, account=account, reason='咨询售后')
            operator = staff_client(['order:view', 'order:settle'])
            preview = self.salary.preview(self.salary.current_text)
            self.assertTrue(preview['can_confirm'], preview['warnings'])
        url = f'/api/admin/aftersales/{case.pk}/resolve/'
        status, data = self._post_during_payroll(order.pk, case.pk, operator, url, {'resolution':'已处理'})
        self.assertEqual((status, data['code']), (200, 0), data)
        with store_context(self.store):
            retry = operator.post(url, {'resolution':'已处理'}, format='json')
            self.assertEqual(retry.status_code, 400, retry.data)
            self._verify_stale_preview_and_attribution(case, preview)

    def test_safebox_resolution_is_counted_once_after_wait(self):
        with store_context(self.store):
            fixture = test_subsidy_agreement.SubsidyAgreementTest(
                methodName='test_execution_waits_for_both_confirmations_and_settles_once_after_clocks')
            self.addCleanup(fixture.doCleanups)
            fixture.setUp()
            operator = make_console_user(['order:view', 'order:refund'])
            account = get_or_create_account_for_legacy_user(operator)
            operator.admin_membership.account = account
            operator.admin_membership.save(update_fields=['account'])
            proposal = fixture.propose()
            decide(fixture.case.pk, actor=fixture.customer, proposal_id=proposal.pk, accepted=True)
            decide(fixture.case.pk, actor=fixture.accounts[1], proposal_id=proposal.pk, accepted=True)
            client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            payload = {'proposal_id':proposal.pk, 'client_request_id':'salary-subsidy-close'}
            url = f'/api/admin/aftersales/{fixture.case.pk}/subsidy-negotiation-execution/'
            preview = self.salary.preview(self.salary.current_text)
            self.assertTrue(preview['can_confirm'], preview['warnings'])
        status, data = self._post_during_payroll(fixture.order.pk, fixture.case.pk, client, url, payload)
        self.assertEqual((status, data['code']), (200, 0), data)
        with store_context(self.store):
            retry = client.post(url, payload, format='json')
            self.assertEqual(retry.data['code'], 0, retry.data)
            self.assertEqual(SubsidyAgreementExecution.objects.filter(order=fixture.order).count(), 1)
            self._verify_stale_preview_and_attribution(fixture.case, preview)
