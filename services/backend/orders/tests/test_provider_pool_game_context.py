"""Game identifiers should remain with the buyer, staff and assigned provider."""
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderProvider
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ProviderPoolGameContextTest(APITransactionTestCase):
    def test_public_pool_does_not_expose_unassigned_game_identity(self):
        boss, provider = make_user(phone='13800000001'), make_provider(deposit_mode='EARNINGS', pass_tier='BLACK',
            pass_expires_at=timezone.now() + timezone.timedelta(days=7))
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, provider)]
        provider.escort_profile.account = accounts[1]
        provider.escort_profile.save(update_fields=['account'])
        service = make_service(price=10000)
        provider.escort_profile.service_items.add(service)
        make_wallet(boss, balance=50000)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[0])[0])
        created = self.client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC',
            'dispatch_mode': 'AUTO_GRAB', 'game_region': '隔离大区', 'game_nickname': '隔离游戏昵称',
            'game_uid': 'ISOLATED-UID-123', 'client_request_id': 'pool-game-privacy'}, format='json').data
        self.assertEqual(created['code'], 0, created)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[1])[0])
        response = self.client.get('/api/orders/orders/', {'status': 'pending'}).data
        self.assertEqual(response['code'], 0, response)
        order = next(row for row in response['data'] if row['id'] == created['data']['id'])
        self.assertEqual(order['game_region'], '隔离大区')
        self.assertFalse(order['game_nickname'])
        self.assertFalse(order['game_uid'])
        self.assertFalse(order['customer']['phone'])
        result = self.client.post(f"/api/orders/orders/{order['id']}/grab/", {}, format='json').data
        self.assertEqual(result['code'], 0, result)
        detail = self.client.get(f"/api/orders/orders/{order['id']}/").data['data']
        self.assertEqual((detail['game_nickname'], detail['game_uid']), ('隔离游戏昵称', 'ISOLATED-UID-123'))
        self.assertEqual(detail['customer']['phone'], '13800000001')
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[0])[0])
        detail = self.client.get(f"/api/orders/orders/{order['id']}/").data['data']
        self.assertEqual((detail['game_nickname'], detail['game_uid']), ('隔离游戏昵称', 'ISOLATED-UID-123'))
        self.assertEqual(detail['customer']['phone'], '13800000001')
        staff = make_console_user(['order:view'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        detail = self.client.get(f"/api/admin/orders/{order['id']}/").data['data']
        self.assertEqual((detail['game_nickname'], detail['game_uid']), ('隔离游戏昵称', 'ISOLATED-UID-123'))
        self.assertEqual(detail['customer_phone'], '13800000001')
        # Historical orders may only link the legacy user; do not break access.
        Order.objects.filter(pk=order['id']).update(provider_account=None)
        OrderProvider.objects.filter(order_id=order['id']).update(provider_account=None)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[1])[0])
        detail = self.client.get(f"/api/orders/orders/{order['id']}/").data['data']
        self.assertEqual((detail['game_nickname'], detail['game_uid']), ('隔离游戏昵称', 'ISOLATED-UID-123'))
        self.assertEqual(detail['customer']['phone'], '13800000001')

    def test_unselected_applicant_cannot_read_identity_after_selection(self):
        boss = make_user(phone='13800000002')
        providers = [make_provider(deposit_mode='EARNINGS', pass_tier='BLACK',
            pass_expires_at=timezone.now() + timezone.timedelta(days=7)) for _ in range(2)]
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, *providers)]
        service = make_service(price=10000)
        for user, account in zip(providers, accounts[1:]):
            user.escort_profile.account = account
            user.escort_profile.save(update_fields=['account'])
            user.escort_profile.service_items.add(service)
        make_wallet(boss, balance=50000)
        def auth(account):
            self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        auth(accounts[0])
        created = self.client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC',
            'dispatch_mode': 'BOSS_SELECT', 'game_nickname': '隔离昵称', 'game_uid': 'ISOLATED-UID',
            'client_request_id': 'pool-selection-privacy'}, format='json').data
        self.assertEqual(created['code'], 0, created)
        order_id = created['data']['id']
        applications = []
        for account in accounts[1:]:
            auth(account)
            response = self.client.post(f'/api/orders/orders/{order_id}/grab/', {}, format='json').data
            self.assertEqual(response['code'], 0, response)
            applications.append(response['data']['my_applications'][0]['id'])
        auth(accounts[0])
        response = self.client.post(f'/api/orders/orders/{order_id}/applications/{applications[0]}/select/', {}, format='json').data
        self.assertEqual(response['code'], 0, response)
        auth(accounts[2])
        closed = self.client.get(f'/api/orders/orders/{order_id}/applications/{applications[1]}/mine/').data
        self.assertEqual(closed['code'], 0, closed)
        self.assertFalse(closed['data']['order']['game_nickname'])
        self.assertFalse(closed['data']['order']['game_uid'])
        self.assertFalse(closed['data']['order']['customer']['phone'])
        auth(accounts[1])
        selected = self.client.get(f'/api/orders/orders/{order_id}/').data
        self.assertEqual(selected['code'], 0, selected)
        self.assertEqual(selected['data']['customer']['phone'], '13800000002')
