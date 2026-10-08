from datetime import timedelta
from unittest.mock import patch
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import Order, OrderProvider
from orders.tests.factories import make_user, make_provider, make_service, make_wallet, make_superuser
from orders.companion_catalog import corrected_service_fields
from users.models import EscortProfile
from users.companion_shortcuts import recent_provider_ids
from console.serializers import AdminEscortSerializer

@override_settings(KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class CompanionPreferenceTest(APITestCase):
    def setUp(self):
        self.boss = make_user(); self.account = get_or_create_account_for_legacy_user(self.boss)
        self.wallet = make_wallet(self.boss, balance=100000)
        self.service = make_service(name='技术单陪', price=9000, service_type='COMPANION', catalog_family='常规陪玩', catalog_variant='技术 · 单陪1小时')
        self.male = make_provider(gender='MALE'); self.female = make_provider(gender='FEMALE')
        self.male_account = self.link(self.male); self.female_account = self.link(self.female)
        for name in ('_schedule_auto_cancel', 'enqueue_kook_dispatch', 'enqueue_wecom_dispatch', 'notify_order_update'):
            patcher=patch('orders.views.'+name);patcher.start();self.addCleanup(patcher.stop)
    def link(self, user):
        account=get_or_create_account_for_legacy_user(user)
        EscortProfile.objects.filter(user=user).update(account=account)
        user.escort_profile.service_items.add(self.service)
        return account
    def create(self, **extra):
        self.client.force_authenticate(self.boss)
        payload={'service_id':self.service.pk, 'dispatch_mode':'AUTO_GRAB', 'game_region':'微信','game_nickname':'老板','client_request_id':'gender-test'}
        payload.update(extra)
        return self.client.post('/api/orders/create/',payload,format='json')
    def pending(self, **extra):
        order=Order.objects.create(customer=self.boss,customer_account=self.account,service=self.service,amount=9000,**extra)
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now()-timedelta(hours=1))
        return order
    def test_gender_is_saved_and_legacy_default_is_any(self):
        response=self.create(provider_gender='FEMALE');self.assertEqual(response.data['code'],0,response.data)
        self.assertEqual(Order.objects.get(pk=response.data['data']['id']).provider_gender,'FEMALE')
        self.assertEqual(self.create(client_request_id='legacy').data['data']['provider_gender'],'ANY')
    def test_wrong_selected_gender_rejects_quote_and_payment_without_debit(self):
        self.client.force_authenticate(self.boss)
        payload={'service_id':self.service.pk,'provider_id':self.male.pk,'provider_gender':'FEMALE'}
        response=self.client.post('/api/orders/quote/',payload,format='json');self.assertEqual(response.data['code'],400)
        self.assertEqual(self.create(provider_id=self.male.pk,provider_gender='FEMALE').data['code'],400)
        self.wallet.refresh_from_db();self.assertEqual(self.wallet.balance,100000);self.assertFalse(Order.objects.exists())
    def test_matching_selected_gender_can_pay_and_assign(self):
        response=self.create(provider_id=self.female.pk,provider_gender='FEMALE')
        self.assertEqual(response.data['code'],0,response.data);self.assertEqual(response.data['data']['status'],'GRABBED')
    def test_unknown_gender_cannot_satisfy_female_choice(self):
        EscortProfile.objects.filter(user=self.female).update(gender='UNKNOWN')
        self.assertEqual(self.create(provider_id=self.female.pk,provider_gender='FEMALE').data['code'],400)
    def test_non_companion_and_invalid_gender_rejected(self):
        self.assertEqual(self.create(provider_gender='INVALID').data['code'],400)
        self.service.service_type='REGULAR';self.service.save()
        self.assertEqual(self.create(provider_gender='FEMALE').data['code'],400)
    def test_pool_filters_and_direct_grab_cannot_bypass_gender(self):
        order=self.pending(provider_gender='FEMALE')
        self.client.force_authenticate(self.male)
        self.assertFalse(self.client.get('/api/orders/orders/?status=pending').data['data'])
        self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/grab/').data['code'],400)
        self.client.force_authenticate(self.female)
        self.assertEqual(len(self.client.get('/api/orders/orders/?status=pending').data['data']),1)
        self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/grab/').data['code'],0)
    def test_boss_selection_applications_filter_gender(self):
        order=self.pending(provider_gender='FEMALE',requires_boss_selection=True)
        self.client.force_authenticate(self.male)
        self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/grab/').data['code'],400)
        self.client.force_authenticate(self.female)
        self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/grab/').data['code'],0)
    def test_staff_assignment_cannot_bypass_gender(self):
        order=self.pending(provider_gender='FEMALE');self.client.force_authenticate(make_superuser())
        response=self.client.post(f'/api/admin/orders/{order.pk}/assign/',{'provider_id':self.male_account.pk},format='json')
        self.assertEqual(response.data['code'],400,response.data);order.refresh_from_db();self.assertIsNone(order.provider_id)
    def test_staff_quick_dispatch_validates_gender_before_debit(self):
        self.client.force_authenticate(make_superuser())
        response=self.client.post('/api/admin/orders/dispatch/',{'customer_id':self.boss.pk,'service_id':self.service.pk,'provider_gender':'FEMALE','provider_id':self.male.pk},format='json')
        self.assertEqual(response.data['code'],400,response.data);self.wallet.refresh_from_db();self.assertEqual(self.wallet.balance,100000)
    def test_recent_is_account_scoped_distinct_bounded_and_includes_double(self):
        providers=[make_provider() for _ in range(5)];accounts=[self.link(p) for p in providers]
        for user,account in zip(providers,accounts): self.pending(provider=user,provider_account=account)
        duplicate=self.pending(provider=providers[2],provider_account=accounts[2])
        OrderProvider.objects.create(order=duplicate,provider=self.female,provider_account=self.female_account)
        self.pending(provider=self.male,provider_account=self.male_account,status='CANCELLED')
        old=self.pending(provider=self.male,provider_account=self.male_account)
        Order.objects.filter(pk=old.pk).update(business_cycle=2)
        other=make_user();Order.objects.create(customer=other,customer_account=get_or_create_account_for_legacy_user(other),provider=self.male,provider_account=self.male_account,service=self.service,amount=9000)
        with self.assertNumQueries(2): ids=recent_provider_ids(self.account)
        self.assertEqual(set(ids),{accounts[2].pk,self.female_account.pk,accounts[4].pk,accounts[3].pk})
        self.assertEqual(len(ids),4)
    def test_recommended_slots_order_conflict_and_authentication(self):
        EscortProfile.objects.filter(user=self.male).update(recommendation_slot=2)
        EscortProfile.objects.filter(user=self.female).update(recommendation_slot=1)
        self.client.force_authenticate(self.boss)
        response=self.client.get(f'/api/users/escorts/?shortcuts=1&service_item={self.service.pk}')
        self.assertEqual(response.data['code'],0,response.data)
        self.assertEqual([row['id'] for row in response.data['data']['recommended']],[self.female_account.pk,self.male_account.pk])
        third=make_provider();self.link(third)
        serializer=AdminEscortSerializer(third.escort_profile,data={'recommendation_slot':1},partial=True)
        self.assertFalse(serializer.is_valid())
        serializer=AdminEscortSerializer(third.escort_profile,data={'recommendation_slot':5},partial=True)
        self.assertFalse(serializer.is_valid())
        self.client.force_authenticate(None);self.assertIn(self.client.get('/api/users/escorts/?shortcuts=1').status_code,(401,403))
    def test_shortcuts_gender_and_unavailable_status(self):
        self.pending(provider=self.female,provider_account=self.female_account)
        EscortProfile.objects.filter(user=self.female).update(recommendation_slot=1,status='OFFLINE')
        EscortProfile.objects.filter(user=self.male).update(recommendation_slot=2)
        self.client.force_authenticate(self.boss)
        data=self.client.get(f'/api/users/escorts/?shortcuts=1&service_item={self.service.pk}&gender=FEMALE').data['data']
        self.assertEqual([p['id'] for p in data['recent']],[self.female_account.pk])
        self.assertFalse(data['recommended'][0]['can_order']);self.assertEqual(len(data['recommended']),1)
    def test_catalog_correction_changes_single_only_and_removes_female_restriction(self):
        self.service.price=10000;self.service.rules='100元／小时。娱乐女陪须安排女陪。娱乐类单陪+10r/h'
        fields=corrected_service_fields(self.service);self.assertEqual(fields['price'],9000)
        self.assertNotIn('须安排女陪',fields['rules']);self.assertIn('娱乐／技术类单陪+10r/h',fields['rules'])
        self.service.billing_provider_count=2;self.assertNotIn('price',corrected_service_fields(self.service))

    def test_catalog_command_is_idempotent_and_keeps_existing_order_price(self):
        from django.core.management import call_command
        from io import StringIO
        self.service.price=10000;self.service.save()
        order=self.pending();before=order.amount
        output=StringIO();call_command('correct_companion_catalog',apply=True,stdout=output)
        self.service.refresh_from_db();self.assertEqual(self.service.price,9000)
        order.refresh_from_db();self.assertEqual(order.amount,before)
        call_command('correct_companion_catalog',apply=True,stdout=output)
        self.assertIn('"services": []',output.getvalue())

    def test_legacy_writer_during_rollout_gets_database_default_gender(self):
        from django.db import connection
        order=self.pending()
        fields=[f for f in Order._meta.local_concrete_fields if f.name not in ('id','provider_gender')]
        quote=connection.ops.quote_name
        columns=','.join(quote(f.column) for f in fields)
        select=','.join('%s' if f.name=='order_no' else quote(f.column) for f in fields)
        with connection.cursor() as cursor:
            cursor.execute(f'INSERT INTO {quote(Order._meta.db_table)} ({columns}) SELECT {select} FROM {quote(Order._meta.db_table)} WHERE id=%s', ['LEGACY-COMPANION-TEST',order.pk])
        self.assertEqual(Order.objects.get(order_no='LEGACY-COMPANION-TEST').provider_gender,'ANY')
