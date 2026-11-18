from django.db import DatabaseError, connections, transaction
from django.core.management import call_command
from django.core.management.base import CommandError
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from club_accounts.models import ClubAccount
from club_accounts.tokens import account_for_claims, issue_account_tokens
from stores.context import StoreUnavailable, resolve_store, store_context
from stores.database_identity import lock_store_registration
from stores.models import PlatformAccount, Store, StoreRegistryLock
from stores.platform_auth import issue_tokens, start_challenge, verify_challenge


class StoreSocketOutageTests(SimpleTestCase):
    async def test_directory_failure_closes_with_retryable_status(self):
        from club_accounts.channels_middleware import ClubAccountTokenAuthMiddleware
        downstream, send = AsyncMock(), AsyncMock()
        scope = {'type': 'websocket', 'path': '/ws/orders/', 'query_string': b'token=temporary',
                 'headers': [(b'host', b'xa.esports.club')], 'server': ('xa.esports.club', 80)}
        with patch('club_accounts.channels_middleware.get_account_context',
                   new=AsyncMock(side_effect=DatabaseError('simulated directory outage'))):
            await ClubAccountTokenAuthMiddleware(downstream)(scope, AsyncMock(), send)
        send.assert_awaited_once_with({'type': 'websocket.close', 'code': 1013})
        downstream.assert_not_awaited()


class StoreRoutingSnapshotTests(TransactionTestCase):
    databases = {'default', 'store_a', 'store_b'}

    def test_signed_route_is_store_and_database_bound_and_cannot_undo_disable(self):
        from django.conf import settings
        from club_accounts.models import StoreRoutingSnapshot
        from stores.routing_snapshot import capture, lookup, publish
        store = Store.objects.update_or_create(slug='shenglang', defaults=dict(name='声浪电竞', currency_name='浪花',
            database_alias='store_a', status='ACTIVE', initialized_at=timezone.now()))[0]
        resolve_store('shenglang')
        with store_context(store):
            row = StoreRoutingSnapshot.objects.get(pk=1)
            original = {'payload': row.payload, 'signature': row.signature}
            old_payload = capture(store, 0)
        StoreRoutingSnapshot.objects.using('store_b').update_or_create(pk=1, defaults=original)
        self.assertEqual(lookup('shenglang').database_alias, 'store_a')
        with override_settings(SECRET_KEY='new-routing-secret', SECRET_KEY_FALLBACKS=[settings.SECRET_KEY]):
            self.assertIsNotNone(lookup('shenglang'))
        with override_settings(SECRET_KEY='new-routing-secret', SECRET_KEY_FALLBACKS=[]):
            self.assertIsNone(lookup('shenglang'))
        with store_context(store):
            StoreRoutingSnapshot.objects.filter(pk=1).update(payload={**original['payload'], 'revision': 999})
        self.assertIsNone(lookup('shenglang'))
        with store_context(store):
            StoreRoutingSnapshot.objects.filter(pk=1).update(**original)
        store.status = 'DISABLED'
        publish(capture(store, 1))
        publish(old_payload)
        self.assertEqual(lookup('shenglang').status, 'DISABLED')

    @override_settings(LICENSE_BENEFIT_ENFORCEMENT_ENABLED=True, LICENSE_SNAPSHOT_CACHE_ENABLED=True,
                       LICENSE_ACCOUNT_ADMISSION_ENABLED=False, BOSS_FUNDING_SELECTION_ENABLED=True,
                       KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
    def test_order_and_points_use_local_snapshots_when_control_reads_fail(self):
        from engagement.models import LicenseConsumptionPointsDecision, PointLedger
        from orders.models import Order
        from orders.tests.test_service_funding_api import ServiceFundingApiTest
        from stores.license_snapshot import business_status
        from stores.models import StoreLicenseCode, StoreLicenseContract
        store = Store.objects.update_or_create(slug='xinan', defaults=dict(name='兴安电竞', currency_name='星辉',
            database_alias='store_b', status='ACTIVE', initialized_at=timezone.now()))[0]
        actor = PlatformAccount.objects.create(username='snapshot-control-owner', phone='13800001111')
        contract = StoreLicenseContract.objects.create(store=store, amount_due_fen=0, created_by=actor, updated_by=actor)
        now = timezone.now()
        StoreLicenseCode.objects.create(store=store, contract=contract, code_digest='a' * 64, tier='PRO', kind='INITIAL',
            starts_at=now-timedelta(days=1), ends_at=now+timedelta(days=1),
            activated_at=now-timedelta(hours=1), activated_by_id=1, created_by=actor)
        with store_context(store):
            ServiceFundingApiTest.setUp(self)
            self.boss = APIClient(HTTP_HOST='xa.esports.club',
                                  HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
            self.assertEqual(business_status(store)['license_source'], 'ONLINE')
        resolve_store('xinan')
        def fail_control_sql(_execute, _sql, _params, _many, _context):
            raise DatabaseError('simulated control database outage')
        # Deny every query on the control connection, not just known helpers.
        with connections['default'].execute_wrapper(fail_control_sql):
            payload = ServiceFundingApiTest.payload(self, gift_balance_amount=200, compensation_balance_amount=100)
            response = self.boss.post('/api/orders/create/', payload, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            with store_context(store):
                order = Order.objects.get(pk=response.data['data']['id'])
                self.assertTrue(ServiceFundingApiTest.mature(self, order))
                self.assertEqual(PointLedger.objects.get(order=order, kind='CONSUMPTION').amount, 1050)
                self.assertEqual(LicenseConsumptionPointsDecision.objects.get(order=order).license_evidence['license_source'], 'CACHE')


@override_settings(PLATFORM_IDENTITY_ENABLED=True, LICENSE_ACCOUNT_ADMISSION_ENABLED=False)
class IsolatedControlPlaneTests(TransactionTestCase):
    databases = {'default', 'store_a', 'store_b'}

    def test_independent_mode_refuses_a_store_pointing_to_control(self):
        Store.objects.create(slug='invalid-control-store', name='错误配置', database_alias='default',
                             status='ACTIVE', initialized_at=timezone.now())
        with self.assertRaisesMessage(StoreUnavailable, '不能使用平台控制库'):
            resolve_store('invalid-control-store')
        with patch('stores.management.commands.migrate_stores.call_command') as migrate:
            with self.assertRaisesMessage(CommandError, '指向平台控制库'):
                call_command('migrate_stores', slug='invalid-control-store', verbosity=0)
            self.assertEqual(migrate.call_count, 1)  # control migration only; no tenant migration

    def test_independent_schemas_registration_and_domain_bound_accounts(self):
        control = set(connections['default'].introspection.table_names())
        first = set(connections['store_a'].introspection.table_names())
        second = set(connections['store_b'].introspection.table_names())
        self.assertIn('stores_store', control)
        self.assertIn('stores_storeregistrylock', control)
        self.assertNotIn('club_accounts_clubaccount', control)
        for tables in (first, second):
            self.assertIn('club_accounts_clubaccount', tables)
            self.assertNotIn('stores_store', tables)
        self.assertFalse(Store.objects.exists())
        with transaction.atomic(using='default'):
            self.assertIsInstance(lock_store_registration(), StoreRegistryLock)
        with self.assertRaisesMessage(ValueError, '平台控制库不能访问门店业务数据'):
            ClubAccount.objects.count()

        password = 'Isolated-Platform-Password-123!'
        with patch('stores.management.commands.bootstrap_platform_admin.sys.stdin.isatty', return_value=True), \
             patch('stores.management.commands.bootstrap_platform_admin.getpass', side_effect=[password, password]):
            call_command('bootstrap_platform_admin', username='isolated-owner', phone='13800001111')
        platform = PlatformAccount.objects.get(username='isolated-owner')
        codes = []
        with patch('stores.platform_auth.send_verification_code', side_effect=lambda _phone, code: codes.append(code)):
            challenge = start_challenge(platform)
        verified, proof = verify_challenge(platform, challenge['smsChallenge'], codes[0], 'SMS')
        platform_token, _ = issue_tokens(verified, 'SMS', proof)
        platform_client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + platform_token)
        finance = platform_client.post('/api/admin/platform/accounts', {
            'username': 'isolated-finance', 'password': 'Isolated-Finance-Password-123!',
            'nickname': '平台财务', 'phone': '13800002222',
            'grants': [{'role': 'FINANCE', 'scope_slug': '*'}],
        }, format='json')
        self.assertEqual(finance.status_code, 201, finance.data)
        self.assertFalse(Store.objects.exists())
        for slug, name, currency, alias, prefix in (
            ('shenglang', '声浪电竞', '浪花', 'store_a', 'SL'),
            ('xinan', '兴安电竞', '星辉', 'store_b', 'XA'),
        ):
            created = platform_client.post('/api/admin/platform/store-management', {
                'slug': slug, 'name': name, 'currency_name': currency,
                'database_alias': alias, 'entity_prefix': prefix,
            }, format='json')
            self.assertEqual(created.status_code, 201, created.data)
        now = timezone.now()
        Store.objects.filter(slug__in=['shenglang', 'xinan']).update(status='ACTIVE', initialized_at=now)
        sl, xa = Store.objects.get(slug='shenglang'), Store.objects.get(slug='xinan')
        tokens = []
        for store in (sl, xa):
            with store_context(store):
                account = ClubAccount.objects.create(username='same-boss', account_type='BOSS',
                                                     account_no=f'{store.slug}-boss')
                tokens.append(issue_account_tokens(account)[0])
                self.assertEqual(ClubAccount.objects.count(), 1)
                with self.assertRaises(TokenError):
                    issue_account_tokens(account, auth_method='platform')
                unscoped = AccessToken(tokens[-1])
                del unscoped['store_slug']
                with self.assertRaises(TokenError):
                    account_for_claims(unscoped)
        from stores.tests_foundation import admin
        with store_context(xa):
            staff = admin('cached-store-staff')
            staff.phone = '13800003333'
            staff.save(update_fields=['phone'])
            staff_token = issue_account_tokens(staff)[0]
        for host, token in (('sl.esports.club', tokens[0]), ('xa.esports.club', tokens[1])):
            client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token)
            response = client.get('/api/club/points/')
            self.assertEqual(response.status_code, 200, response.data)
        wrong = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + tokens[0])
        self.assertEqual(wrong.get('/api/club/points/').status_code, 401)
        with patch('stores.middleware.resolve_store', side_effect=DatabaseError('simulated directory outage')):
            unavailable = APIClient(HTTP_HOST='xa.esports.club').get('/api/club/points/')
        self.assertEqual((unavailable.status_code, unavailable.json()['errorCode']),
                         (503, 'STORE_DIRECTORY_UNAVAILABLE'))

        # Once the trusted host has populated each local snapshot, a control
        # outage preserves that store's existing authenticated business access.
        with patch('stores.context._fetch_store', side_effect=DatabaseError('simulated directory outage')):
            for host, token in (('sl.esports.club', tokens[0]), ('xa.esports.club', tokens[1])):
                client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token)
                self.assertEqual(client.get('/api/club/points/').status_code, 200)
            self.assertEqual(wrong.get('/api/club/points/').status_code, 401)
            unbound = APIClient(HTTP_HOST='unbound.example', HTTP_X_STORE_SLUG='xinan',
                                HTTP_AUTHORIZATION='Bearer ' + tokens[1])
            self.assertEqual(unbound.get('/api/club/points/').status_code, 404)
            console = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + staff_token)
            self.assertEqual(console.get('/api/admin/auth/profile').status_code, 200)

            from asgiref.sync import async_to_sync
            from channels.layers import get_channel_layer
            from channels.testing import WebsocketCommunicator
            from club_accounts.channels_middleware import ClubAccountTokenAuthMiddleware
            from orders.consumers import OrderConsumer
            async def verify_socket():
                client = WebsocketCommunicator(ClubAccountTokenAuthMiddleware(OrderConsumer.as_asgi()),
                    '/ws/orders/?token=' + tokens[1], headers=[(b'host', b'xa.esports.club')])
                connected, _ = await client.connect()
                self.assertTrue(connected)
                with store_context(xa):
                    await get_channel_layer().group_send('club_bosses', {'type': 'club_feed_update', 'data': {}})
                self.assertEqual((await client.receive_json_from())['type'], 'club_feed_update')
                await client.disconnect()
            async_to_sync(verify_socket)()

        def fail_control_sql(_execute, _sql, _params, _many, _context):
            raise DatabaseError('simulated control database outage')
        codes = []
        with connections['default'].execute_wrapper(fail_control_sql), \
             patch('users.password_login.send_verification_code', side_effect=lambda _phone, code: codes.append(code)):
            client = APIClient(HTTP_HOST='admin.esports.club')
            data = {'username': 'xinan/cached-store-staff', 'password': 'test-password'}
            first = client.post('/api/admin/auth/login', data, format='json')
            self.assertTrue(first.data['data']['requiresSms'], first.data)
            completed = client.post('/api/admin/auth/login', {**data,
                'smsChallenge': first.data['data']['smsChallenge'], 'smsCode': codes[-1]}, format='json')
            self.assertEqual(completed.data['code'], 0, completed.data)
            self.assertEqual(completed.data['data']['store_slug'], 'xinan')

        # A disable cannot commit if its local tombstone cannot be written.
        xa.refresh_from_db()
        disable = {'status': 'DISABLED', 'expected_updated_at': xa.updated_at.isoformat()}
        with patch('stores.routing_snapshot.publish', side_effect=DatabaseError('tenant unavailable')):
            blocked = platform_client.patch(f'/api/admin/platform/store-management/{xa.pk}', disable, format='json')
        self.assertEqual(blocked.status_code, 503, blocked.data)
        xa.refresh_from_db()
        self.assertEqual(xa.status, 'ACTIVE')
        # A tenant tombstone can commit immediately before the directory
        # transaction fails. The next update must advance past that version.
        from stores.models import StoreRoutingRevision
        from stores.routing_snapshot import capture, lookup, publish
        with transaction.atomic(using='default'):
            pending = Store.objects.select_for_update().get(pk=xa.pk)
            pending.status = 'DISABLED'
            pending.save(update_fields=['status', 'updated_at'])
            revision, _ = StoreRoutingRevision.objects.get_or_create(store=pending)
            revision.version = 1
            revision.save(update_fields=['version'])
            publish(capture(pending, revision.version), require_current=True)
            transaction.set_rollback(True, using='default')
        self.assertEqual(lookup('xinan').status, 'DISABLED')
        xa.refresh_from_db()
        self.assertEqual(xa.status, 'ACTIVE')
        disable['expected_updated_at'] = xa.updated_at.isoformat()
        self.assertEqual(platform_client.patch(f'/api/admin/platform/store-management/{xa.pk}', disable, format='json').status_code, 200)
        self.assertEqual(StoreRoutingRevision.objects.get(store=xa).version, 2)
        with patch('stores.context._fetch_store', side_effect=DatabaseError('simulated directory outage')):
            self.assertEqual(APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + tokens[1]).get('/api/club/points/').status_code, 403)
            self.assertEqual(APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + tokens[0]).get('/api/club/points/').status_code, 200)
        # Restore the active status for the periodic fan-out check below.
        xa.refresh_from_db()
        enabled = platform_client.patch(f'/api/admin/platform/store-management/{xa.pk}',
            {'status': 'ACTIVE', 'expected_updated_at': xa.updated_at.isoformat()}, format='json')
        self.assertEqual(enabled.status_code, 200, enabled.data)

        from config.celery import app
        from stores.tasks import StoreTask
        task = StoreTask()
        task.name = 'isolated.single_order'
        task.bind(app)
        task.run = Mock()
        with patch('celery.app.task.Task.apply_async', return_value=SimpleNamespace(id='isolated-job')) as sender:
            with self.assertRaisesMessage(ValueError, '必须指定所属门店'):
                task.apply_async()
            task.apply_async(headers={'store_slug': 'xinan'})
            self.assertEqual(sender.call_args.kwargs['headers']['store_slug'], 'xinan')
        task.run.return_value = 'tenant job complete'
        task.push_request(headers={'store_slug': 'xinan'}, called_directly=False)
        try:
            with patch('stores.context._fetch_store', side_effect=DatabaseError('simulated directory outage')):
                self.assertEqual(task(), 'tenant job complete')
            task.run.assert_called_once()
            task.run.reset_mock()
        finally:
            task.pop_request()
        task.push_request(headers={}, called_directly=False)
        try:
            with self.assertRaisesMessage(ValueError, '必须指定所属门店'):
                task()
            task.run.assert_not_called()
            task.name = 'orders.tasks.expire_reservations'
            task.apply_async = Mock(return_value=SimpleNamespace(id='periodic-job'))
            result = task()
            self.assertEqual({item['store'] for item in result['stores']}, {'shenglang', 'xinan'})
            with patch('stores.context.active_stores', side_effect=DatabaseError('simulated directory outage')):
                offline_result = task()
            self.assertEqual({item['store'] for item in offline_result['stores']}, {'shenglang', 'xinan'})
            self.assertTrue(offline_result['directory_fallback'])
            task.run.assert_not_called()
        finally:
            task.pop_request()
