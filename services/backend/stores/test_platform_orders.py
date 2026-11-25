from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError, connections
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from orders.direct_payment_models import DirectPaymentIntent
from orders.models import AfterSale, Order, OrderProvider, OrderStatusLog
from orders.tests.factories import make_provider, make_service, make_service_category
from wallet.models import Transaction, Wallet

from .context import resolve_store, store_context
from .display import provider_display_name
from .models import PlatformAccount, PlatformAuditEvent, PlatformRoleGrant
from .platform_auth import issue_tokens
from .tests_hosts import xinan_store


@override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False, PLATFORM_IDENTITY_ENABLED=True)
class PlatformOrdersTests(TestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.first = resolve_store()
        if not self.first.initialized_at:
            self.first.initialized_at = timezone.now()
            self.first.save(update_fields=['initialized_at'])
        self.second = xinan_store()
        self.client = APIClient(HTTP_HOST='admin.esports.club')
        self.base_time = timezone.now() - timedelta(days=1)
        self.first_order = self._make_order(self.first, 41001, created_at=self.base_time)
        self.second_order = self._make_order(self.second, 41001, created_at=self.base_time)
        self.account, self.grant, self.token = self._platform_actor('SUPER_ADMIN', '*')
        self.headers = {'HTTP_AUTHORIZATION': 'Bearer ' + self.token}

    def _platform_actor(self, role, scope):
        username = f'platform-{role.lower()}-{PlatformAccount.objects.count() + 1}'
        account = PlatformAccount(username=username, phone='13800009999', is_phone_verified=True)
        account.set_password('test-password')
        account.save()
        grant = PlatformRoleGrant.objects.create(account=account, role=role, scope_slug=scope)
        token, _ = issue_tokens(account, 'SMS', account.phone)
        return account, grant, token

    def _make_order(self, store, order_id, *, source='BOSS_H5', status=Order.Status.PENDING,
                    payment_status=Order.PaymentStatus.PAID, created_at=None, with_log=True,
                    customer_name=None, order_no=None):
        with store_context(store):
            customer = ClubAccount.objects.create_account(
                username=f'boss-{store.slug}-{order_id}', account_type=ClubAccount.AccountType.BOSS,
                nickname=customer_name or f'{store.name}老板-{order_id}',
            )
            provider_user = make_provider(display_name=f'陪玩-{store.slug}-{order_id}')
            provider_account = ClubAccount.objects.create_account(
                username=f'provider-{store.slug}-{order_id}', account_type=ClubAccount.AccountType.PROVIDER,
                nickname=provider_user.nickname,
            )
            category = make_service_category(f'礼物-{store.slug}-{order_id}', is_gift=True) if source == 'GIFT' else None
            service = make_service(f'服务-{store.slug}-{order_id}', service_category=category)
            order = Order.objects.create(
                id=order_id, order_no=order_no or f'PO-{store.slug}-{order_id}',
                customer_account=customer, service=service, amount=10000,
                provider=provider_user, provider_account=provider_account,
                provider_name_snapshot=provider_user.nickname, service_name_snapshot=service.name,
                status=status, payment_status=payment_status,
                # Deliberately contradict source: dispatch_route is not creation source.
                dispatch_route=Order.DispatchRoute.STAFF if source == 'BOSS_H5' else Order.DispatchRoute.PUBLIC,
                remark='INTERNAL_REMARK', cancel_reason='INTERNAL_CANCEL_REASON',
                customer_phone_snapshot='13900000000', game_uid='INTERNAL_GAME_UID',
            )
            if with_log:
                creator = customer
                reason = '下单'
                if source == 'STAFF_MANUAL':
                    creator = ClubAccount.objects.create_account(
                        username=f'staff-{store.slug}-{order_id}', account_type=ClubAccount.AccountType.STAFF,
                        nickname='客服甲',
                    )
                    reason = '客服代派单'
                elif source == 'RESERVATION':
                    reason = '预约转单：RES-1'
                OrderStatusLog.objects.create(
                    order=order, action=OrderStatusLog.Action.CREATE, operator_account=creator,
                    reason=reason + ' INTERNAL_REMARK',
                )
                if status != Order.Status.PENDING:
                    OrderStatusLog.objects.create(
                        order=order, action=OrderStatusLog.Action.GRAB, operator_account=provider_account,
                        reason='INTERNAL_REMARK',
                    )
            if created_at is not None:
                Order.objects.filter(pk=order.pk).update(created_at=created_at)
            return order

    def _list(self, query='', headers=None):
        response = self.client.get('/api/admin/platform/orders' + query, **(self.headers if headers is None else headers))
        self.assertEqual(response['Cache-Control'], 'no-store', (response.status_code, getattr(response, 'data', response.content)))
        return response

    def _detail(self, slug, order_id, headers=None):
        response = self.client.get(
            f'/api/admin/platform/orders/{slug}/{order_id}', **(self.headers if headers is None else headers),
        )
        self.assertEqual(response['Cache-Control'], 'no-store', (response.status_code, getattr(response, 'data', response.content)))
        return response

    def _snapshot(self):
        result = {}
        for store in (self.first, self.second):
            with store_context(store):
                result[store.slug] = {
                    'orders': list(Order.objects.order_by('pk').values(
                        'id', 'status', 'payment_status', 'amount', 'remark', 'cancel_reason', 'updated_at',
                    )),
                    'logs': OrderStatusLog.objects.count(),
                    'transactions': Transaction.objects.count(),
                    'wallets': list(Wallet.objects.order_by('pk').values('id', 'balance', 'frozen_amount')),
                }
        result['audit'] = PlatformAuditEvent.objects.count()
        return result

    def test_unauthenticated_and_profile_capability_are_explicit(self):
        response = self._list(headers={})
        self.assertEqual(response.status_code, 401)
        profile = self.client.get('/api/admin/platform/profile', **self.headers)
        self.assertEqual(profile.status_code, 200, profile.data)
        capabilities = profile.data['data']['capabilities']
        self.assertIn('PLATFORM_ORDERS_V1', capabilities)
        self.assertNotIn('PLATFORM_DISPATCH_V1', capabilities)

    def test_super_admin_sees_both_stores_without_crossing_equal_numeric_ids(self):
        response = self._list()
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data['data']
        self.assertEqual(data['total'], 2)
        self.assertTrue(data['complete'])
        self.assertEqual(data['missing_stores'], [])
        self.assertEqual([(row['store_slug'], row['id']) for row in data['results']], [
            ('shenglang', '41001'), ('xinan', '41001'),
        ])
        self.assertEqual([row['customer_name'] for row in data['results']], [
            '声浪电竞老板-41001', '兴安电竞老板-41001',
        ])
        self.assertTrue(all(row['allowed_actions'] == [] for row in data['results']))
        self.assertTrue(all(row['order_source'] == 'BOSS_H5' for row in data['results']))
        self.assertTrue(all(row['dispatch_route'] == 'STAFF' for row in data['results']))
        self.assertEqual(data['results'][0]['currency_name'], self.first.currency_name)
        self.assertEqual(data['results'][0]['service_name'], '服务-shenglang-41001')
        with store_context(self.first):
            expected_provider = provider_display_name(self.first_order.provider_name_snapshot)
        self.assertEqual(data['results'][0]['provider_names'], [expected_provider])

    def test_detail_binds_slug_and_numeric_id_and_redacts_events(self):
        first = self._detail(self.first.slug, self.first_order.pk)
        second = self._detail(self.second.slug, self.second_order.pk)
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(first.data['data']['store_slug'], self.first.slug)
        self.assertEqual(second.data['data']['store_slug'], self.second.slug)
        self.assertNotEqual(first.data['data']['customer_name'], second.data['data']['customer_name'])
        event = first.data['data']['events'][0]
        self.assertEqual(event['title'], '创建')
        self.assertNotIn('reason', event)
        self.assertNotIn('remark', event)
        serialized = first.content.decode()
        for secret in ('INTERNAL_REMARK', 'INTERNAL_CANCEL_REASON', 'INTERNAL_GAME_UID', '13900000000', '"amount"'):
            self.assertNotIn(secret, serialized)
        self.assertEqual(self._detail(self.first.slug, 999999).status_code, 404)
        self.assertEqual(self._detail('missing-store', self.first_order.pk).status_code, 404)
        self.assertEqual(self._detail(self.first.slug, 'not-a-number').status_code, 404)

    def test_provider_names_and_aftersale_projection_use_only_safe_fields(self):
        with store_context(self.first):
            second_provider = make_provider(display_name='第二陪玩')
            second_account = ClubAccount.objects.create_account(
                username='second-provider', account_type=ClubAccount.AccountType.PROVIDER,
                nickname='第二陪玩',
            )
            OrderProvider.objects.create(
                order=self.first_order, provider=second_provider, provider_account=second_account,
                provider_name_snapshot='第二陪玩', settlement_base=5000, provider_income=4000,
            )
            AfterSale.objects.create(
                order=self.first_order, account=self.first_order.customer_account,
                reason='INTERNAL_AFTERSALE_REASON', resolution='INTERNAL_AFTERSALE_RESOLUTION',
                status='PARTIALLY_REFUNDED',
            )
        response = self._detail(self.first.slug, self.first_order.pk)
        self.assertEqual(response.status_code, 200, response.data)
        row = response.data['data']
        self.assertEqual(row['aftersale_status'], 'PARTIALLY_REFUNDED')
        with store_context(self.first):
            expected_names = {
                provider_display_name(self.first_order.provider_name_snapshot),
                provider_display_name('第二陪玩'),
            }
        self.assertEqual(set(row['provider_names']), expected_names)
        self.assertNotIn('INTERNAL_AFTERSALE_REASON', response.content.decode())
        self.assertNotIn('INTERNAL_AFTERSALE_RESOLUTION', response.content.decode())

    def test_order_viewer_and_operator_roles_are_read_only(self):
        for role in (PlatformRoleGrant.Role.ORDER_VIEWER, PlatformRoleGrant.Role.ORDER_OPERATOR):
            with self.subTest(role=role):
                _, _, token = self._platform_actor(role, '*')
                headers = {'HTTP_AUTHORIZATION': 'Bearer ' + token}
                response = self._list(headers=headers)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data['data']['total'], 2)
                self.assertTrue(all(row['allowed_actions'] == [] for row in response.data['data']['results']))
                self.assertEqual(self._detail(self.second.slug, self.second_order.pk, headers).status_code, 200)

    def test_scoped_viewer_cannot_read_another_store_even_with_same_id(self):
        _, _, token = self._platform_actor(PlatformRoleGrant.Role.ORDER_VIEWER, self.first.slug)
        headers = {'HTTP_AUTHORIZATION': 'Bearer ' + token}
        response = self._list(headers=headers)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row['store_slug'] for row in response.data['data']['results']], [self.first.slug])
        directory = self.client.get('/api/admin/platform/stores', **headers)
        self.assertEqual(directory.status_code, 200, directory.data)
        self.assertEqual([row['slug'] for row in directory.data['data']['results']], [self.first.slug])
        self.assertEqual(self._list('?store=xinan', headers).status_code, 404)
        self.assertEqual(self._detail(self.second.slug, self.first_order.pk, headers).status_code, 404)

    def test_account_without_order_permission_gets_403(self):
        _, _, token = self._platform_actor(PlatformRoleGrant.Role.FINANCE, '*')
        headers = {'HTTP_AUTHORIZATION': 'Bearer ' + token}
        self.assertEqual(self._list(headers=headers).status_code, 403)
        self.assertEqual(self._detail(self.first.slug, self.first_order.pk, headers).status_code, 403)
        wrong_host = APIClient(HTTP_HOST='xa.esports.club')
        self.assertEqual(wrong_host.get('/api/admin/platform/orders', **self.headers).status_code, 403)

    def test_global_pagination_is_stable_and_total_is_accurate(self):
        self._make_order(self.first, 41003, created_at=self.base_time + timedelta(minutes=1))
        self._make_order(self.second, 41002, created_at=self.base_time + timedelta(minutes=1))
        first_page = self._list('?page=1&page_size=2')
        second_page = self._list('?page=2&page_size=2')
        self.assertEqual(first_page.status_code, 200, first_page.data)
        self.assertEqual(second_page.status_code, 200, second_page.data)
        self.assertEqual(first_page.data['data']['total'], 4)
        self.assertEqual([(row['store_slug'], row['id']) for row in first_page.data['data']['results']], [
            ('shenglang', '41003'), ('xinan', '41002'),
        ])
        self.assertEqual([(row['store_slug'], row['id']) for row in second_page.data['data']['results']], [
            ('shenglang', '41001'), ('xinan', '41001'),
        ])
        self.assertEqual(self._list('?page=3&page_size=2').status_code, 400)

    def test_filters_use_creation_facts_and_customer_display_name(self):
        self._make_order(
            self.first, 41002, source='STAFF_MANUAL', status=Order.Status.COMPLETED,
            payment_status=Order.PaymentStatus.REFUNDED, customer_name='筛选老板',
            order_no='PO-FILTER-TARGET',
        )
        query = '?store=shenglang&source=STAFF_MANUAL&status=COMPLETED&payment=REFUNDED&keyword=筛选'
        response = self._list(query)
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data['data']
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['results'][0]['order_no'], 'PO-FILTER-TARGET')
        self.assertEqual(data['results'][0]['order_source'], 'STAFF_MANUAL')
        self.assertEqual(data['results'][0]['dispatch_route'], 'PUBLIC')
        self.assertEqual(self._list('?source=GIFT').data['data']['total'], 0)

    def test_reservation_gift_and_unknown_sources_do_not_guess(self):
        self._make_order(self.first, 41002, source='RESERVATION')
        self._make_order(self.first, 41003, source='GIFT')
        self._make_order(self.first, 41004, with_log=False)
        rows = self._list('?store=shenglang&page_size=10').data['data']['results']
        sources = {row['id']: row['order_source'] for row in rows}
        self.assertEqual(sources['41002'], 'RESERVATION')
        self.assertEqual(sources['41003'], 'GIFT')
        self.assertIsNone(sources['41004'])
        self.assertEqual(self._list('?source=RESERVATION').data['data']['total'], 1)

    def test_query_validation_rejects_duplicates_enums_and_out_of_range_values(self):
        invalid = [
            '?page=1&page=2', '?page=0', '?page=abc', '?page=100001', '?page=' + '9' * 21,
            '?page_size=0', '?page_size=101', '?page_size=1.5',
            '?source=UNKNOWN', '?status=DONE', '?payment=SUCCESS',
            '?store=', '?keyword=', '?keyword=' + 'x' * 101, '?unexpected=1',
            '?page=1002&page_size=100',
        ]
        for query in invalid:
            with self.subTest(query=query):
                self.assertEqual(self._list(query).status_code, 400)

    def test_single_store_failure_returns_partial_and_only_authorized_missing_stores(self):
        def unavailable(execute, sql, params, many, context):
            raise DatabaseError('test unavailable tenant database')

        with connections[self.second.database_alias].execute_wrapper(unavailable):
            response = self._list()
            unavailable_detail = self._detail(self.second.slug, self.second_order.pk)
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data['data']
        self.assertFalse(data['complete'])
        self.assertEqual(data['missing_stores'], ['xinan'])
        self.assertEqual(data['total'], 1)
        self.assertEqual([row['store_slug'] for row in data['results']], ['shenglang'])
        self.assertEqual(unavailable_detail.status_code, 503)

        _, _, token = self._platform_actor(PlatformRoleGrant.Role.ORDER_VIEWER, self.first.slug)
        headers = {'HTTP_AUTHORIZATION': 'Bearer ' + token}
        with connections[self.second.database_alias].execute_wrapper(unavailable):
            scoped = self._list(headers=headers)
        self.assertEqual(scoped.status_code, 200, scoped.data)
        self.assertTrue(scoped.data['data']['complete'])
        self.assertEqual(scoped.data['data']['missing_stores'], [])

    def test_failure_while_loading_page_also_isolates_only_that_store(self):
        from .platform_orders import StoreReadFailure, _load_store_page
        original = _load_store_page

        def fail_second_store(store, order_ids):
            if store.slug == self.second.slug:
                raise StoreReadFailure(store)
            return original(store, order_ids)

        with patch('stores.platform_orders._load_store_page', side_effect=fail_second_store):
            response = self._list()
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data['data']
        self.assertEqual(data['total'], 1)
        self.assertFalse(data['complete'])
        self.assertEqual(data['missing_stores'], ['xinan'])
        self.assertEqual([row['store_slug'] for row in data['results']], ['shenglang'])

    def test_store_failure_keeps_partial_response_when_original_page_becomes_out_of_range(self):
        self._make_order(self.first, 41003, created_at=self.base_time + timedelta(minutes=1))
        self._make_order(self.second, 41002, created_at=self.base_time + timedelta(minutes=1))
        from .platform_orders import StoreReadFailure, _load_store_page
        original = _load_store_page

        def fail_second_store(store, order_ids):
            if store.slug == self.second.slug:
                raise StoreReadFailure(store)
            return original(store, order_ids)

        with patch('stores.platform_orders._load_store_page', side_effect=fail_second_store):
            response = self._list('?page=4&page_size=1')
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data['data']
        self.assertFalse(data['complete'])
        self.assertEqual(data['missing_stores'], ['xinan'])
        self.assertEqual(data['total'], 2)
        self.assertEqual(data['results'], [])

    def test_permission_is_rechecked_after_tenant_read(self):
        PlatformRoleGrant.objects.create(account=self.account, role=PlatformRoleGrant.Role.FINANCE, scope_slug='*')
        from .platform_orders import _count_store
        original = _count_store

        def revoke_after_read(store, query):
            count = original(store, query)
            self.grant.is_active = False
            self.grant.save(update_fields=['is_active'])
            return count

        with patch('stores.platform_orders._count_store', side_effect=revoke_after_read):
            response = self._list()
        self.assertEqual(response.status_code, 403, response.data)
        self.assertNotIn('data', response.data)
        self.assertEqual(self._list().status_code, 403)

    def test_get_and_detail_never_write_order_money_or_audit_records(self):
        before = self._snapshot()
        self.assertEqual(self._list().status_code, 200)
        self.assertEqual(self._detail(self.first.slug, self.first_order.pk).status_code, 200)
        self.assertEqual(self._snapshot(), before)
        for method in ('post', 'put', 'patch', 'delete'):
            with self.subTest(method=method):
                response = getattr(self.client, method)('/api/admin/platform/orders', **self.headers)
                self.assertEqual(response.status_code, 405)
                response = getattr(self.client, method)(
                    f'/api/admin/platform/orders/{self.first.slug}/{self.first_order.pk}', **self.headers,
                )
                self.assertEqual(response.status_code, 405)
        self.assertEqual(self._snapshot(), before)

    def test_direct_payment_display_exposes_no_channel_or_amount_fields(self):
        with store_context(self.first):
            intent = DirectPaymentIntent.objects.create(
                order=self.first_order, account=self.first_order.customer_account,
                client_request_id='platform-order-projection', request_fingerprint='f' * 64,
                merchant_order_no='SECRET-MERCHANT', appid='SECRET-APP', mchid='SECRET-MCH',
                openid='SECRET-OPENID', amount_fen=10000,
            )
        response = self._detail(self.first.slug, self.first_order.pk)
        self.assertEqual(response.status_code, 200, response.data)
        row = response.data['data']
        self.assertEqual(row['payment_display'], {'kind': 'DIRECT_TEST'})
        self.assertEqual(row['allowed_actions'], [])
        serialized = response.content.decode()
        for secret in (intent.merchant_order_no, intent.openid, intent.appid, intent.mchid, 'amount_fen'):
            self.assertNotIn(secret, serialized)
