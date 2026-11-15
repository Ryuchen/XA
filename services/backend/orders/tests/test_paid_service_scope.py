from datetime import timedelta
from unittest.mock import patch
from unittest import skipUnless

from django.conf import settings
from django.test import TestCase
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from orders.models import Order, OrderPaidServiceScope
from orders.tests import test_prestart_handoff as handoff_fixture
from orders.tests import test_game_commission_payment as game_fixture
from orders.tests.factories import make_console_user
from users.models import EscortLevel
from wallet.models import Transaction


@override_settings(PRESTART_HANDOFF_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class PaidScopeHandoffTest(APITransactionTestCase):
    setUp = handoff_fixture.PrestartHandoffTest.setUp
    login = handoff_fixture.PrestartHandoffTest.login
    ok = handoff_fixture.PrestartHandoffTest.ok
    recharge = handoff_fixture.PrestartHandoffTest.recharge
    new_order = handoff_fixture.PrestartHandoffTest.new_order
    book = handoff_fixture.PrestartHandoffTest.book
    replacement = handoff_fixture.PrestartHandoffTest.replacement
    transfer = handoff_fixture.PrestartHandoffTest.transfer

    def test_paid_non_game_scope_preserves_original_minimum_level(self):
        required = EscortLevel.objects.create(name='原最低级', commission_rate=20, sort_order=10)
        original = EscortLevel.objects.create(name='原陪玩等级', commission_rate=20, sort_order=20)
        incoming = EscortLevel.objects.create(name='接替陪玩等级', commission_rate=20, sort_order=5)
        self.service.required_level = required; self.service.save(update_fields=['required_level'])
        self.provider.escort_profile.level = original; self.provider.escort_profile.save(update_fields=['level'])
        new = self.replacement()
        new.escort_profile.level = incoming; new.escort_profile.save(update_fields=['level'])
        order = self.new_order()
        scope = OrderPaidServiceScope.objects.get(order=order).scope
        self.assertEqual((scope['required_level_id'], scope['required_level_rank']), (required.pk, 10))
        self.service.required_level = None; self.service.save(update_fields=['required_level'])
        required.sort_order = 1; required.save(update_fields=['sort_order'])
        self.assertNotEqual(self.transfer(order.pk, self.provider, new).data['code'], 0)
        incoming.sort_order = 11; incoming.save(update_fields=['sort_order'])
        self.ok(self.transfer(order.pk, self.provider, new))
        self.assertEqual(OrderPaidServiceScope.objects.get(order=order).scope, scope)

    def test_service_level_change_before_debit_is_rejected(self):
        from orders import views
        level = EscortLevel.objects.create(name='最低级', commission_rate=20, sort_order=1)
        self.service.required_level = level; self.service.save(update_fields=['required_level'])
        self.provider.escort_profile.level = level; self.provider.escort_profile.save(update_fields=['level'])
        self.ok(self.recharge()); self.login(self.boss)
        original = views.ensure_provider_dispatch_locks
        def change(ids):
            original(ids)
            EscortLevel.objects.filter(pk=level.pk).update(sort_order=8)
        with patch('orders.views.ensure_provider_dispatch_locks', side_effect=change):
            result = self.client.post('/api/orders/create/', {'service_id':self.service.pk,
                'client_request_id':'changed-paid-scope'}, format='json')
        self.assertEqual(result.status_code, 409, result.data)
        self.assertEqual(result.data['errorCode'], 'SERVICE_CHANGED')
        self.assertFalse(Order.objects.exists())
        self.assertFalse(Transaction.objects.filter(tx_type='PAY').exists())

    def test_appointment_conversion_records_paid_scope(self):
        self.ok(self.recharge())
        start = (timezone.now()+timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        _, order_id = self.book(self.provider, start, 'paid-scope-appointment')
        scope = OrderPaidServiceScope.objects.get(order_id=order_id).scope
        self.assertEqual((scope['service_id'], scope['required_level_id']), (self.service.pk, None))

    def test_historical_non_game_order_without_scope_is_not_inferred(self):
        new = self.replacement()
        with override_settings(PRESTART_SCOPE_SNAPSHOT_ENABLED=False):
            order = self.new_order()
        self.assertFalse(OrderPaidServiceScope.objects.filter(order=order).exists())
        denied = self.transfer(order.pk, self.provider, new)
        self.assertIn('历史订单未保存原服务范围', denied.data['msg'])
        order.refresh_from_db(); self.assertEqual(order.provider_account_id, self.accounts[self.provider.pk].pk)

    def test_snapshot_write_failure_rolls_back_order_and_wallet(self):
        from django.db import DatabaseError
        from wallet.models import Wallet
        self.ok(self.recharge()); before = Wallet.objects.get(account=self.accounts[self.boss.pk]).balance
        self.login(self.boss)
        with patch('orders.models.OrderPaidServiceScope.objects.create', side_effect=DatabaseError('scope write failed')):
            with self.assertRaises(DatabaseError):
                self.client.post('/api/orders/create/', {'service_id':self.service.pk,
                    'client_request_id':'failed-paid-scope'}, format='json')
        self.assertFalse(Order.objects.exists())
        self.assertEqual(Wallet.objects.get(account=self.accounts[self.boss.pk]).balance, before)
        self.assertFalse(Transaction.objects.filter(tx_type='PAY').exists())


@override_settings(PRESTART_SCOPE_SNAPSHOT_ENABLED=True, GAME_COMMISSION_PAYMENT_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class PaidScopeStaffTest(APITransactionTestCase):
    setUp = game_fixture.GameCommissionPaymentTest.setUp
    link = game_fixture.GameCommissionPaymentTest.link
    auth = game_fixture.GameCommissionPaymentTest.auth

    def test_staff_order_records_same_scope_as_game_commission(self):
        staff = make_console_user(['order:view', 'order:dispatch']); account = self.link(staff)
        staff.admin_membership.account = account; staff.admin_membership.save(update_fields=['account'])
        self.auth(account)
        result = self.client.post('/api/admin/orders/dispatch/', {'customer_id':self.boss_account.pk,
            'service_id':self.service.pk, 'provider_id':self.accounts[0].pk,
            'client_request_id':'staff-paid-scope'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get(pk=result.data['data']['id'])
        self.assertEqual(OrderPaidServiceScope.objects.get(order=order).scope,
                         order.game_commission_snapshot['service_scope'])

    @override_settings(GAME_COMMISSION_PAYMENT_ENABLED=False)
    def test_staff_scope_changed_before_debit_is_rejected(self):
        from console import views
        staff = make_console_user(['order:view', 'order:dispatch']); account = self.link(staff)
        staff.admin_membership.account = account; staff.admin_membership.save(update_fields=['account'])
        self.auth(account)
        original = views.ensure_provider_dispatch_locks
        def changed(ids):
            original(ids)
            type(self.service).objects.filter(pk=self.service.pk).update(catalog_family='new family')
        with patch('console.views.ensure_provider_dispatch_locks', side_effect=changed):
            result = self.client.post('/api/admin/orders/dispatch/', {'customer_id':self.boss_account.pk,
                'service_id':self.service.pk, 'provider_id':self.accounts[0].pk,
                'client_request_id':'staff-scope-race'}, format='json')
        self.assertEqual(result.status_code, 409, result.data)
        self.wallet.refresh_from_db(); self.assertEqual(self.wallet.balance, 100000)
        self.assertFalse(Order.objects.exists())

    @override_settings(PRESTART_HANDOFF_ENABLED=True)
    def test_conflicting_game_and_payment_scope_reject_handoff(self):
        self.auth(self.boss_account)
        response = self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'provider_id':self.accounts[0].pk, 'client_request_id':'scope-corruption'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        saved = OrderPaidServiceScope.objects.get(order=order)
        saved.scope['provider_kind'] = 'COMPANION'; saved.save(update_fields=['scope'])
        staff = make_console_user(['order:view', 'order:dispatch']); account = self.link(staff)
        staff.admin_membership.account = account; staff.admin_membership.save(update_fields=['account'])
        self.auth(account)
        result = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason':'更换陪玩', 'transfers':[{
            'old_provider_id':self.accounts[0].pk, 'new_provider_id':self.accounts[1].pk}]}, format='json')
        self.assertIn('服务范围快照不一致', result.data['msg'])
        order.refresh_from_db(); self.assertEqual(order.provider_account_id, self.accounts[0].pk)


@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
@override_settings(PRESTART_SCOPE_SNAPSHOT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class PaidScopeStoreTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_same_order_ids_keep_store_specific_scope(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from club_accounts.tokens import issue_account_tokens
        from orders.tests.factories import make_provider, make_service, make_user
        from rest_framework.test import APIClient
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        from wallet.services import get_wallet
        saved = []
        for store, host, rank in ((resolve_store(), 'sl.esports.club', 2), (xinan_store(), 'xa.esports.club', 7)):
            with store_context(store):
                boss = make_user(); account = get_or_create_account_for_legacy_user(boss)
                wallet = get_wallet(account=account, user=boss); wallet.balance = 2000; wallet.save(update_fields=['balance'])
                level = EscortLevel.objects.create(name='本店等级', commission_rate=20, sort_order=rank)
                provider = make_provider(deposit_mode='EARNINGS'); player = get_or_create_account_for_legacy_user(provider)
                provider.escort_profile.account = player; provider.escort_profile.level = level
                provider.escort_profile.save(update_fields=['account','level'])
                service = make_service(price=1000, required_level=level)
                client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                response = client.post('/api/orders/create/', {'service_id':service.pk, 'provider_id':player.pk,
                    'client_request_id':'same-order-scope'}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                order_id = response.data['data']['id']
                saved.append((order_id, OrderPaidServiceScope.objects.get(order_id=order_id).scope['required_level_rank']))
        self.assertEqual(saved[0][0], saved[1][0])
        self.assertEqual([row[1] for row in saved], [2, 7])
        with store_context(resolve_store()):
            self.assertEqual(OrderPaidServiceScope.objects.get(order_id=saved[0][0]).scope['required_level_rank'], 2)

    def test_pool_uses_only_current_stores_paid_scope(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from club_accounts.tokens import issue_account_tokens
        from orders.tests.factories import make_provider, make_service, make_user
        from rest_framework.test import APIClient
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        from wallet.services import get_wallet
        results = []
        for store, host, required_rank in ((resolve_store(), 'sl.esports.club', 2), (xinan_store(), 'xa.esports.club', 7)):
            with store_context(store):
                boss = make_user(); account = get_or_create_account_for_legacy_user(boss)
                wallet = get_wallet(account=account, user=boss); wallet.balance = 2000; wallet.save(update_fields=['balance'])
                required = EscortLevel.objects.create(name='最低等级', commission_rate=20, sort_order=required_rank)
                player_level = EscortLevel.objects.create(name='陪玩等级', commission_rate=20, sort_order=3)
                provider = make_provider(deposit_mode='EARNINGS'); player = get_or_create_account_for_legacy_user(provider)
                provider.escort_profile.account = player; provider.escort_profile.level = player_level
                provider.escort_profile.save(update_fields=['account','level'])
                service = make_service(price=1000, required_level=required)
                boss_client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                response = boss_client.post('/api/orders/create/', {'service_id':service.pk, 'dispatch_mode':'AUTO_GRAB',
                    'client_request_id':'same-store-pool'}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                order_id = response.data['data']['id']
                Order.objects.filter(pk=order_id).update(pool_entered_at=timezone.now()-timedelta(minutes=10),
                                                        auto_cancel_at=timezone.now()+timedelta(hours=1))
                service.required_level = None; service.save(update_fields=['required_level'])
                player_client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(player)[0])
                pool = player_client.get('/api/orders/orders/', {'status':'pending'}).data['data']
                results.append((order_id, order_id in [row['id'] for row in pool]))
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual([row[1] for row in results], [True, False])
