"""Concurrent handoffs must serialize on the same real MySQL order lock."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderPrestartHandoff, OrderProvider
from orders.tests.factories import make_console_user, make_order, make_provider, make_user


@skipUnless(connection.vendor == 'mysql', 'Requires real MySQL row locks')
@override_settings(PRESTART_HANDOFF_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, PROVIDER_WECHAT_GATE_ENABLED=False)
class PrestartHandoffMySQLTest(TransactionTestCase):
    def test_competing_replacements_cannot_both_succeed(self):
        buyer = make_user(); boss = get_or_create_account_for_legacy_user(buyer)
        players = [make_provider(deposit_mode='EARNINGS') for _ in range(3)]
        accounts = [get_or_create_account_for_legacy_user(player) for player in players]
        for user, account in zip(players, accounts):
            user.escort_profile.account = account; user.escort_profile.save(update_fields=['account'])
        staff = make_console_user(['order:view','order:dispatch']); operator = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = operator; staff.admin_membership.save(update_fields=['account'])
        order = make_order(buyer, customer_account=boss, provider=players[0], provider_account=accounts[0],
            status='GRABBED', payment_status='PAID', amount=10000, provider_income=8000, commission_rate=20, shop_income=2000)
        OrderProvider.objects.create(order=order, provider=players[0], provider_account=accounts[0],
                                     settlement_base=10000, provider_income=8000, commission_rate=20)
        token = issue_account_tokens(operator)[0]
        barrier = Barrier(2)
        def submit(replacement):
            close_old_connections()
            try:
                client = APIClient(HTTP_AUTHORIZATION='Bearer '+token)
                barrier.wait(timeout=10)
                return client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason':'并发改派测试', 'transfers':[{
                    'old_provider_id':accounts[0].pk, 'new_provider_id':replacement}]}, format='json').data
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, [accounts[1].pk, accounts[2].pk]))
        self.assertEqual(sum(result['code'] == 0 for result in results), 1, results)
        order.refresh_from_db()
        self.assertIn(order.provider_account_id, [accounts[1].pk, accounts[2].pk])
        self.assertEqual(OrderPrestartHandoff.objects.filter(order=order).count(), 1)
        self.assertEqual(order.providers.filter(released_at__isnull=True).count(), 1)
