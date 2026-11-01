"""Order game details are read from the purchased order, not a live profile."""
from django.test import override_settings
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_service, make_user, make_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class AdminOrderGameDetailsTest(APITestCase):
    def setUp(self):
        self.boss = make_user()
        self.boss_account = get_or_create_account_for_legacy_user(self.boss)
        make_wallet(self.boss, balance=50000)
        self.service = make_service(price=10000, billing_provider_count=1)
        self.staff = make_console_user(['order:view'])
        self.staff_account = get_or_create_account_for_legacy_user(self.staff)
        self.staff.admin_membership.account = self.staff_account
        self.staff.admin_membership.save(update_fields=['account'])

    def auth(self, account):
        token, _ = issue_account_tokens(account)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_real_purchase_returns_game_details_only_on_authorized_detail(self):
        self.auth(self.boss_account)
        created = self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'client_request_id': 'game-details',
            'game_region': '隔离微信区', 'game_nickname': '隔离老板昵称', 'game_uid': 'QA-000123',
        }, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order_id = created.data['data']['id']
        self.auth(self.staff_account)
        detail = self.client.get(f'/api/admin/orders/{order_id}/')
        self.assertEqual(detail.data['code'], 0, detail.data)
        self.assertEqual([detail.data['data'][key] for key in ('game_region', 'game_nickname', 'game_uid')],
                         ['隔离微信区', '隔离老板昵称', 'QA-000123'])
        rows = self.client.get('/api/admin/orders/').data['data']['list']
        row = next(item for item in rows if item['id'] == order_id)
        for field in ('game_region', 'game_nickname', 'game_uid'):
            self.assertNotIn(field, row)
        self.staff.admin_membership.roles.clear()
        self.assertEqual(self.client.get(f'/api/admin/orders/{order_id}/').status_code, 403)

    def test_missing_game_fields_remain_empty(self):
        self.auth(self.boss_account)
        created = self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'client_request_id': 'empty-game-details', 'game_region': '隔离区服',
        }, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        self.auth(self.staff_account)
        detail = self.client.get(f"/api/admin/orders/{created.data['data']['id']}/").data['data']
        self.assertEqual((detail['game_region'], detail['game_nickname'], detail['game_uid']), ('隔离区服', '', ''))
