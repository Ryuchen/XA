import os
from unittest import skipUnless
from datetime import timedelta
from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import PointFundingLot
from engagement.services import config
from orders.models import Order
from orders.tests.factories import make_provider, make_service, make_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from wallet.services import get_wallet


@override_settings(ALLOWED_HOSTS=['*'], BOSS_FUNDING_SELECTION_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class ServiceFundingStoreTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '资金来源多店结算开放/付款来源快照/抢单窗口/补贴三方结算/VIP折扣重定价/店币消息属正式契约深水区（18-盘点 资金来源/结算/补贴/VIP待联调）；最终联调启用')
    def test_real_orders_use_only_the_current_store_sources_and_rate(self):
        first, second = resolve_store(), xinan_store()
        for store, host, rate in ((first, 'sl.esports.club', 125), (second, 'xa.esports.club', 175)):
            with store_context(store):
                operation = config(); operation.base_points_rate = rate
                operation.save(update_fields=['base_points_rate'])
                user = make_user(); account = get_or_create_account_for_legacy_user(user)
                wallet = get_wallet(account=account, user=user)
                wallet.balance = 1200; wallet.save(update_fields=['balance'])
                PointFundingLot.objects.create(account=account, source_key='same-principal', kind='PREPAID', remaining=1000)
                PointFundingLot.objects.create(account=account, source_key='same-gift', kind='GIFT', remaining=200)
                provider = make_provider(deposit_mode='CASH', deposit_paid=40000)
                recipient = get_or_create_account_for_legacy_user(provider)
                provider.escort_profile.account = recipient; provider.escort_profile.save(update_fields=['account'])
                service = make_service(price=1000)
                client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                response = client.post('/api/orders/create/', {'service_id': service.pk, 'provider_id': recipient.pk,
                    'gift_balance_amount': 200, 'client_request_id': 'same-store-funding-order'}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                order = Order.objects.get(pk=response.data['data']['id'])
                self.assertEqual(order.funding_terms['points_rate'], rate)
                self.assertEqual(dict(order.funding_allocations.values_list('kind', 'amount')),
                                 {'PREPAID': 800, 'GIFT': 200})
                wallet.refresh_from_db(); self.assertEqual(wallet.balance, 200)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '资金来源多店结算开放/付款来源快照/抢单窗口/补贴三方结算/VIP折扣重定价/店币消息属正式契约深水区（18-盘点 资金来源/结算/补贴/VIP待联调）；最终联调启用')
    def test_reservation_conversion_uses_each_store_wallet_and_rate(self):
        first, second = resolve_store(), xinan_store()
        for store, host, rate in ((first, 'sl.esports.club', 125), (second, 'xa.esports.club', 175)):
            with store_context(store):
                operation = config(); operation.base_points_rate = rate
                operation.save(update_fields=['base_points_rate'])
                user = make_user(); account = get_or_create_account_for_legacy_user(user)
                wallet = get_wallet(account=account, user=user)
                wallet.balance = 1200; wallet.save(update_fields=['balance'])
                PointFundingLot.objects.create(account=account, source_key='same-reservation-principal', kind='PREPAID', remaining=1000)
                PointFundingLot.objects.create(account=account, source_key='same-reservation-gift', kind='GIFT', remaining=200)
                provider = make_provider(deposit_mode='CASH', deposit_paid=40000)
                recipient = get_or_create_account_for_legacy_user(provider)
                provider.escort_profile.account = recipient; provider.escort_profile.save(update_fields=['account'])
                service = make_service(price=1000)
                boss = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                player = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(recipient)[0])
                start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
                created = boss.post('/api/orders/reservations/', {'service_id': service.pk,
                    'provider_account_id': recipient.pk, 'start_time': start.isoformat(),
                    'end_time': (start + timedelta(hours=1)).isoformat(), 'game_rounds': 1}, format='json')
                self.assertEqual(created.data['code'], 0, created.data)
                path = f"/api/orders/reservations/{created.data['data']['id']}/"
                self.assertEqual(player.post(path + 'confirm/', {}, format='json').data['code'], 0)
                converted = boss.post(path + 'convert/', {'gift_balance_amount': 200,
                    'client_request_id': 'same-store-reservation-funding'}, format='json')
                self.assertEqual(converted.data['code'], 0, converted.data)
                order = Order.objects.get(pk=converted.data['data']['order']['id'])
                self.assertEqual(order.funding_terms['points_rate'], rate)
                self.assertEqual(dict(order.funding_allocations.values_list('kind', 'amount')),
                                 {'PREPAID': 800, 'GIFT': 200})
                wallet.refresh_from_db(); self.assertEqual(wallet.balance, 200)
