from unittest import skipUnless

from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import AfterSale, Order, OrderProvider
from orders.tests.factories import make_order, make_provider, make_service, make_user
from stores.context import resolve_store, store_context
from stores.models import Store


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
@skipUnless('store_b' in settings.DATABASES, 'requires two isolated store databases')
class AfterSaleScopeStoreBoundaryTests(APITestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_same_order_and_account_ids_only_affect_current_store(self):
        original = resolve_store()
        branch = Store.objects.create(slug='xinan', name='兴安测试', database_alias='store_b',
                                      status=Store.Status.ACTIVE)
        fixtures = {}
        for store in (original, branch):
            with store_context(store):
                boss = make_user()
                provider = make_provider(deposit_mode='EARNINGS')
                boss_account = get_or_create_account_for_legacy_user(boss)
                provider_account = get_or_create_account_for_legacy_user(provider)
                provider.escort_profile.account = provider_account
                provider.escort_profile.save(update_fields=['account'])
                service = make_service(name=f'{store.slug}-服务')
                order = make_order(boss, service, provider, pk=87221, order_no='ORD' + 'B' * 20,
                                   customer_account=boss_account, provider_account=provider_account,
                                   status=Order.Status.COMPLETED, payment_status=Order.PaymentStatus.PAID,
                                   completed_at=timezone.now(), settlement_mode='REPORT_APPROVAL')
                OrderProvider.objects.create(order=order, provider=provider, provider_account=provider_account,
                                             settlement_base=order.amount, provider_income=0)
                client = APIClient(HTTP_X_STORE_SLUG=store.slug)
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss_account)[0])
                fixtures[store.slug] = (boss_account.pk, provider_account.pk, client)
        self.assertEqual(fixtures[original.slug][:2], fixtures[branch.slug][:2])
        with store_context(branch):
            account_id = fixtures[branch.slug][1]
            response = fixtures[branch.slug][2].post('/api/orders/orders/87221/aftersale/', {
                'reason': '兴安门店售后', 'client_request_id': 'same-case-key',
                'affected_provider_account_ids': [account_id],
            }, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            self.assertEqual(AfterSale.objects.get(order_id=87221).affected_provider_account_ids, [account_id])
        with store_context(original):
            self.assertFalse(AfterSale.objects.filter(order_id=87221).exists())
            account_id = fixtures[original.slug][1]
            response = fixtures[original.slug][2].post('/api/orders/orders/87221/aftersale/', {
                'reason': '声浪门店售后', 'client_request_id': 'same-case-key',
                'affected_provider_account_ids': [account_id],
            }, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            self.assertEqual(AfterSale.objects.get(order_id=87221).reason, '声浪门店售后')
        with store_context(branch):
            self.assertEqual(AfterSale.objects.get(order_id=87221).reason, '兴安门店售后')
