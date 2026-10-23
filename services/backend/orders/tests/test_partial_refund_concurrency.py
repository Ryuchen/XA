"""Real MySQL arbitration of partial refunds, releases and withdrawal funds."""
from django.test import skipUnlessDBFeature,override_settings
from rest_framework.test import APITransactionTestCase
from orders.models import OrderRefund
from orders.tests import test_partial_refunds as partial,test_settled_refund_concurrency as concurrent
from wallet.income import release_due_income
from wallet.models import IncomeHold,Transaction,WithdrawRequest


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',LEGACY_FORCE_AUTH_COMPAT=False,REQUIRE_ORDER_EVIDENCE_IMAGES=False)
@skipUnlessDBFeature('has_select_for_update')
class PartialRefundConcurrencyTest(APITransactionTestCase):
    setUp = partial.PartialRefundTest.setUp
    client_for = partial.PartialRefundTest.client_for
    settled = partial.PartialRefundTest.settled
    ok = partial.PartialRefundTest.ok
    preview_amount = partial.PartialRefundTest.preview_amount
    pay_refund = partial.PartialRefundTest.pay_refund
    race = concurrent.SettledRefundConcurrencyTest.race

    def action(self,order,amount,key,token):
        client = self.client_for(self.admin_account)
        return lambda:client.post(f'/api/admin/orders/{order.pk}/refund/',{'refund_amount':amount,
            'reason':'MySQL部分退款','client_request_id':key,'plan_token':token},format='json').data

    def test_simultaneous_same_key_partial_refund_replays_one_committed_result(self):
        order=self.settled()
        token=self.preview_amount(order,2500)['plan_token']
        results=self.race(self.action(order,2500,'mysql-same-key',token),self.action(order,2500,'mysql-same-key',token))
        self.assertTrue(all(r['code']==0 for r in results),results)
        self.assertEqual(OrderRefund.objects.filter(order=order).count(),1)
        self.assertEqual(Transaction.objects.get(order=order,tx_type='REFUND').amount,2500)

    def test_different_keys_cannot_use_the_same_cumulative_plan_twice(self):
        order=self.settled()
        token=self.preview_amount(order,6000)['plan_token']
        results=self.race(self.action(order,6000,'mysql-first-key',token),self.action(order,6000,'mysql-second-key',token))
        self.assertEqual(sum(r['code']==0 for r in results),1,results)
        self.assertEqual(sum(r['amount'] for r in OrderRefund.objects.filter(order=order).values('amount')),6000)
        self.ok(self.pay_refund(order))
        self.assertEqual(Transaction.objects.filter(order=order,tx_type='REFUND').count(),2)

    def test_partial_refund_racing_release_only_releases_the_unrecovered_remainder(self):
        order=self.settled(hold=72)
        hold=IncomeHold.objects.get(income_transaction__order=order)
        token=self.preview_amount(order,2500)['plan_token']
        result,_=self.race(self.action(order,2500,'mysql-partial-release',token),lambda:release_due_income(now=hold.release_at,limit=1))
        if result['code'] != 0:
            self.assertEqual(result['errorCode'],'REFUND_PLAN_CHANGED')
            self.ok(self.pay_refund(order,2500))
        self.provider_wallet.refresh_from_db();hold.refresh_from_db()
        self.assertEqual((self.provider_wallet.balance,self.provider_wallet.frozen_amount),(6000,0))
        self.assertIsNotNone(hold.released_at)
        self.ok(self.pay_refund(order))
        self.provider_wallet.refresh_from_db()
        self.assertEqual((self.provider_wallet.balance,self.provider_wallet.frozen_amount),(0,0))

    def test_partial_refund_racing_withdrawal_keeps_other_order_and_pending_freezes(self):
        foreign=self.settled(amount=5000,hold=72)
        order=self.settled(amount=20000)
        token=self.preview_amount(order,10000)['plan_token']
        provider=self.client_for(self.provider_account)
        def withdraw():
            return provider.post('/api/wallet/withdraw/',{'amount':10000,'payee_method':'WECHAT','payee_account':'LOCAL_TEST_ONLY',
                'payee_name':'隔离样例','client_request_id':'mysql-partial-withdraw'},format='json').data
        result,withdrawal=self.race(self.action(order,10000,'mysql-partial-withdraw-refund',token),withdraw)
        self.assertNotEqual(result['code']==0,withdrawal['code']==0,(result,withdrawal))
        if withdrawal['code']==0:
            self.assertEqual(result['errorCode'],'REFUND_RECOVERY_INSUFFICIENT')
            request=WithdrawRequest.objects.get(account=self.provider_account)
            self.ok(self.admin.post(f'/api/admin/withdrawals/{request.pk}/reject/',{'audit_remark':'并发用例退回'},format='json'))
            self.ok(self.pay_refund(order,10000))
        self.ok(self.pay_refund(order))
        self.provider_wallet.refresh_from_db()
        self.assertEqual((self.provider_wallet.balance,self.provider_wallet.frozen_amount),(0,4000))
        self.assertIsNone(IncomeHold.objects.get(income_transaction__order=foreign).cancelled_at)
