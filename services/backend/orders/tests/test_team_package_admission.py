from unittest.mock import patch

from django.test import override_settings
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderApplication
from orders.serializers import OrderSerializer
from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user, make_wallet
from users.models import EscortProfile
from wallet.models import Transaction, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class TeamPackageAdmissionTest(APITestCase):
    def setUp(self):
        self.boss = make_user()
        self.boss_account = get_or_create_account_for_legacy_user(self.boss)
        self.providers = [make_provider() for _ in range(2)]
        self.accounts = [get_or_create_account_for_legacy_user(user) for user in self.providers]
        for user, account in zip(self.providers, self.accounts):
            EscortProfile.objects.filter(user=user).update(account=account)
        self.admin = make_console_user(['order:dispatch', 'order:view'])
        self.admin_account = get_or_create_account_for_legacy_user(self.admin)
        membership = self.admin.admin_membership
        membership.account = self.admin_account
        membership.save(update_fields=['account'])
        self.single = make_service(price=5000)
        self.double = make_service(price=5000, billing_provider_count=2)
        make_wallet(self.boss, balance=100000)
        patcher = patch('orders.applications.notify_order_update')
        patcher.start()
        self.addCleanup(patcher.stop)

    def auth(self, account):
        token, _ = issue_account_tokens(account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer '+token)

    def order(self, service=None, **kwargs):
        return make_order(self.boss, service=service or self.single, customer_account=self.boss_account,
                          requires_boss_selection=True, **kwargs)

    def apply(self, order, size, partner=None):
        self.auth(self.accounts[0])
        payload = {'team_size':size}
        if partner is not None:
            payload['partner_id'] = partner.pk
        return self.client.post(f'/api/orders/orders/{order.pk}/grab/', payload, format='json')

    def test_single_rejects_two_person_application_and_teammate_listing_without_writes(self):
        order = self.order()
        balances = list(Wallet.objects.values_list('pk','balance','frozen_amount'))
        result = self.apply(order, 2, self.accounts[1])
        self.assertEqual(result.data['code'], 400, result.data)
        self.assertIn('单陪', result.data['msg'])
        self.assertFalse(OrderApplication.objects.filter(order=order).exists())
        self.assertFalse(order.providers.exists())
        self.assertFalse(Transaction.objects.exists())
        self.assertEqual(list(Wallet.objects.values_list('pk','balance','frozen_amount')), balances)
        self.assertEqual(self.client.get(f'/api/orders/orders/{order.pk}/teammates/').data['code'],404)

    def test_stale_single_team_cannot_confirm_or_be_selected_but_can_withdraw(self):
        order = self.order()
        row = OrderApplication.objects.create(order=order, applicant=self.accounts[0], partner=self.accounts[1], partner_confirmed=True)
        self.auth(self.accounts[1])
        response = self.client.post(f'/api/orders/orders/{order.pk}/applications/{row.pk}/action/', {'action':'CONFIRM_TEAM'})
        self.assertEqual(response.data['code'],400)
        self.auth(self.boss_account)
        response = self.client.post(f'/api/orders/orders/{order.pk}/applications/{row.pk}/select/')
        self.assertEqual(response.data['code'],400)
        self.assertFalse(order.providers.exists())
        self.auth(self.accounts[1])
        self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/applications/{row.pk}/action/', {'action':'WITHDRAW'}).data['code'],0)

    def test_double_rejects_single_application_and_direct_single_grab(self):
        order = self.order(self.double)
        self.assertEqual(self.apply(order,1).data['code'],400)
        Order.objects.filter(pk=order.pk).update(requires_boss_selection=False)
        self.assertEqual(self.apply(order,1).data['code'],400)
        self.assertFalse(order.providers.exists())

    def test_original_single_snapshot_does_not_upgrade_when_catalog_changes(self):
        order = self.order()
        self.single.billing_provider_count = 2
        self.single.save(update_fields=['billing_provider_count'])
        self.assertEqual(order.required_provider_count,1)
        self.assertEqual(OrderSerializer(order).data['required_team_size'],1)
        self.assertFalse(OrderSerializer(order).data['can_team_apply'])
        self.assertEqual(self.apply(order,2,self.accounts[1]).data['code'],400)

    def test_original_double_snapshot_does_not_downgrade_when_catalog_changes(self):
        order = self.order(self.double)
        self.double.billing_provider_count = 1
        self.double.save(update_fields=['billing_provider_count'])
        self.assertEqual(OrderSerializer(order).data['required_team_size'],2)
        self.assertTrue(OrderSerializer(order).data['can_team_apply'])
        with patch('orders.applications.eligible'):
            self.assertEqual(self.apply(order,2,self.accounts[1]).data['code'],0)

    def test_historical_single_without_explicit_double_snapshot_cannot_upgrade(self):
        order = self.order(self.double)
        Order.objects.filter(pk=order.pk).update(required_provider_count=None, gameplay_snapshot={})
        order.refresh_from_db()
        self.assertEqual(OrderSerializer(order).data['required_team_size'],1)
        self.assertEqual(self.apply(order,2,self.accounts[1]).data['code'],400)

    def test_historical_double_keeps_original_total_amount_and_participant_mode(self):
        order = self.order(amount=10001, provider_income=8001, shop_income=2000, escort_mode='DOUBLE', status='GRABBED')
        Order.objects.filter(pk=order.pk).update(required_provider_count=None, gameplay_snapshot={})
        order.refresh_from_db()
        row = OrderSerializer(order).data
        self.assertEqual(row['required_team_size'],2)
        order.refresh_from_db()
        self.assertEqual((order.amount,order.provider_income,order.shop_income,order.escort_mode),(10001,8001,2000,'DOUBLE'))

    def test_admin_cannot_create_double_assignment_from_single_package_before_debit(self):
        self.auth(self.admin_account)
        options = self.client.get('/api/admin/options/service-items/').data['data']['list']
        by_id = {row['id']:row for row in options}
        self.assertEqual(by_id[self.single.pk]['billing_provider_count'],1)
        self.assertEqual(by_id[self.double.pk]['billing_provider_count'],2)
        self.assertEqual(by_id[self.single.pk]['service_type'],self.single.service_type)
        response = self.client.post('/api/admin/orders/dispatch/', {
            'customer_id':self.boss_account.pk, 'service_id':self.single.pk, 'escort_mode':'DOUBLE',
            'providers':[{'provider_id':account.pk} for account in self.accounts],
            'client_request_id':'single-must-not-be-double',
        }, format='json')
        self.assertEqual(response.data['code'],400,response.data)
        self.assertFalse(Order.objects.exists())
        self.assertFalse(Transaction.objects.exists())
        self.assertEqual(Wallet.objects.get(user=self.boss).balance,100000)

    def test_both_single_assignment_routes_reject_original_double_package(self):
        order = self.order(self.double)
        self.double.billing_provider_count = 1
        self.double.save(update_fields=['billing_provider_count'])
        self.auth(self.admin_account)
        for path in [f'/api/admin/orders/{order.pk}/assign/',f'/api/orders/orders/{order.pk}/assign/']:
            with self.subTest(path=path):
                response = self.client.post(path, {'provider_id':self.accounts[0].pk}, format='json')
                self.assertEqual(response.data['code'],400,response.data)
                self.assertFalse(order.providers.exists())

    def test_double_self_quote_and_create_allow_platform_team_selection(self):
        self.auth(self.boss_account)
        with patch('orders.views._schedule_auto_cancel'):
            for path in ['/api/orders/quote/','/api/orders/create/']:
                response = self.client.post(path, {'service_id':self.double.pk,'dispatch_mode':'BOSS_SELECT'}, format='json')
                self.assertEqual(response.data['code'],0,response.data)
        order=Order.objects.get()
        self.assertEqual((order.required_provider_count,order.status),(2,'PENDING'))
        self.assertTrue(order.requires_boss_selection)
        self.assertFalse(order.providers.exists())
        self.assertEqual(Wallet.objects.get(user=self.boss).balance,90000)
