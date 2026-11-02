"""Actual gift refund receipt for explicit and legacy order identities."""
from tempfile import TemporaryDirectory

from django.test import override_settings
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, ServiceCategory
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from orders.tests.test_release_flow import screenshot


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class LegacyGiftNamingReceiptTest(APITransactionTestCase):
    def test_gift_refund_receipt_respects_explicit_accounts_and_legacy_fallback(self):
        media = TemporaryDirectory(prefix='xa-legacy-gift-naming-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss, provider, outsider = make_user(), make_provider(is_verified=True, deposit_mode='CASH'), make_user()
        staff = make_console_user(['wallet:recharge', 'order:view', 'order:refund'])
        users = (boss, provider, outsider, staff)
        accounts = [get_or_create_account_for_legacy_user(user) for user in users]
        for user, account in zip(users, accounts):
            link_legacy_relations(user, account)
        provider.escort_profile.account = accounts[1]
        provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = accounts[3]
        staff.admin_membership.save(update_fields=['account'])
        category = ServiceCategory.objects.create(name='隔离冠名', is_gift=True)
        gift = make_service(name='隔离冠名礼物', price=4000, commission_rate=20,
                            service_category=category, gift_naming_days=2)
        clients = [APIClient() for _ in users]
        for client, account in zip(clients, accounts):
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        def ok(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']
        ok(clients[3].post('/api/admin/wallets/recharge/', {'account_id':accounts[0].pk,
            'amount':20000, 'gift_amount':0, 'trade_no':'legacy-gift-recharge',
            'proof_image':screenshot(), 'client_request_id':'legacy-gift-recharge'}, format='multipart'))
        receipt = ok(clients[0].post('/api/orders/gifts/send/', {'gift_service_id':gift.pk,
            'provider_id':accounts[1].pk, 'quantity':1, 'naming_title':'隔离冠名',
            'expected_amount':4000, 'client_request_id':'legacy-gift-send'}, format='json'))
        order_id = receipt['id']
        preview = ok(clients[3].get(f'/api/admin/orders/{order_id}/refund-preview/'))
        ok(clients[3].post(f'/api/admin/orders/{order_id}/refund/', {'reason':'隔离冠名退款',
            'plan_token':preview['plan_token'], 'client_request_id':'legacy-gift-refund'}, format='json'))
        def result(client):
            return ok(client.get(f'/api/orders/orders/{order_id}/'))['refund_summary']['records'][0]
        expected = result(clients[0])['gift_naming_result']
        self.assertEqual(result(clients[1])['gift_naming_result'], expected)
        self.assertEqual(expected['remaining_paid_grants'], 0)
        self.assertIn('gift_naming', ok(clients[3].get(f'/api/admin/orders/{order_id}/'))['refund_summary']['records'][0]['benefit_effects'])
        self.assertEqual(clients[2].get(f'/api/orders/orders/{order_id}/').status_code, 404)
        # Represent a pre-account migration order while keeping the same real refund ledger.
        Order.objects.filter(pk=order_id).update(customer_account=None, provider_account=None)
        self.assertEqual(result(clients[0])['gift_naming_result'], expected)
        self.assertEqual(result(clients[1])['gift_naming_result'], expected)
        self.assertEqual(clients[2].get(f'/api/orders/orders/{order_id}/').status_code, 404)
        # Explicit business accounts override conflicting legacy user columns.
        other_provider = make_provider(is_verified=True, deposit_mode='CASH')
        other_account = get_or_create_account_for_legacy_user(other_provider)
        link_legacy_relations(other_provider, other_account)
        other_provider.escort_profile.account = other_account
        other_provider.escort_profile.save(update_fields=['account'])
        other_client = APIClient()
        other_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(other_account)[0])
        Order.objects.filter(pk=order_id).update(customer=outsider, customer_account=accounts[0],
                                                provider=other_provider, provider_account=accounts[1])
        self.assertEqual(result(clients[0])['gift_naming_result'], expected)
        self.assertEqual(result(clients[1])['gift_naming_result'], expected)
        self.assertEqual(clients[2].get(f'/api/orders/orders/{order_id}/').status_code, 404)
        self.assertEqual(other_client.get(f'/api/orders/orders/{order_id}/').status_code, 404)
