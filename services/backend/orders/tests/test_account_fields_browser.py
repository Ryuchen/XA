"""Real admin account-field rule and boss checkout through the HTTP API."""
import os
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from wallet.services import get_wallet


@skipUnless(os.environ.get('XA_ACCOUNT_FIELDS_BROWSER') == '1', 'Requires isolated ASGI and local Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class AccountFieldsBrowserTest(LiveServerTestCase):
    def test_admin_sets_service_rule_and_boss_places_order(self):
        boss = make_user(); boss_account = get_or_create_account_for_legacy_user(boss)
        wallet = get_wallet(user=boss, account=boss_account); wallet.balance = 3000; wallet.save(update_fields=['balance'])
        provider = make_provider(deposit_mode='CASH', deposit_paid=40000)
        provider_account = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = provider_account; provider.escort_profile.save(update_fields=['account'])
        service = make_service(name='隔离资料配置服务', price=1000)
        perms = ['dictionary:view', 'dictionary:edit', 'order:view', 'order:dispatch']
        staff = make_console_user(perms); account = get_or_create_account_for_legacy_user(staff)
        link_legacy_relations(staff, account)
        staff.admin_membership.account = account; staff.admin_membership.save(update_fields=['account'])
        admin = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        saved = admin.put(f'/api/admin/config/order-account-fields/service/{service.pk}/',
                          {'fields': ['game_uid'], 'version': 0}, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)
        buyer = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss_account)[0])
        created = buyer.post('/api/orders/create/', {'service_id': service.pk, 'provider_id': provider_account.pk,
            'game_uid': 'browser-uid', 'client_request_id': 'account-fields-browser-order'}, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order = Order.objects.get(customer_account=boss_account)
        self.assertEqual((order.game_region, order.game_nickname, order.game_uid), ('', '', 'browser-uid'))
