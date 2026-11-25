"""Disabled stores expose only the two exact public read endpoints."""
from datetime import datetime, timezone as datetime_timezone
from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount, StoreRoutingSnapshot
from club_accounts.tokens import issue_account_tokens

from .closure_clock import CLOCK_SOURCE, ControlClockEvidence
from .context import store_context
from .models import PlatformAccount, PlatformRoleGrant, Store, StoreClosureNotice, StoreRoutingRevision
from .platform_auth import issue_tokens
from .routing_snapshot import lookup
from .tests_hosts import xinan_store


CURRENT_URL = '/api/stores/current/'
CLOSURE_URL = '/api/stores/closure/'
DISABLED_ANNOUNCEMENT = {
    'status': 'DISABLED',
    'announcement_visible': True,
    'clock_status': 'NOT_REQUIRED',
    'announcement': '本店已停用，不再接受新增业务。既有订单、售后与余额返还情况需另行核对；停用不代表已完成退款或结清。',
}


@override_settings(
    PLATFORM_IDENTITY_ENABLED=True,
    LEGACY_FORCE_AUTH_COMPAT=False,
    ALLOWED_HOSTS=['*'],
    ISOLATED_CONTROL_PLANE=True,
    STORE_DATABASE_ALIASES=('store_a', 'store_b'),
    STORE_HOST_BINDINGS={
        'sl.esports.club': 'shenglang',
        'xa.esports.club': 'xinan',
        'testserver': 'shenglang',
    },
)
class DisabledPublicAccessTests(TestCase):
    databases = {'default', 'store_a', 'store_b'}

    def setUp(self):
        self.first = Store.objects.get(slug='shenglang')
        self.first.database_alias = 'store_a'
        self.first.status = Store.Status.ACTIVE
        self.first.initialized_at = timezone.now()
        self.first.save(update_fields=['database_alias', 'status', 'initialized_at', 'updated_at'])
        self.second = xinan_store()

        self.actor = PlatformAccount.objects.create(
            username='disabled-public-super', nickname='停用公开接口测试超管',
            phone='13800008011', is_phone_verified=True,
        )
        PlatformRoleGrant.objects.create(account=self.actor, role='SUPER_ADMIN', scope_slug='*')
        platform_token, _ = issue_tokens(self.actor, 'SMS', self.actor.phone)
        self.platform = APIClient(
            HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + platform_token,
        )

        with store_context(self.second):
            account = ClubAccount.objects.create_account(
                id=807011, username='disabled-public-boss', account_type='BOSS',
                account_no='XA807011', nickname='兴安停用测试老板',
            )
            account.set_password('Disabled-Public-Password-2026!')
            account.save(update_fields=['password'])
            self.boss_access, self.boss_refresh = issue_account_tokens(account)
        self.public = APIClient(HTTP_HOST='xa.esports.club')
        self.neighbor = APIClient(HTTP_HOST='sl.esports.club')
        self.boss = APIClient(
            HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + self.boss_access,
        )

    def disable_second_store(self):
        self.second.refresh_from_db()
        previous = StoreRoutingRevision.objects.filter(store=self.second).values_list(
            'version', flat=True,
        ).first() or 0
        response = self.platform.patch(
            f'/api/admin/platform/store-management/{self.second.pk}',
            {'status': 'DISABLED', 'expected_updated_at': self.second.updated_at.isoformat()},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.second.refresh_from_db()
        self.assertEqual(self.second.status, Store.Status.DISABLED)

        revision = StoreRoutingRevision.objects.get(store=self.second)
        self.assertGreater(revision.version, previous)
        snapshot = StoreRoutingSnapshot.objects.using('store_b').get(pk=1)
        self.assertEqual(snapshot.payload['revision'], revision.version)
        self.assertEqual(snapshot.payload['store']['status'], Store.Status.DISABLED)
        cached = lookup('xinan')
        self.assertIsNotNone(cached)
        self.assertEqual(cached.status, Store.Status.DISABLED)

    def test_only_exact_current_and_closure_get_head_are_public(self):
        before = self.public.get(CURRENT_URL)
        self.assertEqual(before.status_code, 200, before.content)
        before_data = before.data['data'].copy()
        self.assertEqual((before_data['status'], before_data['is_active']), ('ACTIVE', True))
        self.disable_second_store()

        for method in ('get', 'head'):
            with self.subTest(path=CURRENT_URL, method=method):
                response = getattr(self.public, method)(CURRENT_URL)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(response['X-Store-Slug'], 'xinan')
                if method == 'get':
                    self.assertEqual(set(response.data['data']), set(before_data))
                    self.assertEqual(response.data['data'], {
                        **before_data, 'status': 'DISABLED', 'is_active': False,
                    })
            with self.subTest(path=CLOSURE_URL, method=method):
                response = getattr(self.public, method)(CLOSURE_URL)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(response['X-Store-Slug'], 'xinan')
                self.assertEqual(response['Cache-Control'], 'no-store')
                if method == 'get':
                    self.assertEqual(response.data['data'], DISABLED_ANNOUNCEMENT)

        self.first.refresh_from_db()
        self.assertEqual(self.first.status, Store.Status.ACTIVE)
        neighbor_current = self.neighbor.get(CURRENT_URL)
        self.assertEqual(neighbor_current.status_code, 200, neighbor_current.content)
        self.assertEqual(neighbor_current.data['data']['status'], 'ACTIVE')
        neighbor_closure = self.neighbor.get(CLOSURE_URL)
        self.assertEqual(neighbor_closure.status_code, 200, neighbor_closure.content)
        self.assertEqual(neighbor_closure.data['data'], {
            'status': 'NOT_STARTED', 'announcement_visible': False,
        })

        for method, path in (
            ('post', CLOSURE_URL),
            ('get', '/api/stores/current'),
            ('get', '/api/stores/closure'),
            ('get', '/api/stores/closure/history/'),
        ):
            with self.subTest(boundary_method=method, boundary_path=path):
                response = getattr(self.public, method)(path, {}, format='json')
                self.assertEqual(response.status_code, 403, response.content)

        denied_requests = (
            (self.boss, 'get', '/api/users/me/', None),
            (self.public, 'post', '/api/users/password-login/', {
                'audience': 'boss', 'username': 'disabled-public-boss',
                'password': 'Disabled-Public-Password-2026!',
            }),
            (self.public, 'post', '/api/users/refresh/', {'refresh': self.boss_refresh}),
            (self.boss, 'post', '/api/orders/create/', {'service_id': 1}),
            (self.boss, 'post', '/api/wallet/withdraw/', {'amount': 100}),
            (self.boss, 'post', '/api/wallet/reports/', {}),
            (self.boss, 'post', '/api/orders/orders/1/segment-report/', {
                'action': 'open', 'segment_id': 1, 'client_request_id': 'disabled-report-1',
            }),
        )
        for client, method, path, payload in denied_requests:
            with self.subTest(denied_path=path):
                response = getattr(client, method)(path, payload, format='json') if payload is not None \
                    else getattr(client, method)(path)
                self.assertEqual(response.status_code, 403, response.content)

    def test_disabled_notice_ignores_old_notice_clock_and_invalid_bearer(self):
        secret_reason = '内部秘密：合同争议、联系人与待核金额 987654 分'
        clock = ControlClockEvidence(
            datetime(2026, 11, 1, 4, 0, tzinfo=datetime_timezone.utc),
            CLOCK_SOURCE, 'sqlite-test:disabled-public',
        )
        with patch('stores.closure.read_control_clock', return_value=clock):
            started = self.platform.post(
                f'/api/admin/platform/store-management/{self.second.pk}/closure',
                {
                    'reason_category': 'OPERATOR_REQUEST', 'reason': secret_reason,
                    'client_request_id': 'disabled-public-secret-request',
                }, format='json',
            )
        self.assertEqual(started.status_code, 201, started.content)
        self.assertTrue(StoreClosureNotice.objects.filter(store=self.second).exists())
        self.disable_second_store()

        with patch('stores.closure._sample_clock', side_effect=AssertionError('停用公告不得读取控制时钟')), \
             patch('stores.closure.StoreClosureNotice.objects.filter',
                   side_effect=AssertionError('停用公告不得读取旧公示')):
            for authorization in (None, 'Bearer invalid'):
                headers = {} if authorization is None else {'HTTP_AUTHORIZATION': authorization}
                with self.subTest(authorization=authorization):
                    response = APIClient(HTTP_HOST='xa.esports.club').get(CLOSURE_URL, **headers)
                    self.assertEqual(response.status_code, 200, response.content)
                    self.assertEqual(response.data['data'], DISABLED_ANNOUNCEMENT)
                    self.assertEqual(set(response.data['data']), {
                        'status', 'announcement_visible', 'clock_status', 'announcement',
                    })
                    self.assertEqual(response['Cache-Control'], 'no-store')
                    self.assertEqual(response['Pragma'], 'no-cache')
                    public_text = response.content.decode()
                    for secret in (
                        secret_reason, '合同争议', '987654', 'disabled-public-super',
                        'disabled-public-secret-request', 'sqlite-test:disabled-public',
                        'OPERATOR_REQUEST', clock.instant.isoformat(),
                    ):
                        self.assertNotIn(secret, public_text)

    def test_disabled_public_reads_keep_routing_error_contracts(self):
        self.disable_second_store()
        for path in (CURRENT_URL, CLOSURE_URL):
            for suffix, headers in (
                ('?store=shenglang', {}),
                ('', {'HTTP_X_STORE_SLUG': 'shenglang'}),
                ('?store=xinan&store=shenglang', {}),
            ):
                with self.subTest(path=path, selector=suffix, headers=headers):
                    response = self.public.get(path + suffix, **headers)
                    self.assertEqual(response.status_code, 400, response.content)

            with self.subTest(path=path, unknown_host=True):
                response = APIClient(HTTP_HOST='unknown.esports.club').get(
                    path + '?store=xinan', HTTP_X_STORE_SLUG='xinan',
                )
                self.assertEqual(response.status_code, 404, response.content)
                self.assertNotIn('X-Store-Slug', response)

            with self.subTest(path=path, directory_outage=True), patch(
                'stores.middleware.resolve_store', side_effect=DatabaseError('simulated directory outage'),
            ):
                response = self.public.get(path)
                self.assertEqual(response.status_code, 503, response.content)
                self.assertEqual(response.json()['errorCode'], 'STORE_DIRECTORY_UNAVAILABLE')

        self.assertEqual(self.public.get(CURRENT_URL + '?store=xinan').status_code, 200)
        self.assertEqual(self.public.get(CLOSURE_URL, HTTP_X_STORE_SLUG='xinan').status_code, 200)
