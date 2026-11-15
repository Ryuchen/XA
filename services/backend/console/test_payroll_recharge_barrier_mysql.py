"""Real recharge and payroll confirmation serialize on a disposable MySQL store."""
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient
from common.mysql_test_support import require_disposable_mysql
from console import test_payroll_late_activity as salary_fixture
from console.payroll_event_barrier import lock_payroll_events
from console.payroll_models import PayrollEventBarrier
from engagement.recharge_pricing import LOCK_KEY
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.models import Store
from wallet.models import RechargeRecord, SystemConfig, Transaction, Wallet
from wallet.services import get_wallet
from wallet import test_recharge_pricing


@skipUnless(connection.vendor == 'mysql', 'Requires disposable MySQL row locks')
@override_settings(PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True, PAYROLL_REFUND_ACCOUNTING_ENABLED=True,
    LICENSE_ACCOUNT_ADMISSION_ENABLED=False, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'],
    REQUIRE_RECHARGE_PROOF=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class PayrollRechargeBarrierMySQLTest(TransactionTestCase):
    def test_recharge_waits_before_wallet_locks_then_requires_attribution(self):
        require_disposable_mysql(connection)
        Store.objects.get_or_create(slug='shenglang', defaults={'name':'测试门店', 'entity_prefix':'SL',
            'database_alias':'default', 'status':Store.Status.ACTIVE})
        store = resolve_store()
        with store_context(store):
            PayrollEventBarrier.objects.get_or_create(pk=1)
            salary = salary_fixture.LateActivityPayrollTest(
                methodName='test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms')
            salary.setUp()
            salary.plan.recharge_bps = 500
            salary.plan.save(update_fields=['recharge_bps'])
            salary.assign(salary.current_text)
            preview = salary.preview(salary.current_text)
            self.assertTrue(preview['can_confirm'], preview['warnings'])
            pricing = test_recharge_pricing.RechargePricingTest(methodName='test_custom_bonus_credits_principal_then_approval_credits_once')
            pricing.setUp()
            wallet = get_wallet(account=pricing.boss)
            SystemConfig.objects.get_or_create(key=LOCK_KEY, defaults={'value':'', 'remark':'测试互斥行'})
            payload = {'account_id':pricing.boss.pk, 'amount':900000, 'gift_amount':40000,
                       'trade_no':'payroll-gate-recharge', 'client_request_id':'payroll-gate-recharge'}

        def recharge():
            close_old_connections()
            try:
                with store_context(store):
                    response = pricing.client.post('/api/admin/wallets/recharge/', payload, format='json')
                    return response.status_code, response.data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with store_context(store), transaction.atomic():
                lock_payroll_events()
                pending = pool.submit(recharge)
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
                        self.fail(f'Recharge returned before barrier: {pending.result()}')
                    sleep(0.05)
                self.assertTrue(found, 'Recharge did not wait for payroll barrier')
                Wallet.objects.select_for_update(nowait=True).get(pk=wallet.pk)
                SystemConfig.objects.select_for_update(nowait=True).get(key=LOCK_KEY)
                self.assertFalse(RechargeRecord.objects.filter(trade_no=payload['trade_no']).exists())
            status, data = pending.result(timeout=20)
        self.assertEqual((status, data['code']), (200, 0), data)
        self.assertTrue(data['data']['gift_approval_pending'])
        with store_context(store):
            record = RechargeRecord.objects.get(trade_no=payload['trade_no'])
            self.assertEqual(Transaction.objects.filter(pk=record.recharge_tx_id, tx_type='TOPUP').count(), 1)
            stale = salary.client.post('/api/admin/payroll/confirm/', {'month':salary.current_text,
                'fingerprint':preview['fingerprint']}, format='json')
            self.assertEqual(stale.status_code, 409, stale.data)
            missing = salary.preview(salary.current_text)
            self.assertFalse(missing['can_confirm'])
            self.assertEqual(missing['unattributed']['recharge']['count'], 1)
            assigned = salary.client.post('/api/admin/salary-activities/', {'kind':'RECHARGE',
                'account':salary.worker.pk, 'source_id':record.pk, 'reason':'核实充值归属'}, format='json')
            self.assertEqual(assigned.status_code, 201, assigned.data)
            before_gift = salary.preview(salary.current_text)
            decided = pricing.manager.post(f'/api/admin/recharge-records/{record.pk}/gift-decision/',
                                           {'decision':'APPROVED', 'reason':'核实赠送'}, format='json')
            self.assertEqual(decided.status_code, 200, decided.data)
            record.refresh_from_db(); wallet.refresh_from_db()
            self.assertEqual((record.amount, record.gift_amount, wallet.balance), (900000, 40000, 940000))
            after_gift = salary.preview(salary.current_text)
            self.assertEqual(after_gift['fingerprint'], before_gift['fingerprint'])
            self.assertEqual(after_gift['lines'][0]['recharge_amount_fen'], 900000)
            confirmed = salary.confirm(salary.current_text)
            self.assertEqual(confirmed['totals']['recharge_commission_fen'], 45000)
            replay = pricing.client.post('/api/admin/wallets/recharge/', payload, format='json')
            self.assertNotEqual(replay.data.get('code'), 0, replay.data)
            self.assertEqual(RechargeRecord.objects.filter(trade_no=payload['trade_no']).count(), 1)
