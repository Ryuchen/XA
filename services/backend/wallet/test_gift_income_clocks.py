from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import AfterSale, Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_service_category, make_user
from wallet.income import release_due_income
from wallet.models import IncomeHold, SystemConfig, Transaction
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, LOGIN_OTP_REQUIRED=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class GiftIncomeClocksTest(APITestCase):
    def setUp(self):
        self.boss, self.provider = make_user(), make_provider(display_name='隔离礼物陪玩')
        self.staff = make_console_user(['order:view', 'order:refund', 'deposit_rule:view', 'deposit_rule:edit'])
        self.accounts = [get_or_create_account_for_legacy_user(user) for user in (self.boss, self.provider, self.staff)]
        for user, account in zip((self.boss, self.provider, self.staff), self.accounts):
            link_legacy_relations(user, account)
        self.provider.escort_profile.account = self.accounts[1]
        self.provider.escort_profile.save(update_fields=['account'])
        self.staff.admin_membership.account = self.accounts[2]
        self.staff.admin_membership.save(update_fields=['account'])
        self.boss_wallet = get_wallet(account=self.accounts[0], user=self.boss)
        self.boss_wallet.balance = 20000; self.boss_wallet.save(update_fields=['balance'])
        self.provider_wallet = get_wallet(account=self.accounts[1], user=self.provider)
        self.gift = make_service(name='隔离礼物时限', price=2000, service_category=make_service_category(is_gift=True))
        self.clients = []
        for account in self.accounts:
            client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            self.clients.append(client)

    def send(self, key):
        response = self.clients[0].post('/api/orders/gifts/send/', {'gift_service_id':self.gift.pk,
            'provider_id':self.accounts[1].pk, 'quantity':1, 'expected_amount':2000, 'client_request_id':key}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        return Order.objects.get(pk=response.data['data']['id'])

    def configure(self, aftersale, freeze, version=0):
        response = self.clients[2].put('/api/admin/config/settlement-clocks', {'aftersale_hours':aftersale,
            'income_freeze_hours':freeze, 'expected_version':version}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data['data']

    def test_snapshot_freezes_immediately_and_configuration_only_changes_new_gifts(self):
        old = self.send('gift-clock-old')
        old_hold = IncomeHold.objects.get(income_transaction__order=old)
        self.provider_wallet.refresh_from_db()
        self.assertEqual((old.gift_aftersale_hours, old.gift_freeze_hours), (72,72))
        self.assertEqual((self.provider_wallet.balance, self.provider_wallet.frozen_amount), (0,1600))
        self.assertEqual(old_hold.release_at, old.completed_at + timedelta(hours=72))
        self.configure(6,12)
        new = self.send('gift-clock-new')
        old.refresh_from_db()
        self.assertEqual((old.gift_aftersale_hours, old.gift_freeze_hours), (72,72))
        self.assertEqual((new.gift_aftersale_hours, new.gift_freeze_hours), (6,12))
        self.assertEqual(IncomeHold.objects.get(income_transaction__order=new).release_at, new.completed_at + timedelta(hours=12))
        benefits = self.clients[1].get('/api/wallet/provider-benefits/').data['data']['gift_records']
        self.assertEqual({item['settlement_status'] for item in benefits}, {'FROZEN'})
        self.assertEqual({item['frozen_income'] for item in benefits}, {1600})

    def test_open_aftersale_blocks_release_even_after_both_clocks_then_resolution_unblocks(self):
        self.configure(1,2)
        order = self.send('gift-clock-aftersale')
        response = self.clients[0].post(f'/api/orders/orders/{order.pk}/aftersale/',
            {'reason':'核实赠礼订单', 'client_request_id':'gift-case-1'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        hold = IncomeHold.objects.get(income_transaction__order=order)
        future = order.completed_at + timedelta(hours=3)
        self.assertEqual(release_due_income(now=future), 0)
        self.provider_wallet.refresh_from_db()
        self.assertEqual((self.provider_wallet.balance, self.provider_wallet.frozen_amount), (0,1600))
        case = AfterSale.objects.get(order=order); case.status='RESOLVED'; case.save(update_fields=['status'])
        self.assertEqual(release_due_income(now=future), 1)
        self.assertEqual(release_due_income(now=future), 0)
        hold.refresh_from_db(); self.provider_wallet.refresh_from_db()
        self.assertIsNotNone(hold.released_at)
        self.assertEqual((self.provider_wallet.balance, self.provider_wallet.frozen_amount), (1600,0))

    def test_release_requires_the_later_of_both_independent_deadlines(self):
        self.configure(4, 2)
        order = self.send('gift-clock-aftersale-later')
        deadline = order.completed_at + timedelta(hours=4)
        self.assertEqual(release_due_income(now=deadline - timedelta(microseconds=1)), 0)
        self.assertEqual(release_due_income(now=deadline), 1)
        self.assertEqual(release_due_income(now=deadline), 0)
        self.provider_wallet.refresh_from_db()
        self.assertEqual((self.provider_wallet.balance, self.provider_wallet.frozen_amount), (1600, 0))

    def test_refund_uses_frozen_gift_earnings_and_does_not_release_refunded_amount(self):
        order = self.send('gift-clock-refund')
        preview = self.clients[2].get(f'/api/admin/orders/{order.pk}/refund-preview/', {'refund_amount':1000})
        self.assertEqual(preview.data['code'], 0, preview.data)
        result = self.clients[2].post(f'/api/admin/orders/{order.pk}/refund/', {'refund_amount':1000,
            'reason':'隔离礼物部分退款', 'plan_token':preview.data['data']['plan_token'],
            'client_request_id':'gift-clock-refund-part'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        hold = IncomeHold.objects.get(income_transaction__order=order)
        self.assertEqual((hold.amount, hold.recovered_amount), (1600,800))
        self.provider_wallet.refresh_from_db()
        self.assertEqual((self.provider_wallet.balance, self.provider_wallet.frozen_amount), (0,800))
        benefit = self.clients[1].get('/api/wallet/provider-benefits/').data['data']['gift_records'][0]
        self.assertEqual((benefit['settlement_status'], benefit['frozen_income']), ('PARTIALLY_REFUNDED_FROZEN',800))
        self.assertEqual(release_due_income(now=order.completed_at+timedelta(hours=73)), 1)
        self.provider_wallet.refresh_from_db()
        self.assertEqual((self.provider_wallet.balance, self.provider_wallet.frozen_amount), (800,0))

    def test_expired_window_blocks_new_case_and_new_admin_refund(self):
        self.configure(1,2)
        order = self.send('gift-clock-expired')
        Order.objects.filter(pk=order.pk).update(completed_at=timezone.now()-timedelta(hours=2))
        response = self.clients[0].post(f'/api/orders/orders/{order.pk}/aftersale/',
            {'reason':'过期售后', 'client_request_id':'gift-case-late'}, format='json')
        self.assertNotEqual(response.data.get('code', 400), 0)
        preview = self.clients[2].get(f'/api/admin/orders/{order.pk}/refund-preview/')
        self.assertFalse(preview.data['data']['eligible'])
        response = self.clients[2].post(f'/api/admin/orders/{order.pk}/refund/',
            {'reason':'过期退款', 'plan_token':preview.data['data']['plan_token']}, format='json')
        self.assertNotEqual(response.data.get('code', 400), 0)
        self.provider_wallet.refresh_from_db()
        self.assertEqual((self.provider_wallet.balance, self.provider_wallet.frozen_amount), (0,1600))

    def test_zero_clocks_and_version_conflict(self):
        self.configure(0,0)
        stale = self.clients[2].put('/api/admin/config/settlement-clocks', {'aftersale_hours':1,
            'income_freeze_hours':1, 'expected_version':0}, format='json')
        self.assertEqual(stale.status_code, 409)
        order = self.send('gift-clock-zero')
        self.assertFalse(IncomeHold.objects.filter(income_transaction__order=order).exists())
        self.provider_wallet.refresh_from_db()
        self.assertEqual((self.provider_wallet.balance, self.provider_wallet.frozen_amount), (1600,0))

    def test_unrelated_due_hold_is_not_starved_by_open_gift_case(self):
        self.configure(1,1)
        first = self.send('gift-clock-blocked')
        second = self.send('gift-clock-ready')
        AfterSale.objects.create(order=first, account=self.accounts[0], reason='待核实')
        future = second.completed_at + timedelta(hours=2)
        self.assertEqual(release_due_income(now=future, limit=1), 1)
        self.assertIsNone(IncomeHold.objects.get(income_transaction__order=first).released_at)
        self.assertIsNotNone(IncomeHold.objects.get(income_transaction__order=second).released_at)
