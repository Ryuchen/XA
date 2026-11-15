from unittest import skipUnless

from django.conf import settings
from django.test import TestCase, override_settings

from club_accounts.services import get_or_create_account_for_legacy_user
from console import test_payroll_late_activity as salary_fixture
from orders.tests.factories import make_user
from orders.tests.test_addon_configuration import staff_client
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from wallet.models import RechargeRecord


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], REQUIRE_RECHARGE_PROOF=False)
class LatePayrollStoresTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_same_account_and_recharge_ids_use_each_store_original_rate(self):
        observed = []
        for index, store in enumerate((resolve_store(), xinan_store())):
            with store_context(store):
                salary = salary_fixture.LateActivityPayrollTest(
                    methodName='test_confirmed_month_late_exam_and_aftersale_paid_once_using_original_terms')
                salary.setUp()
                salary.plan.recharge_bps = 500 + 500 * index
                salary.plan.save(update_fields=['recharge_bps'])
                salary.assign(salary.original_text)
                salary.confirm(salary.original_text)
                salary.assign(salary.current_text)
                buyer = get_or_create_account_for_legacy_user(make_user())
                finance = staff_client(['wallet:recharge'])
                response = finance.post('/api/admin/wallets/recharge/', {'account_id': buyer.pk,
                    'amount': 20000, 'trade_no': 'same-store-local-trade', 'client_request_id': 'same-recharge'},
                    format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                receipt = RechargeRecord.objects.get(trade_no='same-store-local-trade')
                RechargeRecord.objects.filter(pk=receipt.pk).update(created_at=salary.original_time)
                result = salary.client.post('/api/admin/salary-activities/', {'kind': 'RECHARGE',
                    'account': salary.worker.pk, 'source_id': receipt.pk, 'reason': '核实本店原月充值',
                    'client_request_id': 'same-late-activity'}, format='json')
                self.assertEqual(result.status_code, 201, result.data)
                paid = salary.confirm(salary.current_text)
                observed.append((salary.worker.pk, receipt.pk, paid['totals']['late_adjustments_fen']))
        self.assertEqual(observed[0][:2], observed[1][:2])
        self.assertEqual([item[2] for item in observed], [1000, 2000])
