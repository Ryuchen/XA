"""Multi-hop/re-entry contracts use final official credits, never active seats."""
from fractions import Fraction
from datetime import timedelta
from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APITestCase, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from orders.models import Order, OrderProvider, OrderRefund, OrderStatusLog
from orders.notifier import notify_order_update
from orders.services import required_report_providers
from orders.tests.factories import make_provider
from orders.tests.test_transferred_team_refunds import TransferredFixtures
from orders.tests import test_settled_refund_concurrency as concurrency
from orders.tests.test_report_realtime import RecordingLayer
from wallet.income import release_due_income
from wallet.models import IncomeHold, Transaction, WithdrawRequest
from wallet.services import get_wallet


class MultiFixtures(TransferredFixtures):
    def setUp(self):
        super().setUp()
        self.extra = []
        for _ in range(2):
            user = make_provider(deposit_mode='CASH')
            account = get_or_create_account_for_legacy_user(user)
            self.accounts[user.pk] = account
            profile = user.escort_profile
            profile.account = account
            profile.save(update_fields=['account'])
            self.extra.append((user,account,get_wallet(user=user,account=account)))
        self.actors = [(self.provider,self.provider_account,self.provider_wallet),
                       (self.other,self.other_account,self.other_wallet),
                       (self.new,self.new_account,self.new_wallet),*self.extra]
        role = self.admin_user.admin_membership.roles.first()
        role.permissions = list(set(role.permissions)|{'escort:dispose'})
        role.save(update_fields=['permissions'])

    def move(self,order,old,new,percent,*,client=None):
        return self.transfer(order,client=client,commands=[{
            'old_provider_id':self.actors[old][1].pk,'new_provider_id':self.actors[new][1].pk,
            'split_type':'PERCENT','split_value':percent,'penalty_amount':0,
        }])

    def staged(self,*,amount=10000,hold=72,steps=4):
        order = self.in_service(amount=amount,hold=hold)
        for old,new,percent in [(0,2,40),(2,0,50),(0,3,25),(1,4,60)][:steps]:
            self.ok(self.move(order,old,new,percent))
            self.assertEqual(order.providers.filter(released_at=None).count(),2)
            self.assertFalse(order.transactions.filter(tx_type='INCOME').exists())
            self.assertEqual(sum(order.providers.values_list('settlement_base',flat=True)),amount)
        return order

    def multi(self,*,amount=10000,hold=72):
        order = self.staged(amount=amount,hold=hold)
        self.finish_and_approve(order)
        self.assertIsNotNone(order.settled_at)
        return order

    def foreign_multi(self):
        order = self.multi(amount=5000)
        holds = list(IncomeHold.objects.filter(income_transaction__order=order).order_by('pk').values())
        return order,holds

    def dispose(self,actor,kind,amount):
        return self.admin.post(f'/api/admin/escorts/{actor[0].escort_profile.pk}/dispose/',
            {'dispose_type':kind,'amount':amount,'reason':'隔离多次转单余额边界'},format='json')

    def assert_five_credits(self,order,values=(700,2100,1200,2100,1400,2500)):
        self.assertEqual(list(self.credits(order).values_list('amount',flat=True)),list(values))
        self.assertEqual(order.providers.count(),5)
        for row in order.providers.all():
            self.assertTrue(row.settled_at)
            self.assertEqual(order.transactions.filter(wallet__account_id=row.provider_account_id,
                tx_type='INCOME',status='SUCCESS',reversal_of=None,amount__gt=0).count(),1)


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',LEGACY_FORCE_AUTH_COMPAT=False,
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True)
class MultiTransferredRefundTest(MultiFixtures,APITestCase):
    def test_official_return_reuses_one_row_and_retransfers_its_whole_unposted_pool(self):
        order = self.in_service()
        original_ids = dict(order.providers.values_list('provider_account_id','pk'))
        self.ok(self.move(order,0,2,40))
        self.assertEqual(list(order.providers.order_by('pk').values_list('provider_income',flat=True)),[1600,3500,2400])
        self.ok(self.move(order,2,0,50))
        returned = order.providers.get(provider=self.provider)
        self.assertEqual((returned.pk,returned.provider_income,returned.settlement_base,returned.released_at),
                         (original_ids[self.provider_account.pk],2800,3800,None))
        self.ok(self.move(order,0,3,25))
        # Existing released/re-entry contract: none of these snapshots has
        # been approved or credited yet. The merged row is split as one pool.
        self.assertEqual(list(order.providers.order_by('pk').values_list('provider_income',flat=True)),[700,3500,1200,2100])
        self.ok(self.move(order,1,4,60))
        self.assertEqual(list(order.providers.order_by('pk').values_list('settlement_base',flat=True)),[700,2100,1200,3100,2900])
        self.assertEqual(set(order.providers.filter(released_at=None).values_list('provider_account_id',flat=True)),
                         {self.actors[3][1].pk,self.actors[4][1].pk})
        self.assertEqual(OrderStatusLog.objects.filter(order=order,action='TRANSFER').count(),4)
        self.finish_and_approve(order,approve_old=False)
        self.assertIsNone(order.settled_at)
        self.assertEqual(required_report_providers(order),{actor[0].pk for actor in self.actors})
        self.assertFalse(order.transactions.filter(tx_type='INCOME').exists())
        report = order.provider_reports.get(provider=self.provider)
        self.ok(self.admin.post(f'/api/admin/reports/{report.pk}/approve/',{},format='json'))
        order.refresh_from_db()
        self.assert_five_credits(order)
        self.assertEqual(IncomeHold.objects.filter(income_transaction__order=order).count(),5)

    def test_mixed_five_sources_multi_round_full_remainder_and_final_replay(self):
        foreign,foreign_holds = self.foreign_multi()
        order = self.multi()
        self.release_own(order,self.actors[2][2])
        self.release_own(order,self.actors[4][2])
        clocks = order.completed_at,order.settled_at
        for amount,target in [(2500,[175,525,300,525,350,625]),(1500,[280,840,480,840,560,1000])]:
            self.ok(self.pay_refund(order,amount))
            self.assertEqual(self.recovered(order),target)
            order.refresh_from_db()
            self.assertEqual((order.completed_at,order.settled_at),clocks)
            self.assertEqual((order.status,order.payment_status),('COMPLETED','PARTIALLY_REFUNDED'))
        self.ok(self.pay_refund(order,key='multi-final'))
        self.assertEqual(self.recovered(order),[700,2100,1200,2100,1400,2500])
        before = self.full_snapshot()
        self.ok(self.pay_refund(order,key='multi-final',token='replayed-success'))
        self.assertEqual(before,self.full_snapshot())
        self.assertEqual(self.pay_refund(order,1,key='multi-final',token='intent-mismatch').data['code'],409)
        self.assertEqual(before,self.full_snapshot())
        self.assertEqual(list(IncomeHold.objects.filter(income_transaction__order=foreign).order_by('pk').values()),foreign_holds)
        for actor,frozen in zip(self.actors,(350,1050,600,1050,700)):
            actor[2].refresh_from_db()
            self.assertEqual((actor[2].balance,actor[2].frozen_amount),(0,frozen))

    def test_each_of_five_actual_recipients_can_block_the_entire_refund_without_touching_others(self):
        foreign,foreign_holds = self.foreign_multi()
        order = self.multi(amount=100000,hold=0)
        self.assert_five_credits(order,(7000,21000,12000,21000,14000,25000))
        for index,(actor,income,required) in enumerate(zip(self.actors,(7000,21000,12000,21000,14000),(1750,5250,3000,5250,3500))):
            with self.subTest(recipient=index):
                penalty = income-required+100
                self.ok(self.dispose(actor,'PENALTY',penalty))
                preview = self.preview_amount(order,25000)
                self.assertEqual(preview['shortfalls'],[{'wallet_id':actor[2].pk,'account_id':actor[1].pk,
                    'required':required,'available':required-100,'shortfall':100}])
                before = self.full_snapshot()
                rejected = self.pay_refund(order,25000,key=f'multi-retry-{index}',token=preview['plan_token'])
                self.assertEqual((rejected.data['code'],rejected.data['errorCode']),(400,'REFUND_RECOVERY_INSUFFICIENT'))
                self.assertEqual(before,self.full_snapshot())
                self.ok(self.dispose(actor,'REWARD',penalty))
                before = self.full_snapshot()
                self.assertEqual(self.pay_refund(order,25000,key=f'multi-retry-{index}',token=preview['plan_token']).data['code'],409)
                self.assertEqual(before,self.full_snapshot())
        self.ok(self.pay_refund(order,25000,key='multi-retry-4'))
        self.assertEqual(self.recovered(order),[1750,5250,3000,5250,3500,6250])
        self.ok(self.pay_refund(order))
        self.assertEqual(self.recovered(order),[7000,21000,12000,21000,14000,25000])
        self.assertEqual(list(IncomeHold.objects.filter(income_transaction__order=foreign).order_by('pk').values()),foreign_holds)

    def test_one_raw_midpoint_oracle_monotonicity_and_final_cap_over_six_credits(self):
        order = self.multi(amount=43,hold=0)
        self.assert_five_credits(order,(3,9,5,10,6,10))
        credits = list(self.credits(order))
        atoms = sorted((Fraction(2*k+1,2*row.amount),row.pk,i) for i,row in enumerate(credits) for k in range(row.amount))
        expected = [0]*6
        for refunded in range(1,14):
            expected[atoms[refunded-1][2]] += 1
            self.ok(self.pay_refund(order,1,key=f'multi-raw-{refunded}'))
            self.assertEqual(self.recovered(order),expected)
        before = self.full_snapshot()
        self.assertEqual(self.pay_refund(order,31,token='above-remaining').data['errorCode'],'REFUND_INVALID_AMOUNT')
        self.assertEqual(before,self.full_snapshot())
        self.ok(self.pay_refund(order))
        self.assertEqual(self.recovered(order),[3,9,5,10,6,10])

    def test_all_five_own_http_and_private_ws_summaries_do_not_leak_other_recipient_money(self):
        order = self.multi()
        self.ok(self.pay_refund(order,2500))
        order.refresh_from_db()
        layer = RecordingLayer()
        with patch('orders.notifier.get_channel_layer',return_value=layer):
            notify_order_update(order)
        events = {group:event['data'] for group,event in layer.sent}
        for actor,income,recovered in zip(self.actors,(700,2100,1200,2100,1400),(175,525,300,525,350)):
            client = self.client_for(actor[1])
            response = client.get(f'/api/orders/orders/{order.pk}/')
            self.ok(response)
            http = response.json()['data']
            rows = self.ok(client.get('/api/orders/orders/',{'status':'all'}))
            self.assertEqual(next(row for row in rows if row['id']==order.pk)['expected_income'],income)
            ws = events[f'account_{actor[1].pk}']
            self.assertEqual(http['expected_income'],income)
            self.assertEqual(http['refund_summary'],ws['refund_summary'])
            self.assertEqual((ws['expected_income'],ws['refund_summary']['recovered_amount'],ws['refund_summary']['net_income'],ws['refund_summary']['remaining_frozen_amount']),
                (income,recovered,income-recovered,income-recovered))
            self.assertNotIn('recovery_lines',ws['refund_summary'])
            self.assertNotIn('recovery_lines',ws['refund_summary']['records'][0])
            self.assertEqual(sum(group==f'account_{actor[1].pk}' for group,_ in layer.sent),1)

    def test_settled_or_approved_order_cannot_rewrite_merged_shares(self):
        order = self.multi()
        before = self.full_snapshot()
        self.assertEqual(self.move(order,3,0,50).data['code'],400)
        self.assertEqual(before,self.full_snapshot())
        for report in order.provider_reports.all():
            self.assertEqual(self.admin.post(f'/api/admin/reports/{report.pk}/approve/',{},format='json').data['code'],400)
        self.assertEqual(before,self.full_snapshot())


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',LEGACY_FORCE_AUTH_COMPAT=False,
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True)
@skipUnlessDBFeature('has_select_for_update')
class MultiTransferredConcurrencyTest(MultiFixtures,APITransactionTestCase):
    race = concurrency.SettledRefundConcurrencyTest.race

    def refund_action(self,order,amount,key,token):
        client = self.client_for(self.admin_account)
        return lambda:client.post(f'/api/admin/orders/{order.pk}/refund/',{'refund_amount':amount,
            'client_request_id':key,'reason':'多次转单MySQL退款','plan_token':token},format='json').data

    def test_concurrent_different_seats_can_transfer_without_losing_a_historical_row(self):
        order = self.staged(steps=3)
        a,b = self.race(lambda:self.move(order,1,4,60,client=self.client_for(self.admin_account)).data,
                        lambda:self.move(order,3,2,50,client=self.client_for(self.admin_account)).data)
        self.assertEqual((a['code'],b['code']),(0,0),(a,b))
        self.assertEqual(order.providers.filter(released_at=None).count(),2)
        self.finish_and_approve(order)
        self.assert_five_credits(order,(700,2100,2250,1050,1400,2500))
        self.ok(self.pay_refund(order,2000))
        self.assertEqual(self.recovered(order),[140,420,450,210,280,500])

    def test_duplicate_outgoing_after_reentry_commits_only_one_replacement(self):
        order = self.staged(steps=2)
        a,b = self.race(lambda:self.move(order,0,3,25,client=self.client_for(self.admin_account)).data,
                        lambda:self.move(order,0,4,25,client=self.client_for(self.admin_account)).data)
        self.assertEqual(sorted([a['code'],b['code']]),[0,400],(a,b))
        self.assertEqual(order.providers.count(),4)
        self.assertEqual(order.providers.filter(released_at=None).count(),2)
        self.finish_and_approve(order)
        self.assertEqual(list(self.credits(order).values_list('amount',flat=True)),[700,3500,1200,2100,2500])
        self.ok(self.pay_refund(order))
        self.assertEqual(self.recovered(order),[700,3500,1200,2100,2500])

    def test_same_key_partial_refund_commits_once_for_all_five_recipients(self):
        order = self.multi()
        token = self.preview_amount(order,2500)['plan_token']
        a,b = self.race(self.refund_action(order,2500,'multi-same-key',token),self.refund_action(order,2500,'multi-same-key',token))
        self.assertEqual((a['code'],b['code']),(0,0),(a,b))
        self.assertEqual(OrderRefund.objects.filter(order=order).count(),1)
        self.assertEqual(self.recovered(order),[175,525,300,525,350,625])

    def test_different_keys_cannot_reuse_the_same_six_source_refund_plan(self):
        order = self.multi()
        token = self.preview_amount(order,4000)['plan_token']
        a,b = self.race(self.refund_action(order,4000,'multi-key-a',token),self.refund_action(order,4000,'multi-key-b',token))
        self.assertEqual(sorted([a['code'],b['code']]),[0,409],(a,b))
        self.assertEqual(self.recovered(order),[280,840,480,840,560,1000])
        self.ok(self.pay_refund(order))
        self.assertEqual(self.recovered(order),[700,2100,1200,2100,1400,2500])

    def test_released_historical_middle_provider_release_racing_partial_refund(self):
        foreign,foreign_holds = self.foreign_multi()
        order = self.multi()
        hold = IncomeHold.objects.get(income_transaction__order=order,wallet=self.new_wallet)
        now = timezone.now()
        IncomeHold.objects.filter(pk=hold.pk).update(release_at=now-timedelta(seconds=1))
        token = self.preview_amount(order,2500)['plan_token']
        result,released = self.race(self.refund_action(order,2500,'multi-middle-release',token),lambda:release_due_income(now=now,limit=1))
        self.assertEqual(released,1)
        if result['code']:
            self.assertEqual(result['errorCode'],'REFUND_PLAN_CHANGED')
            self.ok(self.pay_refund(order,2500))
        self.new_wallet.refresh_from_db()
        self.assertEqual((self.new_wallet.balance,self.new_wallet.frozen_amount),(900,600))
        self.ok(self.pay_refund(order))
        self.assertEqual(self.recovered(order),[700,2100,1200,2100,1400,2500])
        self.assertEqual(list(IncomeHold.objects.filter(income_transaction__order=foreign).order_by('pk').values()),foreign_holds)

    def test_multi_recipient_refund_and_withdrawal_cannot_spend_protected_funds(self):
        foreign,foreign_holds = self.foreign_multi()
        order = self.multi(amount=100000,hold=0)
        token = self.preview_amount(order,50000)['plan_token']
        result,withdrawal = self.race(self.refund_action(order,50000,'multi-withdraw-refund',token),
            lambda:self.withdraw(self.other_account,11000,'multi-pending-withdraw').data)
        self.assertNotEqual(result['code']==0,withdrawal['code']==0,(result,withdrawal))
        if withdrawal['code']==0:
            self.assertEqual(result['errorCode'],'REFUND_RECOVERY_INSUFFICIENT')
            self.assertEqual(self.recovered(order),[0]*6)
            self.reject_withdraw(self.other_account)
            self.ok(self.pay_refund(order,50000))
        self.ok(self.pay_refund(order))
        self.assertEqual(self.recovered(order),[7000,21000,12000,21000,14000,25000])
        self.assertEqual(list(IncomeHold.objects.filter(income_transaction__order=foreign).order_by('pk').values()),foreign_holds)


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',LEGACY_FORCE_AUTH_COMPAT=False,
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True,LOGIN_OTP_REQUIRED=False,
                   CHANNEL_LAYERS={'default':{'BACKEND':'channels.layers.InMemoryChannelLayer'}})
class MultiTransferredSocketTest(MultiFixtures,APITransactionTestCase):
    def test_all_five_full_private_events_and_boss_remain_scoped_after_four_transfers(self):
        order = self.multi()
        self.ok(self.pay_refund(order,2500))
        order.refresh_from_db()
        unrelated = get_or_create_account_for_legacy_user(make_provider())
        accounts = [actor[1] for actor in self.actors]+[self.boss_account,unrelated]
        tokens = [issue_account_tokens(account)[0] for account in accounts]
        async def verify():
            sockets = [WebsocketCommunicator(application,f'/ws/orders/?token={token}') for token in tokens]
            try:
                for socket in sockets:
                    self.assertTrue((await socket.connect())[0])
                await database_sync_to_async(notify_order_update)(order)
                for socket,income,recovered in zip(sockets[:5],(700,2100,1200,2100,1400),(175,525,300,525,350)):
                    payload = (await socket.receive_json_from())['data']
                    self.assertEqual(payload['expected_income'],income)
                    self.assertEqual(payload['refund_summary']['recovered_amount'],recovered)
                    self.assertEqual(payload['refund_summary']['net_income'],income-recovered)
                    self.assertNotIn('recovery_lines',payload['refund_summary'])
                    self.assertIn('provider',payload)
                    self.assertIsInstance(payload['settled_at'],str)
                    self.assertTrue(await socket.receive_nothing(timeout=0.1))
                self.assertNotIn('recovered_amount',(await sockets[5].receive_json_from())['data']['refund_summary'])
                self.assertTrue(await sockets[6].receive_nothing(timeout=0.1))
            finally:
                for socket in sockets:
                    await socket.disconnect()
        async_to_sync(verify)()
