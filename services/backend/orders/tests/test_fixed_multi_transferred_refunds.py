"""Whole-order fixed commission survives return transfers and source refunds."""
from fractions import Fraction
from unittest.mock import patch

from django.db.models import Sum
from django.test import override_settings
from rest_framework.test import APITestCase

from orders.models import GameCategory, Order, OrderRefund, OrderStatusLog
from orders.notifier import notify_order_update
from orders.services import required_report_providers
from orders.tests.factories import make_service
from orders.tests import test_multi_transferred_team_refunds as multi
from orders.tests.test_report_realtime import RecordingLayer
from users.models import EscortProfile, ProviderQualification
from wallet.models import IncomeHold, Transaction


INCOMES = (787,2700,1350,2362,1800,1001)
BASES = (787,2700,1350,2863,2300)
FOREIGN = (349,1200,600,1050,800,1001)


class FixedMultiFixtures(multi.MultiFixtures):
    def fixed_staged(self, *, amount=10000, hold=72):
        game, _ = GameCategory.objects.get_or_create(name='固定抽成多转真实档位')
        service = make_service(price=amount//2,billing_provider_count=2,
            commission_policy='REGULAR',commission_override=True,commission_type='FIXED',
            commission_fixed=1001,game_category=game,provider_kind='GUARD',provider_grade='MASTER',
            platform='PC',security_level='CONFIDENTIAL')
        for user,account,_wallet in self.actors:
            profile = EscortProfile.objects.get(account=account)
            profile.service_items.add(service)
            ProviderQualification.objects.get_or_create(profile=profile,game_category=game,
                provider_kind='GUARD',platform='PC',security_level='CONFIDENTIAL',grade='LEGEND')
        data = self.ok(self.admin.post('/api/admin/orders/dispatch/',{
            'customer_id':self.boss_account.pk,'service_id':service.pk,'escort_mode':'DOUBLE',
            'providers':[{'provider_id':self.provider_account.pk},{'provider_id':self.other_account.pk}],
            'client_request_id':f'fixed-multi-dispatch-{Order.objects.count()}'},format='json'))
        order = Order.objects.get(pk=data['id'])
        self.assertEqual((order.amount,order.commission_type,order.commission_fixed,order.provider_income,order.shop_income),
            (amount,'FIXED',1001,amount-1001,1001))
        self.assertEqual(list(order.providers.order_by('pk').values_list('commission_fixed',flat=True)),[501,500])
        original_a = order.providers.get(provider=self.provider).pk
        # Only this independent fixture's agreed duration is set. No global
        # rule or worker-wide release is changed by this regression.
        Order.objects.filter(pk=order.pk).update(income_hold_hours=hold)
        self.ok(self.admin.post(f'/api/admin/orders/{order.pk}/start/',{},format='json'))
        expected_stages = (
            (1799,4500,2700),(3149,4500,1350),
            (787,4500,1350,2362),INCOMES[:-1],
        ) if amount == 10000 else (
            (799,2000,1200),(1399,2000,600),(349,2000,600,1050),FOREIGN[:-1],
        )
        for index,(old,new,percent) in enumerate(((0,2,40),(2,0,50),(0,3,25),(1,4,60))):
            self.ok(self.move(order,old,new,percent))
            self.assertEqual(tuple(order.providers.order_by('pk').values_list('provider_income',flat=True)),expected_stages[index])
            self.assertEqual(order.providers.aggregate(value=Sum('commission_fixed'))['value'],1001)
            self.assertEqual(order.providers.aggregate(value=Sum('settlement_base'))['value'],amount)
            self.assertEqual(order.providers.filter(released_at=None).count(),2)
            self.assertFalse(order.transactions.filter(tx_type='INCOME').exists())
            if index == 1:
                self.assertEqual(order.providers.get(provider=self.provider).pk,original_a)
        self.assertEqual(OrderStatusLog.objects.filter(order=order,action='TRANSFER').count(),4)
        order.refresh_from_db()
        self.assertEqual((order.commission_type,order.commission_fixed,order.commission_source),('FIXED',1001,'service'))
        self.assertEqual(list(order.providers.order_by('pk').values_list('commission_fixed',flat=True)),[0,0,0,501,500])
        return order

    def fixed_multi(self, *, amount=10000, hold=72):
        order = self.fixed_staged(amount=amount,hold=hold)
        self.finish_and_approve(order)
        self.assertIsNotNone(order.settled_at)
        self.assertEqual(tuple(self.credits(order).values_list('amount',flat=True)),INCOMES if amount == 10000 else FOREIGN)
        return order

    def protected_fixed(self):
        order = self.fixed_multi(amount=5000)
        holds = list(IncomeHold.objects.filter(income_transaction__order=order).order_by('pk').values())
        self.assertEqual([row['amount'] for row in holds],list(FOREIGN[:-1]))
        return order,holds

    def independent_targets(self, order, cumulative):
        # A small independent exact-rational oracle, not the production
        # apportionment helper. Original transaction id breaks equal midpoints.
        credits = list(self.credits(order))
        atoms = sorted((Fraction(2*k+1,2*row.amount),row.pk,index)
            for index,row in enumerate(credits) for k in range(row.amount))
        expected = [0]*len(credits)
        for _,_pk,index in atoms[:cumulative]:
            expected[index] += 1
        return expected


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',LEGACY_FORCE_AUTH_COMPAT=False,
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True)
class FixedMultiTransferredRefundTest(FixedMultiFixtures,APITestCase):
    def test_official_four_transfers_wait_for_all_five_reports_and_keep_private_own_amounts(self):
        order = self.fixed_staged()
        self.assertEqual(tuple(order.providers.order_by('pk').values_list('settlement_base',flat=True)),BASES)
        self.assertEqual(required_report_providers(order),{actor[0].pk for actor in self.actors})
        self.finish_and_approve(order,approve_old=False)
        self.assertIsNone(order.settled_at)
        self.assertFalse(self.credits(order).exists())
        report = order.provider_reports.get(provider=self.provider)
        self.ok(self.admin.post(f'/api/admin/reports/{report.pk}/approve/',{},format='json'))
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        self.assertEqual(tuple(self.credits(order).values_list('amount',flat=True)),INCOMES)
        self.assertEqual(tuple(order.provider_reports.order_by('provider_id').values_list('payout_amount',flat=True)),INCOMES[:-1])
        self.assertEqual(IncomeHold.objects.filter(income_transaction__order=order).count(),5)
        self.ok(self.pay_refund(order,2500))
        expected = self.independent_targets(order,2500)
        self.assertEqual(expected,[197,675,338,590,450,250])
        self.assertEqual(self.recovered(order),expected)
        order.refresh_from_db()
        layer = RecordingLayer()
        with patch('orders.notifier.get_channel_layer',return_value=layer):
            notify_order_update(order)
        events = {group:event['data'] for group,event in layer.sent}
        for actor,income,recovered in zip(self.actors,INCOMES,expected):
            response = self.client_for(actor[1]).get(f'/api/orders/orders/{order.pk}/')
            self.ok(response)
            http = response.json()['data']
            ws = events[f'account_{actor[1].pk}']
            self.assertEqual(http['refund_summary'],ws['refund_summary'])
            self.assertEqual((ws['expected_income'],ws['refund_summary']['recovered_amount'],
                ws['refund_summary']['net_income'],ws['refund_summary']['remaining_frozen_amount']),
                (income,recovered,income-recovered,income-recovered))
            self.assertNotIn('recovery_lines',ws['refund_summary'])
            self.assertNotIn('recovery_lines',ws['refund_summary']['records'][0])
            self.assertIn('provider',ws)
            self.assertEqual(sum(group==f'account_{actor[1].pk}' for group,_ in layer.sent),1)
        self.assertNotIn('recovered_amount',events[f'account_{self.boss_account.pk}']['refund_summary'])

    def test_mixed_available_and_frozen_sources_refund_many_rounds_and_only_exact_remainder(self):
        foreign,foreign_holds = self.protected_fixed()
        order = self.fixed_multi()
        for index in (2,4):
            self.release_own(order,self.actors[index][2])
        clocks = order.completed_at,order.settled_at
        hold_clocks = list(IncomeHold.objects.filter(income_transaction__order=order).order_by('pk').values_list('pk','release_at'))
        cumulative = 0
        for amount in (2500,1500,1):
            cumulative += amount
            self.ok(self.pay_refund(order,amount))
            self.assertEqual(self.recovered(order),self.independent_targets(order,cumulative))
            order.refresh_from_db()
            self.assertEqual((order.completed_at,order.settled_at),clocks)
            self.assertEqual((order.status,order.payment_status),('COMPLETED','PARTIALLY_REFUNDED'))
            self.assertEqual(list(IncomeHold.objects.filter(income_transaction__order=order).order_by('pk').values_list('pk','release_at')),hold_clocks)
        before = self.full_snapshot()
        self.assertEqual(self.pay_refund(order,6000,token='above-remainder').data['errorCode'],'REFUND_INVALID_AMOUNT')
        self.assertEqual(self.full_snapshot(),before)
        self.ok(self.pay_refund(order,key='fixed-multi-final'))
        self.assertEqual(self.recovered(order),list(INCOMES))
        before = self.full_snapshot()
        self.ok(self.pay_refund(order,key='fixed-multi-final',token='replayed'))
        self.assertEqual(self.full_snapshot(),before)
        self.assertEqual(self.pay_refund(order,1,key='fixed-multi-final',token='conflicting').data['code'],409)
        self.assertEqual(self.full_snapshot(),before)
        self.assertEqual(OrderRefund.objects.filter(order=order).aggregate(value=Sum('amount'))['value'],10000)
        self.assertEqual(list(IncomeHold.objects.filter(income_transaction__order=foreign).order_by('pk').values()),foreign_holds)
        for actor,protected in zip(self.actors,FOREIGN):
            actor[2].refresh_from_db()
            self.assertEqual((actor[2].balance,actor[2].frozen_amount),(0,protected))
        self.assertEqual(order.providers.aggregate(value=Sum('commission_fixed'))['value'],1001)

    def test_each_of_five_available_recipients_short_by_100_blocks_every_business_funds_row(self):
        foreign,foreign_holds = self.protected_fixed()
        order = self.fixed_multi(hold=0)
        required = self.independent_targets(order,2500)
        self.assertEqual(IncomeHold.objects.filter(income_transaction__order=order).count(),0)
        for index,(actor,income,need) in enumerate(zip(self.actors,INCOMES,required)):
            with self.subTest(insufficient_recipient=index):
                penalty = income-need+100
                self.ok(self.dispose(actor,'PENALTY',penalty))
                plan = self.preview_amount(order,2500)
                self.assertEqual(plan['shortfalls'],[{'wallet_id':actor[2].pk,'account_id':actor[1].pk,
                    'required':need,'available':need-100,'shortfall':100}])
                before = self.full_snapshot()
                result = self.pay_refund(order,2500,key=f'fixed-multi-retry-{index}',token=plan['plan_token'])
                self.assertEqual((result.data['code'],result.data['errorCode']),(400,'REFUND_RECOVERY_INSUFFICIENT'))
                self.assertEqual(self.full_snapshot(),before)
                self.assertEqual(self.recovered(order),[0]*6)
                self.ok(self.dispose(actor,'REWARD',penalty))
                before = self.full_snapshot()
                self.assertEqual(self.pay_refund(order,2500,key=f'fixed-multi-retry-{index}',token=plan['plan_token']).data['code'],409)
                self.assertEqual(self.full_snapshot(),before)
        self.ok(self.pay_refund(order,2500,key='fixed-multi-retry-4'))
        self.assertEqual(self.recovered(order),required)
        self.ok(self.pay_refund(order))
        self.assertEqual(self.recovered(order),list(INCOMES))
        self.assertEqual(list(IncomeHold.objects.filter(income_transaction__order=foreign).order_by('pk').values()),foreign_holds)
        for actor,protected in zip(self.actors,FOREIGN):
            actor[2].refresh_from_db()
            self.assertEqual((actor[2].balance,actor[2].frozen_amount),(0,protected))
