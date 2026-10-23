"""Run against a disposable MySQL database: real refund arbitration and row locks."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest.mock import patch
from django.db import close_old_connections, connections, transaction
from django.test import skipUnlessDBFeature, override_settings
from rest_framework.test import APITransactionTestCase
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.confirmation import process_order
from orders.models import Order, OrderRefund
from orders.tests import test_settled_refunds as fixture_module
from orders.tests.factories import make_user
from wallet.income import release_due_income
from wallet.models import IncomeHold, Transaction, WithdrawRequest
from wallet.services import get_wallet


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',LEGACY_FORCE_AUTH_COMPAT=False,REQUIRE_ORDER_EVIDENCE_IMAGES=False)
@skipUnlessDBFeature('has_select_for_update')
class SettledRefundConcurrencyTest(APITransactionTestCase):
    setUp = fixture_module.SettledRefundTest.setUp
    client_for = fixture_module.SettledRefundTest.client_for
    settled = fixture_module.SettledRefundTest.settled
    preview = fixture_module.SettledRefundTest.preview
    ok = fixture_module.SettledRefundTest.ok

    def race(self,*actions):
        barrier = Barrier(len(actions))
        def run(action):
            close_old_connections()
            try:
                with connections['default'].cursor() as cursor:
                    cursor.execute('SET SESSION innodb_lock_wait_timeout=8')
                barrier.wait(timeout=8)
                return action()
            finally:
                connections['default'].close()
        with ThreadPoolExecutor(max_workers=len(actions)) as executor:
            futures = [executor.submit(run,action) for action in actions]
            return [future.result(timeout=20) for future in futures]

    def refund_action(self,order,token):
        client = self.client_for(self.admin_account)
        return lambda: client.post(f'/api/admin/orders/{order.pk}/refund/',{'reason':'MySQL隔离并发退款','plan_token':token},format='json').data

    def finish_refund(self,order):
        order.refresh_from_db()
        if order.payment_status != 'REFUNDED':
            result = self.refund_action(order,self.preview(order)['plan_token'])()
            self.assertEqual(result['code'],0,result)
        self.assertEqual(OrderRefund.objects.filter(order=order).count(),1)
        self.assertEqual(Transaction.objects.filter(order=order,tx_type='REFUND').count(),1)

    def test_simultaneous_same_order_refunds_reverse_each_credit_once(self):
        order = self.settled()
        token = self.preview(order)['plan_token']
        results = self.race(self.refund_action(order,token),self.refund_action(order,token))
        self.assertEqual(sum(row['code']==0 for row in results),1)
        self.finish_refund(order)
        self.assertEqual(Transaction.objects.filter(order=order,tx_type='INCOME',amount__lt=0).count(),1)

    def test_refund_racing_release_never_releases_cancelled_hold_or_double_credits(self):
        order = self.settled(hold=72)
        hold = IncomeHold.objects.get(income_transaction__order=order)
        token = self.preview(order)['plan_token']
        self.race(self.refund_action(order,token),lambda:release_due_income(now=hold.release_at,limit=1))
        self.finish_refund(order)
        self.provider_wallet.refresh_from_db()
        self.assertEqual((self.provider_wallet.balance,self.provider_wallet.frozen_amount),(0,0))
        self.assertEqual(release_due_income(now=hold.release_at,limit=1),0)

    def test_refund_racing_withdrawal_never_spends_pending_frozen_money(self):
        order = self.settled(amount=20000)
        token = self.preview(order)['plan_token']
        provider_client = self.client_for(self.provider_account)
        def withdraw():
            return provider_client.post('/api/wallet/withdraw/',{'amount':10000,'payee_method':'WECHAT',
                'payee_account':'LOCAL_TEST_ONLY','payee_name':'隔离样例','client_request_id':'mysql-refund-withdraw'},format='json').data
        refund_result,withdraw_result = self.race(self.refund_action(order,token),withdraw)
        self.assertNotEqual(refund_result['code']==0,withdraw_result['code']==0)
        if withdraw_result['code']==0:
            self.assertEqual(refund_result['errorCode'],'REFUND_RECOVERY_INSUFFICIENT')
            row = WithdrawRequest.objects.get(account=self.provider_account)
            self.ok(self.admin.post(f'/api/admin/withdrawals/{row.pk}/reject/',{'audit_remark':'隔离并发验证结束'},format='json'))
        self.finish_refund(order)
        self.provider_wallet.refresh_from_db()
        self.assertEqual((self.provider_wallet.balance,self.provider_wallet.frozen_amount),(0,0))

    def test_refund_racing_settlement_cannot_leave_a_refunded_order_credited(self):
        order = self.settled(perform_settlement=False)
        token = self.preview(order)['plan_token']
        self.race(self.refund_action(order,token),lambda:process_order(order.pk))
        self.finish_refund(order)
        self.provider_wallet.refresh_from_db()
        self.assertEqual(self.provider_wallet.balance,0)

    def test_cross_order_customer_inviter_cycle_with_same_provider_finishes_without_deadlock(self):
        second_boss = make_user()
        second_account = get_or_create_account_for_legacy_user(second_boss)
        second_wallet = get_wallet(user=second_boss,account=second_account)
        second_wallet.balance = 100000
        second_wallet.save(update_fields=['balance'])
        first = self.settled(inviter=second_boss,perform_settlement=False)
        original = self.boss,self.boss_account,self.boss_wallet
        self.boss,self.boss_account,self.boss_wallet = second_boss,second_account,second_wallet
        second = self.settled(inviter=original[0],perform_settlement=False)
        self.boss,self.boss_account,self.boss_wallet = original
        self.race(lambda:process_order(first.pk),lambda:process_order(second.pk))
        self.assertEqual(Order.objects.filter(pk__in=[first.pk,second.pk],settled_at__isnull=False).count(),2)
        self.assertEqual(Transaction.objects.filter(order_id__in=[first.pk,second.pk],tx_type='INCOME').count(),4)

    def test_zero_income_participant_refund_races_cash_deposit_return_without_lock_inversion(self):
        order = self.settled(perform_settlement=False)
        Order.objects.filter(pk=order.pk).update(provider_income=0,shop_income=order.amount,commission_rate=100)
        process_order(order.pk)
        profile = self.provider.escort_profile
        profile.deposit_paid = 1000
        profile.save(update_fields=['deposit_paid'])
        self.platform.refresh_from_db()
        self.platform.balance += 1000
        self.platform.save(update_fields=['balance'])
        Transaction.objects.create(wallet=self.provider_wallet,tx_type='DEPOSIT',amount=-1000)
        Transaction.objects.create(wallet=self.platform,tx_type='DEPOSIT_INCOME',amount=1000)
        token = self.preview(order)['plan_token']
        client = self.client_for(self.admin_account)
        def return_deposit():
            return client.post(f'/api/admin/escorts/{profile.pk}/deposit-refund/',
                {'amount':1000,'reason':'MySQL隔离现金押金退还','client_request_id':'mysql-zero-income-deposit'},format='json').data
        _,deposit = self.race(self.refund_action(order,token),return_deposit)
        self.assertEqual(deposit['code'],0,deposit)
        self.finish_refund(order)
        self.provider_wallet.refresh_from_db();profile.refresh_from_db()
        self.assertEqual((self.provider_wallet.balance,profile.deposit_paid,profile.completed_order_count),(1000,0,0))

    def test_source_order_refund_racing_gift_checkout_has_consistent_order_first_locks(self):
        from orders.models import ServiceCategory
        from orders.tests.factories import make_service
        order = self.settled()
        category = ServiceCategory.objects.create(name='并发赠礼退款',is_gift=True)
        gift = make_service(price=2100,service_category=category,commission_rate=20)
        token = self.preview(order)['plan_token']
        customer = self.client_for(self.boss_account)
        def send():
            return customer.post(f'/api/orders/orders/{order.pk}/tip/',{'gift_service_id':gift.pk,'quantity':1,
                'provider_id':self.provider_account.pk,'expected_amount':2100,'client_request_id':'mysql-gift-vs-refund'},format='json').data
        _,sent = self.race(self.refund_action(order,token),send)
        self.finish_refund(order)
        self.provider_wallet.refresh_from_db()
        self.assertEqual(self.provider_wallet.balance,1680 if sent['code']==0 else 0)

    def test_confirmed_refund_racing_vip_tag_delivery_locks_claim_before_account_update(self):
        from engagement.models import MemberTag, RewardClaim, RewardItem, VipTier
        from engagement.services import grant_entitlement, process_claim
        from engagement.refunds import _locked_refund_claims
        from orders.tests.factories import make_boss_type
        VipTier.objects.create(level=4,boss_type=make_boss_type(),recharge_threshold=10000)
        order = self.settled(confirmed=True)
        reward = RewardItem.objects.create(code='mysql-vip-refund-tag',name='隔离VIP标签',kind='TAG',in_shop=False)
        claim = grant_entitlement(self.boss_account,reward,
            f'vip-quota:{self.boss_account.pk}:1:TAG:1','VIP',{'tag':'并发交付标签'})
        claim.details = {'tag':'并发交付标签'}
        claim.save(update_fields=['details'])
        token = self.preview(order)['plan_token']
        held,attempt = Event(),Event()
        def delivery():
            with transaction.atomic():
                RewardClaim.objects.select_for_update().get(pk=claim.pk)
                held.set()
                if not attempt.wait(timeout=8):
                    raise AssertionError('refund did not reach claim lock')
                return process_claim(claim.pk,'COMPLETED','MySQL隔离交付',self.admin_account).status
        def observe(order,**kwargs):
            attempt.set()
            return _locked_refund_claims(order,**kwargs)
        def refund():
            if not held.wait(timeout=8):
                raise AssertionError('delivery did not lock claim')
            return self.refund_action(order,token)()
        with patch('engagement.refunds._locked_refund_claims',side_effect=observe):
            result,delivered = self.race(refund,delivery)
        self.assertEqual(result['code'],0,result)
        self.assertEqual(delivered,'COMPLETED')
        claim.refresh_from_db()
        self.assertEqual(claim.status,'COMPLETED')
        self.assertFalse(MemberTag.objects.get(event_key=f'claim:{claim.pk}:tag').is_active)
        self.assertIn(claim.pk,OrderRefund.objects.get(order=order).benefit_effects['delivered_claim_ids'])
