"""Partial refunds preserve cumulative money, source locks and net benefits."""
from datetime import timedelta
from unittest.mock import patch
from django.db.models import Sum
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from engagement.confirmation import process_order
from engagement.models import ConsumptionConfirmation,MemberProfile,PointLedger,RewardClaim,RewardItem,VipTier
from engagement.services import completed_consumption
from orders.models import Order,OrderRefund,ServiceCategory
from orders.refunds import proportional_targets,refund_summary
from orders.tests import test_settled_refunds as fixtures
from orders.tests.factories import make_user,make_service,make_boss_type
from wallet.income import release_due_income
from wallet.models import IncomeHold,Transaction,ProviderReport,WithdrawRequest


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',LEGACY_FORCE_AUTH_COMPAT=False,REQUIRE_ORDER_EVIDENCE_IMAGES=False)
class PartialRefundTest(APITestCase):
    setUp = fixtures.SettledRefundTest.setUp
    client_for = fixtures.SettledRefundTest.client_for
    settled = fixtures.SettledRefundTest.settled
    ok = fixtures.SettledRefundTest.ok
    snapshot = fixtures.SettledRefundTest.snapshot

    def preview_amount(self,order,amount=None):
        response = self.admin.get(f'/api/admin/orders/{order.pk}/refund-preview/',{} if amount is None else {'refund_amount':amount})
        return self.ok(response)

    def pay_refund(self,order,amount=None,*,key=None,token=None,reason='隔离部分退款',ticket=None):
        if token is None:
            token = self.preview_amount(order,amount)['plan_token']
        data = {'reason':reason,'resolution':reason,'plan_token':token,
                'client_request_id':key or f'partial-{order.pk}-{OrderRefund.objects.filter(order=order).count()}'}
        if amount is not None:
            data['refund_amount'] = amount
        path = f'/api/admin/aftersales/{ticket.pk}/refund/' if ticket else f'/api/admin/orders/{order.pk}/refund/'
        return self.admin.post(path,data,format='json')

    def test_multiple_partial_and_default_remainder_preserve_original_clocks(self):
        order = self.settled()
        clocks = order.completed_at,order.settled_at
        profile = self.provider.escort_profile
        profile.refresh_from_db()
        count = profile.completed_order_count
        for amount in (2500,2500):
            self.ok(self.pay_refund(order,amount))
            order.refresh_from_db();profile.refresh_from_db()
            self.assertEqual((order.status,order.payment_status),('COMPLETED','PARTIALLY_REFUNDED'))
            self.assertEqual((order.completed_at,order.settled_at),clocks)
            self.assertEqual(profile.completed_order_count,count)
        result = self.ok(self.pay_refund(order))
        order.refresh_from_db();profile.refresh_from_db();self.provider_wallet.refresh_from_db();self.platform.refresh_from_db()
        self.assertEqual((order.status,order.payment_status),('CANCELLED','REFUNDED'))
        self.assertEqual((self.provider_wallet.balance,self.platform.balance),(0,0))
        self.assertEqual(profile.completed_order_count,max(0,count-1))
        self.assertEqual((result['refund_summary']['amount'],result['refund_summary']['remaining_amount']), (10000,0))
        self.assertEqual([r['amount'] for r in result['refund_summary']['records']],[5000,2500,2500])

    def test_partial_hold_releases_only_remaining_at_original_time(self):
        order = self.settled(hold=72)
        hold = IncomeHold.objects.get(income_transaction__order=order)
        original = hold.release_at
        self.ok(self.pay_refund(order,2500))
        hold.refresh_from_db();self.provider_wallet.refresh_from_db()
        self.assertEqual((hold.amount,hold.recovered_amount,self.provider_wallet.frozen_amount),(8000,2000,6000))
        self.assertEqual(hold.release_at,original);self.assertIsNone(hold.cancelled_at)
        self.assertEqual(release_due_income(now=original-timedelta(microseconds=1),limit=1),0)
        self.assertEqual(release_due_income(now=original,limit=1),1)
        self.assertEqual(Transaction.objects.get(order=order,tx_type='EARNINGS_RELEASE').amount,6000)
        self.ok(self.pay_refund(order))
        self.provider_wallet.refresh_from_db()
        self.assertEqual((self.provider_wallet.balance,self.provider_wallet.frozen_amount),(0,0))
        self.assertEqual(release_due_income(now=original,limit=1),0)

    def test_many_one_raw_refunds_are_monotone_exact_and_finally_zero(self):
        order = self.settled(amount=17,hold=72)
        credits = list(order.transactions.filter(tx_type__in=['INCOME','SHOP_INCOME'],amount__gt=0))
        weights = {r.pk:r.amount for r in credits}
        for cumulative in range(1,18):
            preview = self.preview_amount(order,1)
            self.assertEqual(preview['recovery_total'],1)
            self.ok(self.pay_refund(order,1,token=preview['plan_token']))
            expected = proportional_targets(weights,cumulative)
            for credit in credits:
                actual = -(credit.reversals.aggregate(v=Sum('amount'))['v'] or 0)
                self.assertEqual(actual,expected[credit.pk])
        self.provider_wallet.refresh_from_db();hold=IncomeHold.objects.get(income_transaction__order=order)
        self.assertEqual((self.provider_wallet.balance,self.provider_wallet.frozen_amount),(0,0))
        self.assertEqual(hold.recovered_amount,hold.amount);self.assertIsNotNone(hold.cancelled_at)

    def test_multi_provider_inviter_shares_reconcile_after_multiple_refunds(self):
        order = self.settled(amount=30000,double=True,inviter=make_user())
        for amount in (1,999,7000,12000,10000):
            preview = self.preview_amount(order,amount)
            self.assertEqual(sum(r['income_amount'] for r in preview['recoveries']),amount)
            self.ok(self.pay_refund(order,amount,token=preview['plan_token']))
        self.assertEqual(order.transactions.filter(tx_type__in=['INCOME','SHOP_INCOME']).aggregate(v=Sum('amount'))['v'],0)

    def test_idempotent_key_survives_changed_remaining_and_conflicting_intent_is_409(self):
        order = self.settled()
        token = self.preview_amount(order,2500)['plan_token']
        self.ok(self.pay_refund(order,2500,key='repeat-partial',token=token))
        before = self.snapshot()
        self.ok(self.pay_refund(order,2500,key='repeat-partial',token=token))
        self.assertEqual(before,self.snapshot())
        response = self.pay_refund(order,2400,key='repeat-partial',token=token)
        self.assertEqual((response.data['code'],response.data['errorCode']),(409,'REFUND_REQUEST_CONFLICT'))
        self.assertEqual(before,self.snapshot())
        self.ok(self.pay_refund(order,key='remaining-full'))
        before = self.snapshot()
        self.ok(self.pay_refund(order,key='remaining-full',token='expired-is-irrelevant-on-replay'))
        self.assertEqual(before,self.snapshot())

    def test_invalid_amounts_and_unsettled_partial_are_zero_change(self):
        order = self.settled()
        before = self.snapshot()
        for amount in (True,0,-1,1.5,'1.5',10001):
            response = self.admin.post(f'/api/admin/orders/{order.pk}/refund/',
                {'reason':'错误金额','refund_amount':amount,'client_request_id':'invalid-refund'},format='json')
            self.assertEqual(response.data['code'],400,response.data)
            self.assertEqual(before,self.snapshot())
        pending = self.settled(perform_settlement=False)
        response = self.admin.get(f'/api/admin/orders/{pending.pk}/refund-preview/',{'refund_amount':1000})
        self.assertEqual(response.data['errorCode'],'REFUND_PARTIAL_UNSETTLED')

    def test_confirmed_consumption_points_allocations_and_vip_follow_net_amount(self):
        low = VipTier.objects.create(level=1,boss_type=make_boss_type(),recharge_threshold=3000)
        VipTier.objects.create(level=4,boss_type=make_boss_type(),recharge_threshold=6000)
        order = self.settled(amount=6000,confirmed=True)
        for amount,net in ((2000,4000),(1000,3000),(3000,0)):
            self.ok(self.pay_refund(order,amount))
            row = ConsumptionConfirmation.objects.get(order=order)
            profile = MemberProfile.objects.get(account=self.boss_account)
            self.boss_account.refresh_from_db()
            self.assertEqual((row.amount,row.refunded_amount,completed_consumption(self.boss_account)),(6000,6000-net,net))
            self.assertEqual(profile.points_balance,net)
            self.assertEqual(order.point_allocations.aggregate(v=Sum('refunded_amount'))['v'],6000-net)
            if net:
                self.assertIsNone(row.reversed_at)
                self.assertEqual(self.boss_account.boss_type_id,low.boss_type_id)
            else:
                self.assertIsNotNone(row.reversed_at)

    def test_later_confirmation_of_partial_order_uses_net_without_recredit(self):
        order = self.settled()
        self.ok(self.pay_refund(order,2500))
        ProviderReport.objects.filter(order=order).update(submitted_at=self.now-timedelta(hours=74))
        self.assertTrue(process_order(order.pk))
        row = ConsumptionConfirmation.objects.get(order=order)
        self.assertEqual((row.amount,row.refunded_amount,completed_consumption(self.boss_account)),(10000,2500,7500))
        self.assertEqual(order.transactions.filter(tx_type='INCOME',amount__gt=0).count(),1)

    def test_partial_aftersale_reopens_and_provider_summary_has_only_own_net(self):
        order = self.settled(double=True,hold=72)
        data = self.ok(self.customer.post(f'/api/orders/orders/{order.pk}/aftersale/',{'reason':'部分测试','client_request_id':'af-partial-one'},format='json'))
        from orders.models import AfterSale
        ticket=AfterSale.objects.get(pk=data['id'])
        self.ok(self.pay_refund(order,2500,ticket=ticket))
        ticket.refresh_from_db();order.refresh_from_db()
        self.assertEqual(ticket.status,'PARTIALLY_REFUNDED')
        self.ok(self.customer.post(f'/api/orders/orders/{order.pk}/aftersale/',{'reason':'再次申请余款','client_request_id':'af-partial-two'},format='json'))
        ticket.refresh_from_db();self.assertEqual(ticket.status,'OPEN')
        own = refund_summary(order,account=self.provider_account)
        self.assertEqual((own['recovered_amount'],own['net_income'],own['remaining_frozen_amount']),(1000,3000,3000))
        self.assertEqual(own['records'][0]['recovered_amount'],1000)
        boss = refund_summary(order,account=self.boss_account)
        self.assertNotIn('net_income',boss);self.assertNotIn('recovered_amount',boss)

    def test_partial_shortfall_preserves_foreign_hold_and_pending_withdrawal(self):
        foreign = self.settled(amount=5000,hold=72)
        order = self.settled(amount=20000)
        foreign_hold = IncomeHold.objects.get(income_transaction__order=foreign)
        provider = self.client_for(self.provider_account)
        self.ok(provider.post('/api/wallet/withdraw/',{'amount':10000,'payee_method':'WECHAT','payee_account':'LOCAL_TEST_ONLY',
            'payee_name':'隔离测试','client_request_id':'partial-insufficient-withdraw'},format='json'))
        token = self.preview_amount(order,10000)['plan_token']
        before = self.snapshot()
        response = self.pay_refund(order,10000,token=token)
        self.assertEqual(response.data['errorCode'],'REFUND_RECOVERY_INSUFFICIENT')
        self.assertEqual(before,self.snapshot())
        pending = WithdrawRequest.objects.get(account=self.provider_account)
        self.ok(self.admin.post(f'/api/admin/withdrawals/{pending.pk}/reject/',{'audit_remark':'隔离退回提现'},format='json'))
        response = self.pay_refund(order,10000,token=token)
        self.assertEqual(response.data['code'],409)
        self.ok(self.pay_refund(order,10000))
        self.ok(self.pay_refund(order))
        foreign_hold.refresh_from_db();self.provider_wallet.refresh_from_db()
        self.assertEqual((self.provider_wallet.balance,self.provider_wallet.frozen_amount),(0,4000))
        self.assertIsNone(foreign_hold.cancelled_at)

    def test_partial_gift_keeps_qualifying_right_and_prorates_naming_time(self):
        from engagement.models import GiftBenefitRule,GiftNamingGrant,ProviderNaming
        category = ServiceCategory.objects.create(name='部分赠礼',is_gift=True)
        gift = make_service(price=4000,commission_rate=20,service_category=category,gift_naming_days=2)
        reward = RewardItem.objects.create(code='partial-gift-right',name='礼物权益',kind='SERVICE',in_shop=False)
        GiftBenefitRule.objects.create(name='部分赠礼门槛',reward=reward,category=category,threshold=3000,enabled=True)
        result = self.ok(self.customer.post('/api/orders/gifts/send/',{'gift_service_id':gift.pk,'quantity':1,
            'provider_id':self.provider_account.pk,'naming_title':'部分冠名','expected_amount':4000,
            'client_request_id':'partial-gift-order'},format='json'))
        order = Order.objects.get(pk=result['id'])
        grant = GiftNamingGrant.objects.get(order=order)
        base = grant.expires_at-timedelta(days=2)
        self.ok(self.pay_refund(order,500))
        self.assertEqual(RewardClaim.objects.get(source_order=order).status,'PENDING')
        naming = ProviderNaming.objects.get(pk=grant.naming_id)
        self.assertAlmostEqual((naming.expires_at-base).total_seconds(),2*86400*3500//4000,delta=1)
        self.ok(self.pay_refund(order,501))
        self.assertEqual(RewardClaim.objects.get(source_order=order).status,'CANCELLED')

    def test_partial_earnings_deposit_and_remaining_hold_reconcile_to_zero(self):
        from wallet.models import SystemConfig
        SystemConfig.objects.update_or_create(key='club_income_rules_enabled',defaults={'value':'true'})
        profile = self.provider.escort_profile
        profile.deposit_mode='EARNINGS';profile.deposit_paid=0;profile.deposit_required=10000
        profile.save(update_fields=['deposit_mode','deposit_paid','deposit_required'])
        order = self.settled(hold=72)
        self.ok(self.pay_refund(order,2500))
        self.ok(self.pay_refund(order,2500))
        self.ok(self.pay_refund(order))
        profile.refresh_from_db();self.provider_wallet.refresh_from_db();self.platform.refresh_from_db()
        self.assertEqual((profile.deposit_paid,self.provider_wallet.balance,self.provider_wallet.frozen_amount,self.platform.balance),(0,0,0,0))

    def test_amount_bound_plan_and_late_failure_leave_zero_mutation(self):
        order=self.settled(hold=72)
        token=self.preview_amount(order,2500)['plan_token']
        before=self.snapshot()
        response=self.pay_refund(order,3000,token=token)
        self.assertEqual((response.data['code'],response.data['errorCode']),(409,'REFUND_PLAN_CHANGED'))
        self.assertEqual(before,self.snapshot())
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            with patch('engagement.refunds.reverse_refund_benefits',side_effect=RuntimeError('partial late rollback')):
                with self.assertRaises(RuntimeError):
                    self.pay_refund(order,2500,token=token)
        self.assertEqual(callbacks,[])
        self.assertEqual(before,self.snapshot())

    def test_partial_and_successful_replay_emit_one_committed_invalidation(self):
        order=self.settled(hold=72)
        token=self.preview_amount(order,2500)['plan_token']
        with patch('console.views.notify_order_update') as order_notify,patch('orders.services.notify_wallet_update') as wallet_notify:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                self.ok(self.pay_refund(order,2500,key='notify-partial',token=token))
                order_notify.assert_not_called();wallet_notify.assert_not_called()
            for callback in callbacks:
                callback()
            order_notify.assert_called_once();self.assertEqual(wallet_notify.call_count,3)
            with self.captureOnCommitCallbacks(execute=False) as repeated:
                self.ok(self.pay_refund(order,2500,key='notify-partial',token=token))
            self.assertEqual(repeated,[])
