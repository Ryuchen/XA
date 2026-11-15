"""Real ordinary-refund APIs compete with the per-store payroll barrier."""
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep
from unittest import skipUnless
from uuid import uuid4

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.tokens import issue_account_tokens
from common.mysql_test_support import require_disposable_mysql
from console.payroll_event_barrier import lock_payroll_events
from console.payroll_models import PayrollEventBarrier
from orders.models import AfterSale, Order, OrderRefund
from orders.tests import test_dispatch_real_workflow, test_negotiation_termination
from orders.tests.test_addon_configuration import staff_client
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.models import Store
from wallet.models import Wallet


@skipUnless(connection.vendor == 'mysql', 'Requires disposable MySQL row locks')
@override_settings(PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True, LICENSE_ACCOUNT_ADMISSION_ENABLED=False,
    LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
    CELERY_TASK_ALWAYS_EAGER=False, NEGOTIATION_PROPOSAL_ENABLED=True,
    NEGOTIATION_REFUND_EXECUTION_ENABLED=True, REFUND_DEPOSIT_DEFICIT_ENABLED=True)
class OrdinaryRefundPayrollBarrierMySQLTest(TransactionTestCase):
    def _post_during_payroll(self, store, order_id, client, url, payload):
        def post():
            close_old_connections()
            try:
                with store_context(store):
                    response = client.post(url, payload, format='json')
                    return response.status_code, response.data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with store_context(store), transaction.atomic():
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
                        self.fail(f'Refund returned before reaching barrier: {pending.result()}')
                    sleep(0.05)
                self.assertTrue(found, 'Refund did not wait for the payroll event barrier')
                self.assertFalse(pending.done())
                Order.objects.select_for_update(nowait=True).get(pk=order_id)
                self.assertFalse(OrderRefund.objects.filter(order_id=order_id).exists())
            return pending.result(timeout=20)

    def test_real_cancel_refund_aftersale_and_negotiation_wait_before_order_lock(self):
        require_disposable_mysql(connection)
        Store.objects.get_or_create(slug='shenglang', defaults={'name':'测试门店', 'entity_prefix':'SL',
            'database_alias':'default', 'status':Store.Status.ACTIVE})
        store = resolve_store()
        with store_context(store):
            PayrollEventBarrier.objects.get_or_create(pk=1)
            flow = test_dispatch_real_workflow.DispatchRealWorkflowTest(methodName='test_quote_then_dispatch_to_lobby_debits_once')
            flow.client = APIClient()
            flow.setUp()
            boss = APIClient(HTTP_HOST='sl.esports.club')
            boss.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(flow.boss_account)[0])
            staff = staff_client(['order:view', 'order:refund', 'order:cancel', 'support:edit'])
            order_ids = []
            for _ in range(4):
                result = boss.post('/api/orders/create/', {'service_id':flow.service.pk,
                    'dispatch_route':'PUBLIC', 'client_request_id':str(uuid4())}, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
                order_ids.append(result.data['data']['id'])
            case = AfterSale.objects.create(order_id=order_ids[3], account=flow.boss_account,
                                             reason='核实退款')
        entries = [
            (order_ids[0], boss, f'/api/orders/orders/{order_ids[0]}/cancel/', {'reason':'客户取消'}),
            (order_ids[1], staff, f'/api/admin/orders/{order_ids[1]}/cancel/', {'reason':'后台取消'}),
            (order_ids[2], staff, f'/api/admin/orders/{order_ids[2]}/refund/', {'reason':'后台退款', 'client_request_id':'gate-admin-refund'}),
            (order_ids[3], staff, f'/api/admin/aftersales/{case.pk}/refund/', {'resolution':'售后核实退款', 'client_request_id':'gate-case-refund'}),
        ]
        for order_id, client, url, payload in entries:
            with self.subTest(url=url):
                status, data = self._post_during_payroll(store, order_id, client, url, payload)
                self.assertEqual((status, data['code']), (200, 0), data)
                with store_context(store):
                    self.assertEqual(OrderRefund.objects.filter(order_id=order_id).count(), 1)
        with store_context(store):
            self.assertEqual(Wallet.objects.get(user=flow.boss).balance, 100000)
            replay = staff.post(entries[2][2], entries[2][3], format='json')
            self.assertEqual(replay.data['code'], 0, replay.data)
            self.assertEqual(OrderRefund.objects.filter(order_id=order_ids[2]).count(), 1)

            negotiation = test_negotiation_termination.NegotiationTerminationTests(
                methodName='test_only_affected_provider_stops_and_other_provider_continues')
            self.addCleanup(negotiation.doCleanups)
            negotiation.setUp()
            case, proposal = negotiation.accepted(amount=1000)
            client = negotiation.client_for(negotiation.operator_account)
            url = f'/api/admin/aftersales/{case.pk}/negotiation-execution/'
            payload = {'proposal_id':proposal.pk, 'client_request_id':'gate-negotiation-refund'}
        status, data = self._post_during_payroll(store, negotiation.order.pk, client, url, payload)
        self.assertEqual((status, data['code']), (200, 0), data)
        with store_context(store):
            self.assertEqual(OrderRefund.objects.filter(order=negotiation.order).count(), 1)
            retry = client.post(url, payload, format='json')
            self.assertEqual(retry.data['code'], 0, retry.data)
            self.assertEqual(OrderRefund.objects.filter(order=negotiation.order).count(), 1)
