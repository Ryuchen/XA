from django.test import override_settings
from rest_framework.test import APITestCase, APIClient
from rest_framework.exceptions import ValidationError
from orders.models import Gameplay, Order
from orders.serializers import ServiceItemSerializer
from orders.gameplay import validate_service_gameplay
from orders.tests.factories import make_user, make_superuser, make_provider, make_service, make_wallet
from coupons.models import Coupon
from coupons.benefits import applies_to
from wallet.models import Wallet, Transaction

@override_settings(KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class DoubleHourPackageTest(APITestCase):
    def setUp(self):
        self.admin=make_superuser()
        self.boss=make_user()
        make_wallet(self.boss,balance=100000)
        self.providers=[make_provider(),make_provider()]
        self.gameplay=Gameplay.objects.create(name='双陪一小时测试',code='double-hour-test',theme='companion')
        self.service=make_service(name='娱乐双陪',price=6000,billing_provider_count=2,
            price_unit='小时',service_type='COMPANION',commission_policy='COMPANION',
            commission_rate=20,allows_discount_coupons=False,purchase_mode='CONSULT',
            gameplay=self.gameplay,platform='PC',security_level='CONFIDENTIAL')

    def dispatch(self,**overrides):
        self.client.force_authenticate(self.admin)
        payload={'customer_id':self.boss.pk,'service_id':self.service.pk,'escort_mode':'DOUBLE',
            'game_rounds':1,'providers':[{'provider_id':p.pk} for p in self.providers]}
        payload.update(overrides)
        return self.client.post('/api/admin/orders/dispatch/',payload,format='json')

    def test_public_price_is_full_package_and_retains_unit_price(self):
        self.client.force_authenticate(self.boss)
        row=self.client.get('/api/orders/services/').data['data'][0]
        self.assertEqual((row['price'],row['unit_price'],row['billing_provider_count']),(12000,6000,2))
        detail=self.client.get(f'/api/orders/services/{self.service.pk}/').data['data']
        self.assertEqual(detail['price'],12000)
        self.service.refresh_from_db();self.assertEqual(self.service.price,6000)

    def test_gameplay_starting_price_orders_by_full_package(self):
        single=make_service(name='另一套餐',price=10000,billing_provider_count=1,price_unit='份',
            gameplay=self.gameplay,platform='PC',security_level='CONFIDENTIAL')
        row=next(r for r in APIClient().get('/api/orders/gameplays/').data['data'] if r['id']==self.gameplay.pk)
        self.assertEqual((row['starting_price'],row['starting_price_unit']),(10000,'份'))
        single.is_active=False;single.save()
        row=next(r for r in APIClient().get('/api/orders/gameplays/').data['data'] if r['id']==self.gameplay.pk)
        self.assertEqual((row['starting_price'],row['starting_price_unit']),(12000,'小时'))

    def test_dispatch_charges_once_and_assigns_exactly_two_providers(self):
        response=self.dispatch()
        self.assertEqual(response.data['code'],0,response.data)
        order=Order.objects.get(pk=response.data['data']['id'])
        self.assertEqual((order.original_amount,order.amount,order.game_rounds,order.escort_mode),(12000,12000,1,'DOUBLE'))
        self.assertEqual(Wallet.objects.get(user=self.boss).balance,88000)
        shares=list(order.providers.order_by('pk'))
        self.assertEqual([s.settlement_base for s in shares],[6000,6000])
        self.assertEqual([s.provider_income for s in shares],[4800,4800])
        self.assertEqual(sum(s.provider_income for s in shares)+order.shop_income+order.inviter_commission,12000)
        self.assertEqual(Transaction.objects.get(order=order,tx_type='PAY').amount,-12000)

    def test_quantity_above_one_hour_cannot_charge_or_create_order(self):
        response=self.dispatch(game_rounds=2)
        self.assertEqual(response.data['code'],400,response.data)
        self.assertIn('每单1小时',str(response.data))
        self.assertFalse(Order.objects.exists())
        self.assertEqual(Wallet.objects.get(user=self.boss).balance,100000)

    def test_double_package_rejects_single_assignment_before_payment(self):
        response=self.dispatch(escort_mode='SINGLE',providers=[{'provider_id':self.providers[0].pk}])
        self.assertEqual(response.data['code'],400,response.data)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(Wallet.objects.get(user=self.boss).balance,100000)

    def test_double_package_cannot_redeem_single_hour_or_discount_coupons(self):
        free=Coupon.objects.create(name='VIP单陪',amount=1,discount_type='RATE',discount_rate=0,
            scope='COMPANION',service_ids=[self.service.pk],limit_units=1)
        monthly=Coupon.objects.create(name='月券',amount=1,discount_type='RATE',discount_rate=85,max_discount=10000)
        self.assertFalse(applies_to(free,self.service))
        self.assertFalse(applies_to(monthly,self.service))

    def test_single_prices_and_existing_quantity_rules_are_unchanged(self):
        single=make_service(name='单陪',price=7000,price_unit='小时',service_type='COMPANION',
            billing_provider_count=1,gameplay=self.gameplay,platform='PC',security_level='CONFIDENTIAL')
        self.assertEqual(ServiceItemSerializer(single).data['price'],7000)
        validate_service_gameplay(single,2)
