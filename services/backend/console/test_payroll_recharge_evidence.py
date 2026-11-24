from unittest import skipUnless

from django.db import connection
from django.test import TestCase, override_settings

from club_accounts.services import get_or_create_account_for_legacy_user
from console.test_payroll_late_activity import LateActivityPayrollTest
from orders.tests.factories import make_user
from orders.tests.test_addon_configuration import staff_client
from wallet.models import RechargeRecord, Transaction


@skipUnless(connection.vendor == 'mysql',
            '工资确认依赖 MySQL 工资事件屏障（18-盘点工资屏障待联调）；本模块同时作为 barrier_mysql 簇夹具')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, REQUIRE_RECHARGE_PROOF=False, ALLOWED_HOSTS=['*'],
                   PAYROLL_REFUND_ACCOUNTING_INTERNAL_TESTING=True, PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True)
class PayrollRechargeEvidenceTest(TestCase):
    def setUp(self):
        self.salary = LateActivityPayrollTest(methodName='test_late_recharge_is_paid_once_at_original_rate_and_keeps_rounding')
        self.salary.setUp()
        self.salary.plan.recharge_bps = 500
        self.salary.plan.save(update_fields=['recharge_bps'])
        self.salary.assign(self.salary.current_text)
        buyer = get_or_create_account_for_legacy_user(make_user())
        finance = staff_client(['wallet:recharge'])
        response = finance.post('/api/admin/wallets/recharge/', {'account_id': buyer.pk, 'amount': 20000,
            'trade_no': 'payroll-evidence', 'client_request_id': 'payroll-evidence'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.recharge = RechargeRecord.objects.get(trade_no='payroll-evidence')

    def attribute(self):
        return self.salary.client.post('/api/admin/salary-activities/', {'kind': 'RECHARGE',
            'account': self.salary.worker.pk, 'source_id': self.recharge.pk, 'reason': '核实充值归属'}, format='json')

    def test_changed_receipt_blocks_confirmation_and_restored_receipt_can_confirm(self):
        created = self.attribute()
        self.assertEqual(created.status_code, 201, created.data)
        before = self.salary.preview(self.salary.current_text)
        self.assertTrue(before['can_confirm'], before)
        self.assertEqual(before['totals']['recharge_commission_fen'], 1000)
        tx = self.recharge.recharge_tx
        Transaction.objects.filter(pk=tx.pk).update(amount=tx.amount + 1)
        invalid = self.salary.preview(self.salary.current_text)
        self.assertFalse(invalid['can_confirm'])
        self.assertTrue(any('原始凭证不一致' in warning for warning in invalid['warnings']))
        blocked = self.salary.client.post('/api/admin/payroll/confirm/', {
            'month': self.salary.current_text, 'fingerprint': invalid['fingerprint']}, format='json')
        self.assertEqual(blocked.status_code, 409, blocked.data)
        Transaction.objects.filter(pk=tx.pk).update(amount=tx.amount)
        confirmed = self.salary.confirm(self.salary.current_text)
        self.assertEqual(confirmed['totals']['recharge_commission_fen'], 1000)
        Transaction.objects.filter(pk=tx.pk).update(amount=tx.amount + 1)
        self.assertEqual(self.salary.preview(self.salary.current_text)['totals'], confirmed['totals'])

    def test_duplicate_receipt_cannot_be_attributed_or_confirmed(self):
        self.assertEqual(self.attribute().status_code, 201)
        RechargeRecord.objects.create(account=self.recharge.account, user=self.recharge.user,
            amount=self.recharge.amount, recharge_tx=self.recharge.recharge_tx, trade_no='duplicate-proof')
        self.assertFalse(self.salary.preview(self.salary.current_text)['can_confirm'])
        self.assertEqual(self.attribute().status_code, 404)

    def test_unattributed_invalid_receipt_does_not_disappear_as_zero(self):
        tx = self.recharge.recharge_tx
        Transaction.objects.filter(pk=tx.pk).update(balance_after=tx.balance_after + 1)
        self.assertEqual(self.attribute().status_code, 404)
        preview = self.salary.preview(self.salary.current_text)
        self.assertFalse(preview['can_confirm'])
        self.assertTrue(any('未知充值业绩' in warning for warning in preview['warnings']))

    @override_settings(PAYROLL_REFUND_ACCOUNTING_INTERNAL_TESTING=True)
    def test_valid_recharge_with_internal_refund_accounting_enabled(self):
        response = self.attribute()
        self.assertEqual(response.status_code, 201, response.data)
        confirmed = self.salary.confirm(self.salary.current_text)
        self.assertEqual(confirmed['totals']['recharge_commission_fen'], 1000)
