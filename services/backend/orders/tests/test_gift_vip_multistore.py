"""Gift principal and VIP switches are scoped to each store's business database."""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import ConsumptionConfirmation, PointLedger
from engagement.services import config
from orders.tests.factories import make_user, make_provider, make_service, make_service_category
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from wallet.services import get_wallet


@override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class GiftVipStoreIsolationTest(TestCase):
    databases = {'default', 'store_b'}

    def test_each_store_uses_its_own_ordinary_points_multiplier(self):
        cases = [(resolve_store(), 'sl.esports.club', 125), (xinan_store(), 'xa.esports.club', 175)]
        for store, host, rate in cases:
            with store_context(store):
                conf = config(); conf.vip_enabled = True; conf.points_enabled = True; conf.base_points_rate = rate
                conf.save(update_fields=['vip_enabled', 'points_enabled', 'base_points_rate'])
                boss, provider = make_user(), make_provider(is_verified=True)
                owner = get_or_create_account_for_legacy_user(boss)
                recipient = get_or_create_account_for_legacy_user(provider)
                provider.escort_profile.account = recipient; provider.escort_profile.save(update_fields=['account'])
                wallet = get_wallet(account=owner, user=boss)
                wallet.balance = 5000; wallet.save(update_fields=['balance'])
                gift = make_service(price=2100, service_category=make_service_category(is_gift=True))
                client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(owner)[0])
                result = client.post('/api/orders/gifts/send/', {'gift_service_id': gift.pk, 'provider_id': recipient.pk,
                    'quantity': 1, 'expected_amount': 2100, 'client_request_id': 'store-gift-points-test'}, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
                ledger = PointLedger.objects.get(order_id=result.data['data']['id'], kind='CONSUMPTION')
                self.assertEqual(ledger.amount, 2100 * rate // 100)

    def test_each_store_uses_its_own_vip_switch(self):
        cases = [(resolve_store(), 'sl.esports.club', True), (xinan_store(), 'xa.esports.club', False)]
        for store, host, enabled in cases:
            with store_context(store):
                conf = config(); conf.vip_enabled = enabled; conf.points_enabled = True
                conf.save(update_fields=['vip_enabled', 'points_enabled'])
                boss, provider = make_user(), make_provider(is_verified=True)
                owner = get_or_create_account_for_legacy_user(boss)
                recipient = get_or_create_account_for_legacy_user(provider)
                provider.escort_profile.account = recipient; provider.escort_profile.save(update_fields=['account'])
                wallet = get_wallet(account=owner, user=boss)
                wallet.balance = 5000; wallet.save(update_fields=['balance'])
                gift = make_service(price=2100, service_category=make_service_category(is_gift=True))
                client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(owner)[0])
                result = client.post('/api/orders/gifts/send/', {'gift_service_id': gift.pk, 'provider_id': recipient.pk,
                    'quantity': 1, 'expected_amount': 2100, 'client_request_id': 'store-gift-vip-test'}, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
                order_id = result.data['data']['id']
                self.assertEqual(ConsumptionConfirmation.objects.filter(order_id=order_id).exists(), enabled)
                self.assertEqual(PointLedger.objects.filter(order_id=order_id, kind='CONSUMPTION').exists(), enabled)
