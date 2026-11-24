"""Dispatch candidate availability mirrors the real manual order path."""
import os
from unittest import skipUnless
from uuid import uuid4

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from users.models import EscortProfile


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   GRAB_SINGLE_WINDOW_ENABLED=True, GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True,
                   GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True)
class DispatchProviderOptionsWorkflowTest(APITestCase):
    def setUp(self):
        from engagement.models import ClubOperationConfig
        from console.grab_config import save_grab_window
        ClubOperationConfig.objects.get_or_create(pk=1)
        save_grab_window(30, 0, lambda *args: None)
        boss = make_user()
        self.boss = get_or_create_account_for_legacy_user(boss)
        make_wallet(boss, balance=100000)
        staff = make_console_user(['order:dispatch', 'order:view'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        self.admin = self.client_for(staff_account)
        self.service = make_service(price=1000)
        self.providers = []
        for _ in range(3):
            user = make_provider(deposit_mode='EARNINGS')
            account = get_or_create_account_for_legacy_user(user)
            EscortProfile.objects.filter(user=user).update(account=account)
            self.providers.append((user, account))

    @staticmethod
    def client_for(account):
        account.is_phone_verified = True
        fields = ['is_phone_verified']
        if not account.phone:
            account.phone = '13800000000'
            fields.append('phone')
        if account.account_type == 'STAFF':
            account.is_openid_bound = True
            fields.append('is_openid_bound')
        account.save(update_fields=fields)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account, auth_method='password_sms')[0])
        return client

    def assign(self, account):
        quote = self.admin.get('/api/admin/orders/dispatch-quote/', {
            'customer_id': self.boss.pk, 'service_id': self.service.pk, 'game_rounds': 1,
        })
        self.assertEqual(quote.data['code'], 0, quote.data)
        response = self.admin.post('/api/admin/orders/dispatch/', {
            'customer_id': self.boss.pk, 'service_id': self.service.pk, 'game_rounds': 1,
            'escort_mode': 'SINGLE', 'providers': [{'provider_id': account.pk}],
            'expected_amount': quote.data['data']['amount'], 'client_request_id': str(uuid4()),
        }, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data['data']

    def available(self, account):
        response = self.client_for(account).post('/api/users/escorts/status/', {'status': 'AVAILABLE'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)

    @skipUnless(os.environ.get('XA_STAFF_TEAM_ASSIGN_FORMAL') == '1',
                '夹具靠客服指定陪玩派单制造忙碌/满员状态，该旅程已被抢单池取代（18-盘点客服安排抢单）；最终联调启用')
    def test_candidates_exclude_busy_and_full_but_keep_available(self):
        busy = self.providers[0][1]
        capped = self.providers[1][1]
        free = self.providers[2][1]
        self.assign(busy)
        for _ in range(3):
            self.available(capped)
            self.assign(capped)
        self.available(capped)
        response = self.admin.get('/api/admin/options/escorts/', {
            'service_id': self.service.pk, 'page_size': 100,
        })
        self.assertEqual(response.data['code'], 0, response.data)
        ids = {row['user'] for row in response.data['data']['list']}
        self.assertIn(free.pk, ids)
        self.assertNotIn(busy.pk, ids)
        self.assertNotIn(capped.pk, ids)

    @skipUnless(os.environ.get('XA_STAFF_TEAM_ASSIGN_FORMAL') == '1',
                '夹具靠客服指定陪玩派单制造忙碌/满员状态，该旅程已被抢单池取代（18-盘点客服安排抢单）；最终联调启用')
    def test_candidates_match_selected_gender_for_companion_service(self):
        self.service.service_type = 'COMPANION'
        self.service.save(update_fields=['service_type'])
        male, female, _ = self.providers
        EscortProfile.objects.filter(user=male[0]).update(gender='MALE')
        EscortProfile.objects.filter(user=female[0]).update(gender='FEMALE')
        response = self.admin.get('/api/admin/options/escorts/', {
            'service_id': self.service.pk, 'provider_gender': 'FEMALE', 'page_size': 100,
        })
        self.assertEqual(response.data['code'], 0, response.data)
        rows = response.data['data']['list']
        self.assertEqual({row['user'] for row in rows}, {female[1].pk})
        self.assertEqual(rows[0]['gender'], 'FEMALE')
        quote = self.admin.get('/api/admin/orders/dispatch-quote/', {
            'customer_id': self.boss.pk, 'service_id': self.service.pk, 'game_rounds': 1,
        })
        self.assertEqual(quote.data['code'], 0, quote.data)
        payload = {'customer_id': self.boss.pk, 'service_id': self.service.pk, 'game_rounds': 1,
                   'escort_mode': 'SINGLE', 'provider_gender': 'FEMALE',
                   'expected_amount': quote.data['data']['amount']}
        rejected = self.admin.post('/api/admin/orders/dispatch/', {
            **payload, 'providers': [{'provider_id': male[1].pk}], 'client_request_id': str(uuid4()),
        }, format='json')
        self.assertNotEqual(rejected.data['code'], 0, rejected.data)
        accepted = self.admin.post('/api/admin/orders/dispatch/', {
            **payload, 'providers': [{'provider_id': female[1].pk}], 'client_request_id': str(uuid4()),
        }, format='json')
        self.assertEqual(accepted.data['code'], 0, accepted.data)

    def test_candidate_queries_do_not_grow_per_provider(self):
        params = {'service_id': self.service.pk, 'page_size': 100}
        with CaptureQueriesContext(connection) as first:
            response = self.admin.get('/api/admin/options/escorts/', params)
            self.assertEqual(response.data['data']['total'], 3)
        for _ in range(17):
            user = make_provider(deposit_mode='EARNINGS')
            account = get_or_create_account_for_legacy_user(user)
            EscortProfile.objects.filter(user=user).update(account=account)
        with CaptureQueriesContext(connection) as expanded:
            response = self.admin.get('/api/admin/options/escorts/', params)
            self.assertEqual(response.data['data']['total'], 20)
        self.assertLessEqual(len(expanded), len(first) + 1, (len(first), len(expanded)))


    @skipUnless(os.environ.get('XA_STAFF_TEAM_ASSIGN_FORMAL') == '1',
                '夹具靠客服指定陪玩派单制造忙碌/满员状态，该旅程已被抢单池取代（18-盘点客服安排抢单）；最终联调启用')
    def test_order_detail_exposes_service_for_candidate_filter(self):
        created = self.assign(self.providers[0][1])
        detail = self.admin.get(f'/api/admin/orders/{created["id"]}/')
        self.assertEqual(detail.data['code'], 0, detail.data)
        self.assertEqual(detail.data['data']['service_item'], self.service.pk)
        options = self.admin.get('/api/admin/options/escorts/', {
            'service_id': detail.data['data']['service_item'], 'page_size': 100,
        })
        self.assertEqual(options.data['code'], 0, options.data)
        ids = {row['user'] for row in options.data['data']['list']}
        self.assertNotIn(self.providers[0][1].pk, ids)
        self.assertIn(self.providers[1][1].pk, ids)
