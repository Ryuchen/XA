"""Whole-order fixed commissions: configuration, official purchase and snapshots."""
from datetime import timedelta

from django.db.models import Sum
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from console.serializers import AdminServiceItemSerializer
from orders.commission import FIXED_KEY, TYPE_KEY, fixed_specs, global_config, quote, resolve
from orders.models import GameCategory, Order
from orders.pricing import club_provider_specs
from orders.settlement import build_provider_shares
from orders.tests.factories import (make_user, make_superuser, make_provider, make_service,
    make_wallet, make_promotion, make_boss_type, make_escort_level)
from users.models import EscortProfile, ProviderQualification
from wallet.models import COMMISSION_RATE_KEY, SystemConfig, Transaction


class OrderCommissionModeTest(APITestCase):
    def setUp(self):
        self.admin = make_superuser()
        self.boss = make_user()
        self.providers = [make_provider() for _ in range(3)]
        for user in [self.admin,self.boss,*self.providers]:
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user,account)
        self.boss_account = get_or_create_account_for_legacy_user(self.boss)
        self.wallet = make_wallet(self.boss,1000000)
        self.game = GameCategory.objects.create(name='真实档位服务测试')
        self.service = make_service(price=5001,billing_provider_count=2,
            game_category=self.game,provider_kind='GUARD',provider_grade='MASTER',
            platform='PC',security_level='CONFIDENTIAL',commission_policy='REGULAR')
        for user in self.providers:
            profile = EscortProfile.objects.get(user=user)
            profile.account = get_or_create_account_for_legacy_user(user)
            profile.level = make_escort_level(commission_rate=15)
            profile.save(update_fields=['account','level'])
            profile.service_items.add(self.service)
            ProviderQualification.objects.create(
                profile=profile,game_category=self.game,provider_kind='GUARD',platform='PC',security_level='CONFIDENTIAL',grade='LEGEND')
        self.client.force_authenticate(self.admin)

    def configure(self,mode='FIXED',fixed=1001,rate=None,enabled=True):
        data = {'commission_override':enabled,'commission_type':mode,'commission_fixed':fixed}
        if rate is not None: data['commission_rate'] = rate
        serializer = AdminServiceItemSerializer(self.service,data=data,partial=True)
        self.assertTrue(serializer.is_valid(),serializer.errors)
        self.service = serializer.save()

    def dispatch(self,**extra):
        payload = {'customer_id':self.boss.pk,'service_id':self.service.pk,
            'escort_mode':'DOUBLE','providers':[{'provider_id':user.pk} for user in self.providers[:2]],
            'client_request_id':f'isolated-dispatch-{Order.objects.count()}'}
        payload.update(extra)
        response = self.client.post('/api/admin/orders/dispatch/',payload,format='json')
        self.assertEqual(response.data['code'],0,response.data)
        return Order.objects.get(pk=response.data['data']['id'])

    def test_scoped_fixed_once_odd_shares_quote_inviter_and_multiple_units(self):
        self.configure()
        inviter = make_user()
        self.boss_account.inviter = get_or_create_account_for_legacy_user(inviter)
        self.boss_account.inviter_commission_rate = 50
        self.boss_account.save(update_fields=['inviter','inviter_commission_rate'])
        data = self.client.get('/api/admin/orders/dispatch-quote/',{
            'customer_id':self.boss_account.pk,'service_id':self.service.pk,'game_rounds':3}).data['data']
        self.assertEqual((data['amount'],data['commission_type'],data['commission_fixed_total']),(30006,'FIXED',1001))
        self.assertEqual(data['commission_fixed_shares'],[501,500])
        self.assertEqual([row['provider_income'] for row in data['provider_shares']],[14502,14503])
        order = self.dispatch(game_rounds=3)
        self.assertEqual((order.commission_type,order.commission_fixed,order.provider_income,order.inviter_commission,order.shop_income),('FIXED',1001,29005,500,501))
        self.assertEqual(list(order.providers.order_by('pk').values_list('commission_fixed',flat=True)),[501,500])

    def test_fixed_zero_overrides_scope_and_percent_explicit_uses_custom_rate(self):
        self.configure(fixed=0)
        self.assertEqual(quote(self.service,10002)['commission_fixed_total'],0)
        self.assertEqual([s['commission_fixed'] for s in club_provider_specs(self.service,[None,None],10002)],[0,0])
        self.configure(mode='PERCENT',rate=17)
        order = self.dispatch()
        self.assertEqual(list(order.providers.values_list('commission_rate',flat=True)),[17,17])
        self.assertEqual(order.provider_income,8302)
        self.assertTrue(order.commission_override)

    def test_off_preserves_automatic_and_global_fixed_cannot_change_qualified_service(self):
        self.configure(fixed=1001)
        serializer = AdminServiceItemSerializer(self.service,data={'commission_override':False},partial=True)
        self.assertTrue(serializer.is_valid(),serializer.errors);self.service=serializer.save()
        SystemConfig.objects.create(key=TYPE_KEY,value='FIXED')
        SystemConfig.objects.create(key=FIXED_KEY,value='9')
        plan = quote(self.service,10002)
        self.assertEqual((plan['commission_type'],plan['commission_rate'],plan['commission_fixed_total']),('PERCENT',25,None))
        self.assertTrue(plan['commission_locked'])
        self.assertEqual(self.service.commission_fixed,1001)
        self.assertEqual(self.dispatch().provider_income,7502)

    def test_explicit_dispatch_fixed_and_percent_both_override_scope_only(self):
        order = self.dispatch(commission_type='FIXED',commission_fixed=7)
        self.assertEqual((order.commission_fixed,order.provider_income),(7,9995))
        # Independent next pricing scenario; the first fixture no longer
        # occupies dispatch capacity (no production state transition is tested).
        Order.objects.filter(pk=order.pk).update(status='COMPLETED')
        EscortProfile.objects.filter(user__in=self.providers).update(status='AVAILABLE')
        order = self.dispatch(commission_type='PERCENT',providers=[
            {'provider_id':self.providers[0].pk,'commission_rate':10},
            {'provider_id':self.providers[1].pk,'commission_rate':30}])
        self.assertEqual(list(order.providers.order_by('pk').values_list('commission_rate',flat=True)),[10,30])
        self.assertEqual(order.provider_income,8002)
        ProviderQualification.objects.filter(profile__user=self.providers[2]).delete()
        before = list(Transaction.objects.values_list('pk','amount'))
        response = self.client.post('/api/admin/orders/dispatch/',{
            'customer_id':self.boss.pk,'service_id':self.service.pk,'escort_mode':'DOUBLE',
            'commission_type':'FIXED','commission_fixed':0,
            'providers':[{'provider_id':self.providers[0].pk},{'provider_id':self.providers[2].pk}]},format='json')
        self.assertEqual(response.data['code'],400)
        self.assertEqual(list(Transaction.objects.values_list('pk','amount')),before)

    def test_global_fixed_ordinary_precedes_level_but_keeps_legacy_report_percent(self):
        plain = make_service(commission_policy='FUN')
        SystemConfig.objects.update_or_create(key=COMMISSION_RATE_KEY,defaults={'value':'31'})
        response = self.client.put('/api/admin/config/commission',{'commission_type':'FIXED','commission_fixed':37},format='json')
        self.assertEqual(response.data['code'],0,response.data)
        self.assertEqual(global_config(),{'commission_type':'FIXED','commission_fixed':37,'commission_rate':31})
        self.assertEqual(resolve(plain,10000,profile=self.providers[0].escort_profile)['commission_fixed'],37)
        from wallet.models import get_commission_rate
        self.assertEqual(get_commission_rate(),31)

    def test_promotion_fixed_overrides_service_and_old_percent_keeps_old_scope(self):
        self.configure(fixed=1001)
        promo = make_promotion(commission_type='FIXED',commission_fixed=13)
        self.assertEqual(resolve(self.service,10002,promotion=promo)['commission_fixed'],13)
        promo.commission_type='PERCENT';promo.commission_rate=3
        self.service.commission_override=False
        self.assertEqual(resolve(self.service,10002,promotion=promo)['commission_rate'],25)

    def test_discounted_paid_cap_and_single_raw_total_never_overdraws_a_slot(self):
        self.configure(fixed=999999)
        self.boss_account.boss_type=make_boss_type(discount_rate=50)
        self.boss_account.save(update_fields=['boss_type'])
        order = self.dispatch()
        self.assertEqual((order.amount,order.commission_fixed,order.provider_income),(5001,5001,0))
        self.assertEqual(list(order.providers.order_by('pk').values_list('commission_fixed',flat=True)),[2501,2500])
        for amount,total,expected in ((1,1,[1,0]),(3,1,[1,0]),(3,2,[1,1]),(0,0,[0,0])):
            shares=build_provider_shares(amount,fixed_specs(amount,2,total))
            self.assertEqual([s.commission_fixed for s in shares],expected)
            self.assertEqual(sum(s.provider_income+s.commission_fixed for s in shares),amount)

    def test_pending_fixed_and_custom_percent_survive_settings_change_and_assignment(self):
        self.service.billing_provider_count=1;self.service.save(update_fields=['billing_provider_count'])
        for mode,fixed,rate,expected in [('FIXED',101,None,4900),('PERCENT',0,13,4351)]:
            with self.subTest(mode=mode):
                self.configure(mode=mode,fixed=fixed,rate=rate)
                self.client.force_authenticate(self.boss)
                response=self.client.post('/api/orders/create/',{'service_id':self.service.pk,'game_rounds':1,'client_request_id':f'pending-{mode}'},format='json')
                self.assertEqual(response.data['code'],0,response.data)
                order=Order.objects.get(pk=response.data['data']['id'])
                self.service.commission_fixed=4000;self.service.commission_rate=99;self.service.save()
                SystemConfig.objects.update_or_create(key=TYPE_KEY,defaults={'value':'FIXED'})
                SystemConfig.objects.update_or_create(key=FIXED_KEY,defaults={'value':'4000'})
                self.client.force_authenticate(self.admin)
                result=self.client.post(f'/api/admin/orders/{order.pk}/assign/',{'provider_id':get_or_create_account_for_legacy_user(self.providers[0]).pk},format='json')
                self.assertEqual(result.data['code'],0,result.data)
                order.refresh_from_db();self.assertEqual(order.providers.get().provider_income,expected)
                Order.objects.filter(pk=order.pk).update(status='COMPLETED')
                EscortProfile.objects.filter(user=self.providers[0]).update(status='AVAILABLE')

    def test_old_order_never_adopts_new_global_fixed(self):
        plain=make_service(commission_policy='REGULAR')
        order=Order.objects.create(customer=self.boss,customer_account=self.boss_account,service=plain,amount=10000,commission_rate=20)
        SystemConfig.objects.create(key=TYPE_KEY,value='FIXED')
        SystemConfig.objects.create(key=FIXED_KEY,value='9999')
        profile=EscortProfile.objects.select_related('level').get(user=self.providers[0])
        self.assertEqual(club_provider_specs(order,[profile],10000),[{'commission_type':'PERCENT','commission_rate':15}])

    def test_experience_and_companion_auto_unchanged_until_explicit_service_opt_in(self):
        for fields,old_type,old_value in [({'commission_policy':'EXPERIENCE'},'FIXED',300),
                ({'commission_policy':'COMPANION'},'PERCENT',20),({'experience_kind':'NEW_VALUE'},'PERCENT',0)]:
            service=make_service(**fields)
            original=resolve(service,5000)
            self.assertEqual((original['commission_type'],original['commission_fixed'] if old_type=='FIXED' else original['commission_rate']),(old_type,old_value))
            service.commission_override=True;service.commission_type='FIXED';service.commission_fixed=51
            self.assertEqual(resolve(service,5000)['commission_fixed'],51)
            service.commission_type='PERCENT';service.commission_rate=7
            self.assertEqual(resolve(service,5000)['commission_rate'],7)

    def test_invalid_configuration_and_fixed_request_rejected_before_wallet_write(self):
        for data in ({'commission_override':True,'commission_type':'PERCENT','commission_rate':None},
                     {'commission_override':True,'commission_type':'FIXED','commission_fixed':-1}):
            serializer=AdminServiceItemSerializer(self.service,data=data,partial=True)
            self.assertFalse(serializer.is_valid())
        before=list(Transaction.objects.values_list('pk','amount'))
        for fixed in (-1,1.5,True,'2.5',2147483648):
            response=self.client.post('/api/admin/orders/dispatch/',{
                'customer_id':self.boss.pk,'service_id':self.service.pk,'escort_mode':'DOUBLE',
                'commission_type':'FIXED','commission_fixed':fixed,
                'providers':[{'provider_id':user.pk} for user in self.providers[:2]]},format='json')
            self.assertEqual(response.data['code'],400,response.data)
        self.assertEqual(list(Transaction.objects.values_list('pk','amount')),before)

    def test_legacy_double_slot_fixed_keeps_old_per_person_contract(self):
        plain=make_service(price=5000,billing_provider_count=2)
        self.service=plain
        order=self.dispatch(providers=[{'provider_id':self.providers[0].pk,'commission_type':'FIXED','commission_fixed':700},
            {'provider_id':self.providers[1].pk,'commission_type':'FIXED','commission_fixed':900}])
        self.assertEqual((order.commission_type,order.commission_fixed,order.commission_override),('PERCENT',0,False))
        self.assertEqual((order.provider_income,order.shop_income),(8400,1600))
        self.assertEqual(list(order.providers.order_by('pk').values_list('commission_fixed',flat=True)),[700,900])


from orders.tests import test_reservation_api as reservations


class CommissionReservationTest(reservations.ReservationApiTestBase):
    def test_official_reservation_conversion_keeps_fixed_split_and_snapshot(self):
        self.service.commission_override=True
        self.service.commission_type='FIXED'
        self.service.commission_fixed=321
        self.service.save()
        reservation=self.create_reservation_via_api()
        self.client.force_authenticate(self.provider)
        result=self.client.post(f'/api/orders/reservations/{reservation.pk}/confirm/',{})
        self.assertEqual(result.data['code'],0,result.data)
        self.client.force_authenticate(self.boss)
        result=self.client.post(f'/api/orders/reservations/{reservation.pk}/convert/',{'client_request_id':'commission-reservation'})
        self.assertEqual(result.data['code'],0,result.data)
        order=Order.objects.get(pk=result.data['data']['order']['id'])
        self.assertEqual((order.commission_type,order.commission_fixed,order.provider_income,order.shop_income),('FIXED',321,1679,321))
        self.assertEqual((order.providers.get().commission_fixed,order.providers.get().provider_income),(321,1679))


from orders.tests import test_transferred_team_refunds as transferred


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',LEGACY_FORCE_AUTH_COMPAT=False,
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True)
class CommissionSettlementTest(transferred.TransferredFixtures,APITestCase):
    def test_official_dispatch_transfer_approval_and_refund_never_charge_fixed_twice(self):
        service=make_service(price=5000,billing_provider_count=2,commission_override=True,
            commission_type='FIXED',commission_fixed=1001)
        data=self.ok(self.admin.post('/api/admin/orders/dispatch/',{
            'customer_id':self.boss_account.pk,'service_id':service.pk,'escort_mode':'DOUBLE',
            'providers':[{'provider_id':self.provider_account.pk},{'provider_id':self.other_account.pk}],
            'client_request_id':'whole-fixed-official-dispatch'},format='json'))
        order=Order.objects.get(pk=data['id'])
        self.assertEqual((order.commission_fixed,order.provider_income,order.shop_income),(1001,8999,1001))
        Order.objects.filter(pk=order.pk).update(income_hold_hours=72)
        self.ok(self.admin.post(f'/api/admin/orders/{order.pk}/start/',{},format='json'))
        self.ok(self.transfer(order))
        order.refresh_from_db()
        self.assertEqual(list(order.providers.order_by('pk').values_list('provider_income',flat=True)),[1799,4500,2700])
        self.assertEqual(list(order.providers.order_by('pk').values_list('commission_fixed',flat=True)),[0,500,501])
        self.finish_and_approve(order)
        self.assertEqual(list(self.credits(order).values_list('amount',flat=True)),[1799,4500,2700,1001])
        self.ok(self.pay_refund(order,2500))
        self.assertEqual(self.recovered(order),[450,1125,675,250])
        self.ok(self.pay_refund(order))
        self.assertEqual(self.recovered(order),[1799,4500,2700,1001])
        self.assertEqual(order.providers.aggregate(value=Sum('commission_fixed'))['value'],1001)
