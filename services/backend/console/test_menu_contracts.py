"""真实业务 JWT 下的菜单访问、筛选和跨菜单数据一致性。"""
from django.test import override_settings
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.permissions import ALL_PERMISSIONS
from console.urls import router
from orders.models import Order, OrderProvider
from orders.tests.factories import make_console_user, make_order, make_provider, make_user, make_service
from django.utils import timezone


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class MenuContractTest(APITestCase):
    def setUp(self):
        self.staff = make_console_user(ALL_PERMISSIONS)
        self.boss = make_user(nickname='筛选老板')
        self.provider = make_provider()
        self.accounts = {}
        for user in (self.staff, self.boss, self.provider):
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            self.accounts[user.pk] = account
        membership = self.staff.admin_membership
        membership.account = self.accounts[self.staff.pk]
        membership.save(update_fields=['account'])
        profile = self.provider.escort_profile
        profile.account = self.accounts[self.provider.pk]
        profile.save(update_fields=['account'])
        self.role = self.staff.admin_membership.roles.get()
        token, _ = issue_account_tokens(self.accounts[self.staff.pk])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def set_permissions(self, permissions):
        self.role.permissions = permissions
        self.role.save(update_fields=['permissions'])

    def test_each_list_endpoint_works_with_its_own_menu_permission(self):
        for prefix, viewset, _ in router.registry:
            with self.subTest(menu=prefix):
                self.set_permissions([viewset.default_perm])
                response = self.client.get(f'/api/admin/{prefix}/')
                self.assertEqual(response.status_code, 200, getattr(response, 'data', ''))
                self.assertEqual(response.data['code'], 0, response.data)
                self.assertIn('list', response.data['data'])
                self.set_permissions([])
                self.assertEqual(self.client.get(f'/api/admin/{prefix}/').status_code, 403)

    def test_platform_overview_requires_its_menu_permission(self):
        self.set_permissions(['order:view'])
        self.assertEqual(self.client.get('/api/admin/dashboard/stats').status_code, 403)
        self.set_permissions(['dashboard:view'])
        self.assertEqual(self.client.get('/api/admin/dashboard/stats').data['code'], 0)

    def test_configuration_reads_require_their_own_menu_permission(self):
        for endpoint, permission in [('commission', 'report:view'), ('withdraw', 'withdraw:view'), ('checkin', 'checkin:view')]:
            with self.subTest(config=endpoint):
                self.set_permissions(['order:view'])
                self.assertEqual(self.client.get('/api/admin/config/' + endpoint).status_code, 403)
                self.set_permissions([permission])
                self.assertEqual(self.client.get('/api/admin/config/' + endpoint).status_code, 200)

    def test_boss_number_filter_uses_business_account_number(self):
        account = self.accounts[self.boss.pk]
        response = self.client.get('/api/admin/users/', {'boss_no': account.account_no})
        self.assertEqual(response.data['code'], 0)
        self.assertEqual([row['id'] for row in response.data['data']['list']], [account.pk])

    def test_wallet_and_recharge_use_same_boss_number_as_user_menu(self):
        from wallet.models import RechargeRecord
        from wallet.services import get_wallet
        account = self.accounts[self.boss.pk]
        self.boss.boss_no = 'LEGACY-DIFFERENT'
        self.boss.save(update_fields=['boss_no'])
        get_wallet(account=account)
        RechargeRecord.objects.create(user=self.boss, account=account, amount=1000)
        for menu in ['wallets', 'recharge-records']:
            data = self.client.get(f'/api/admin/{menu}/', {'boss_no': account.account_no}).data['data']
            self.assertEqual(data['total'], 1)
            self.assertEqual(data['list'][0]['boss_no'], account.account_no)

    def test_platform_turnover_excludes_unpaid_and_refunded_orders(self):
        make_order(self.boss, amount=8000, payment_status='PAID')
        make_order(self.boss, amount=10000, payment_status='UNPAID')
        make_order(self.boss, amount=3000, payment_status='REFUNDED')
        data = self.client.get('/api/admin/dashboard/stats').data['data']
        self.assertEqual(data['order_amount_total'], 8000)
        self.assertEqual(data['paid_order_total'], 1)
        self.assertEqual(data['order_total'], 3)

    def test_boss_profile_edits_are_visible_in_wallet_search(self):
        account = self.accounts[self.boss.pk]
        response = self.client.patch(f'/api/admin/users/{account.pk}/', {
            'nickname': '更新后的老板', 'phone': '13900001234',
        }, format='json')
        self.assertEqual(response.data['code'], 0)
        response = self.client.get('/api/admin/wallets/', {'nickname': '更新后的老板', 'phone': '13900001234'})
        self.assertEqual(response.data['data']['total'], 1)
        self.assertEqual(response.data['data']['list'][0]['nickname'], '更新后的老板')

    def test_player_overview_attributes_double_and_transfer_shares(self):
        other = make_provider(gender='FEMALE', display_name='次席')
        other_account = get_or_create_account_for_legacy_user(other)
        profile = other.escort_profile
        profile.account = other_account
        profile.save(update_fields=['account'])
        profile = self.provider.escort_profile
        profile.gender = 'MALE'
        profile.save(update_fields=['gender'])
        order = make_order(self.boss, provider=other, customer_account=self.accounts[self.boss.pk],
                           provider_account=other_account, escort_mode='DOUBLE', amount=8000,
                           provider_income=6400, payment_status='PAID', status='COMPLETED')
        OrderProvider.objects.create(order=order, provider=self.provider, provider_account=self.accounts[self.provider.pk],
                                     settlement_base=3000, provider_income=2400, released_at=timezone.now())
        OrderProvider.objects.create(order=order, provider=other, provider_account=other_account,
                                     settlement_base=5000, provider_income=4000)
        result = self.client.get('/api/admin/dashboard/player').data['data']
        self.assertEqual(result['kpi']['total_amount'], 8000)
        self.assertEqual(result['kpi']['male_game'], 3000)
        self.assertEqual(result['kpi']['female_game'], 5000)
        self.assertEqual(sorted(row['value'] for row in result['rank']['income']), [2400, 4000])
        self.assertEqual([row['value'] for row in result['rank']['count']], [1, 1])
        Order.objects.filter(pk=order.pk).update(payment_status='REFUNDED')
        self.assertEqual(self.client.get('/api/admin/dashboard/player').data['data']['rank']['income'], [])

    def test_player_overview_rejects_invalid_or_reversed_date_range(self):
        for params in ({'start_date': 'invalid'}, {'start_date': '2026-02-30'},
                       {'start_date': '2026-09-10', 'end_date': '2026-09-09'}):
            with self.subTest(params=params):
                self.assertEqual(self.client.get('/api/admin/dashboard/player', params).status_code, 400)

    def test_ban_history_is_available_in_each_account_menu(self):
        for resource, object_id, permission in (
            ('users', self.accounts[self.boss.pk].pk, 'user:view'),
            ('escorts', self.provider.escort_profile.pk, 'escort:view'),
        ):
            with self.subTest(resource=resource):
                self.set_permissions([permission, 'user:ban'])
                response = self.client.post(f'/api/admin/{resource}/{object_id}/ban/', {'reason': '菜单封禁验收'})
                self.assertEqual(response.data['code'], 0)
                self.set_permissions([permission])
                response = self.client.get(f'/api/admin/{resource}/{object_id}/ban-history/')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data['data']['list'][0]['reason'], '菜单封禁验收')
                self.assertEqual(self.client.post(f'/api/admin/{resource}/{object_id}/unban/').status_code, 403)
                self.set_permissions([permission, 'user:ban'])
                response = self.client.post(f'/api/admin/{resource}/{object_id}/unban/', {'reason': '验收结束'})
                self.assertEqual(response.data['code'], 0)
                response = self.client.get(f'/api/admin/{resource}/{object_id}/ban-history/')
                self.assertEqual(response.data['data']['list'][0]['status'], 'LIFTED')

    def test_dispatch_options_work_without_user_or_service_management_access(self):
        make_service(name='派单服务')
        self.set_permissions(['order:view', 'order:dispatch'])
        for resource in ('users', 'escorts', 'service-items'):
            with self.subTest(resource=resource):
                self.assertEqual(self.client.get(f'/api/admin/{resource}/').status_code, 403)
                result = self.client.get(f'/api/admin/options/{resource}/')
                self.assertEqual(result.data['code'], 0)
        account = self.accounts[self.boss.pk]
        result = self.client.get('/api/admin/options/users/', {'keyword': account.account_no})
        self.assertEqual([row['id'] for row in result.data['data']['list']], [account.pk])
        self.assertNotIn('openid', result.data['data']['list'][0])
        self.assertNotIn('phone', result.data['data']['list'][0])
        self.assertEqual(self.client.get('/api/admin/options/users/', {'keyword': '不存在的账号'}).data['data']['total'], 0)
        self.set_permissions(['order:view'])
        self.assertEqual(self.client.get('/api/admin/options/users/').status_code, 403)

    def test_dictionary_options_do_not_require_unrelated_menu_permissions(self):
        cases = {
            'user:edit': ['boss-types'],
            'escort:edit': ['escort-levels', 'game-categories', 'service-items'],
            'service:view': ['escort-levels'],
            'promotion:edit': ['service-categories', 'service-items'],
            'admin:edit': ['roles'],
        }
        for permission, resources in cases.items():
            self.set_permissions([permission])
            for resource in resources:
                with self.subTest(permission=permission, resource=resource):
                    self.assertEqual(self.client.get(f'/api/admin/options/{resource}/').data['code'], 0)
        self.set_permissions([])
        self.assertEqual(self.client.get('/api/admin/options/roles/').status_code, 403)

    def test_service_options_paginate_beyond_first_hundred_and_filter_inactive(self):
        from orders.models import ServiceItem
        ServiceItem.objects.bulk_create([ServiceItem(name=f'项目{i}', price=100, is_active=i != 204) for i in range(205)])
        self.set_permissions(['escort:edit'])
        ids = []
        for page in (1, 2, 3):
            result = self.client.get('/api/admin/options/service-items/', {'page': page, 'page_size': 100, 'is_active': 'true'})
            self.assertEqual(result.data['data']['total'], 204)
            ids.extend(row['id'] for row in result.data['data']['list'])
        self.assertEqual(len(set(ids)), 204)
