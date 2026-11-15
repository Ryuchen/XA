"""Real add-on API writes compete with the per-store payroll barrier."""
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings

from common.mysql_test_support import require_disposable_mysql
from console.payroll_event_barrier import lock_payroll_events
from console.payroll_models import PayrollEventBarrier
from orders.models import AfterSale, Order, OrderAddonPayment, OrderAddonRefund
from orders.tests import test_addon_intents, test_addon_payment, test_addon_refund_execution, test_addon_refund_flow
from orders.tests.test_addon_configuration import staff_client
from stores.context import resolve_store, store_context
from stores.models import Store
from stores.db import transaction
from wallet.models import Transaction


@skipUnless(connection.vendor == 'mysql', 'Requires disposable MySQL row locks')
@override_settings(PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True, ADDON_PAYMENT_INTERNAL_TESTING=True,
    ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True, LICENSE_ACCOUNT_ADMISSION_ENABLED=False,
    LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class AddonPayrollBarrierMySQLTest(TransactionTestCase):
    def _post_during_payroll(self, store, order_id, client, url):
        def post():
            close_old_connections()
            try:
                with store_context(store):
                    response = client.post(url, {}, format='json')
                    return response.status_code, response.data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with store_context(store), transaction.atomic():
                lock_payroll_events()
                pending = pool.submit(post)
                deadline = monotonic() + 10
                found = False
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute('SHOW FULL PROCESSLIST')
                        found = any(row[3] == connection.settings_dict['NAME'] and row[4] == 'Query'
                            and 'CONSOLE_PAYROLLEVENTBARRIER' in str(row[7] or '').upper()
                            and 'LOCK IN SHARE MODE' in str(row[7] or '').upper() for row in cursor.fetchall())
                    if found:
                        break
                    sleep(0.05)
                self.assertTrue(found, 'Actual API request did not wait for the payroll event barrier')
                self.assertFalse(pending.done())
                # No order lock may be held before the barrier, otherwise an
                # exclusive payroll reader could deadlock while locking orders.
                Order.objects.select_for_update(nowait=True).get(pk=order_id)
            return pending.result(timeout=20)

    def test_payment_and_refund_api_take_barrier_before_order_lock(self):
        require_disposable_mysql(connection)
        Store.objects.get_or_create(slug='shenglang', defaults={'name': '测试门店',
            'database_alias': 'default', 'status': Store.Status.ACTIVE, 'entity_prefix': 'SL'})
        store = resolve_store()
        with store_context(store):
            PayrollEventBarrier.objects.get_or_create(pk=1)
            fixture = test_addon_intents.AddonIntentTests(
                methodName='test_boss_intent_records_sources_snapshot_and_idempotency_without_payment')
            intent_id, wallet = test_addon_payment.prepare(fixture)
        url = fixture.url + f'{intent_id}/pay/'
        status, data = self._post_during_payroll(store, fixture.order.pk, fixture.client, url)
        self.assertEqual(status, 200, data)
        with store_context(store):
            payment = OrderAddonPayment.objects.get(intent_id=intent_id)
            wallet.refresh_from_db()
            self.assertEqual(wallet.balance, 1500)
            self.assertEqual(Transaction.objects.filter(pk=payment.buyer_transaction_id, tx_type='PAY').count(), 1)
            replay = fixture.client.post(url, {}, format='json')
            self.assertEqual(replay.status_code, 200, replay.data)
            self.assertEqual(OrderAddonPayment.objects.filter(intent_id=intent_id).count(), 1)

            refund_flow = test_addon_refund_flow.AddonRefundFlowTests(
                methodName='test_staff_boss_and_player_confirm_one_paid_addon_without_premature_refund')
            refund_flow.fixture, refund_flow.wallet, refund_flow.payment = fixture, wallet, payment
            refund_flow.case = AfterSale.objects.create(order=fixture.order, account=fixture.boss,
                reason='加购项目协商退款', affected_provider_account_ids=[fixture.provider.pk])
            refund_flow.staff = staff_client(['order:refund'])
            refund_flow.staff_url = f'/api/admin/orders/{fixture.order.pk}/addons/payments/{payment.pk}/refund-proposals/'
            refund_flow.user_url = f'/api/orders/orders/{fixture.order.pk}/addons/payments/{payment.pk}/refund-proposals/'
            refund_flow.body = {'request_id': 'addon-refund-1', 'amount_raw': 500, 'explanation': '加购部分服务未履行'}
            profile = fixture.provider_user.escort_profile
            profile.account = fixture.provider
            profile.save(update_fields=['account'])
            flow = test_addon_refund_execution.AddonRefundExecutionTests(
                methodName='test_confirmed_refunds_restore_original_sources_and_deduct_pending_earnings_once')
            flow.fixture = refund_flow
            refund_url = flow.approve('barrier-addon-refund', 500)
            refund_order = fixture.order
        status, data = self._post_during_payroll(store, refund_order.pk, flow.fixture.staff, refund_url)
        self.assertEqual(status, 200, data)
        with store_context(store):
            refund = OrderAddonRefund.objects.get(payment=flow.fixture.payment)
            self.assertEqual(refund.amount, 500)
            flow.fixture.wallet.refresh_from_db()
            self.assertEqual(flow.fixture.wallet.balance, 2000)
            replay = flow.fixture.staff.post(refund_url, {}, format='json')
            self.assertEqual(replay.status_code, 200, replay.data)
            self.assertEqual(OrderAddonRefund.objects.filter(payment=flow.fixture.payment).count(), 1)
