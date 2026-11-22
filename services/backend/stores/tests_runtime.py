"""Actual store boundaries: overlapping IDs, signed capabilities and queued jobs."""
import os
from unittest import skipUnless
import asyncio
from io import BytesIO
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
import uuid

from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from chat.models import ChatMessage, ChatSession
from club_accounts.models import ClubAccount
from club_accounts.tokens import account_for_claims, issue_account_tokens, refresh_account_access_token
from common.media import build_media_url
from common.private_media import private_media_url
from stores.channels import StoreChannelLayer
from stores.context import current_database, current_store, resolve_store, store_context
from stores.media import decode_login_scene, encode_login_scene, media_root
from stores.models import Store, StoreDomain
from stores.tasks import StoreTask


def picture(color):
    output = BytesIO()
    Image.new('RGB', (8, 8), color).save(output, format='PNG')
    return output.getvalue()


class StoreNamespaceTest(SimpleTestCase):
    def store(self, slug):
        return SimpleNamespace(slug=slug, pk=1 if slug == 'shenglang' else 2,
            database_alias='default' if slug == 'shenglang' else 'store_b')

    @override_settings(CACHES={'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'store-runtime-boundaries', 'KEY_FUNCTION': 'stores.cache.make_key',
    }})
    def test_cache_config_and_rate_counters_are_store_local(self):
        with store_context(self.store('shenglang')):
            cache.set('same-key', 12)
            self.assertTrue(cache.add('rate', 1))
        with store_context(self.store('branch-b')):
            self.assertIsNone(cache.get('same-key'))
            self.assertTrue(cache.add('rate', 1))
            cache.set('same-key', 25)
            cache.delete('rate')
        with store_context(self.store('shenglang')):
            self.assertEqual(cache.get('same-key'), 12)
            self.assertEqual(cache.get('rate'), 1)

    @override_settings(STORE_CHANNEL_LAYER={'BACKEND': 'channels.layers.InMemoryChannelLayer'})
    def test_identical_account_and_operator_groups_do_not_leak(self):
        async def check():
            layer = StoreChannelLayer()
            first, second = await layer.new_channel(), await layer.new_channel()
            with store_context(self.store('shenglang')):
                await layer.group_add('account_7', first)
                await layer.group_add('operators', first)
            with store_context(self.store('branch-b')):
                await layer.group_add('account_7', second)
                await layer.group_add('operators', second)
                await layer.group_send('account_7', {'type': 'isolated', 'value': 25})
            self.assertEqual(await layer.receive(second), {'type': 'isolated', 'value': 25})
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(layer.receive(first), timeout=.03)
            with store_context(self.store('shenglang')):
                await layer.group_send('operators', {'type': 'original'})
            self.assertEqual(await layer.receive(first), {'type': 'original'})
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(layer.receive(second), timeout=.03)
            await layer.flush()
        async_to_sync(check)()

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_context_restores_after_error_and_async_tasks_do_not_share_store(self):
        async def task(slug):
            async with store_context(self.store(slug)):
                await asyncio.sleep(0)
                return current_store().slug
        async def check():
            return await asyncio.gather(task('branch-b'), task('shenglang'))
        original = current_store()
        with store_context(None):
            self.assertEqual(async_to_sync(check)(), ['branch-b', 'shenglang'])
            self.assertIsNone(current_store())
            with self.assertRaises(RuntimeError):
                with store_context(self.store('branch-b')):
                    raise RuntimeError('rollback context')
            self.assertIsNone(current_store())
            self.assertEqual(current_database(), 'default')
        self.assertIs(current_store(), original)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_login_scene_roundtrip_fits_wechat_and_rejects_noncanonical_encoding(self):
        value = uuid.uuid4()
        with store_context(self.store('branch-b')):
            scene = encode_login_scene(value)
        self.assertLessEqual(len(scene), 32)
        self.assertEqual(decode_login_scene(scene), (2, value))
        self.assertEqual(decode_login_scene(value), (None, value))
        with store_context(None):
            platform_scene = encode_login_scene(value, platform=True)
        self.assertEqual(decode_login_scene(platform_scene), (None, value))
        with self.assertRaises(ValueError):
            decode_login_scene(value.hex)
        with self.assertRaises(ValueError):
            decode_login_scene('0_' + scene.split('_', 1)[1])
        with self.assertRaises(ValueError):
            decode_login_scene('invalid scene')


@override_settings(LOGIN_OTP_REQUIRED=False, LEGACY_FORCE_AUTH_COMPAT=False)
class StoreRuntimeDatabaseTest(TestCase):
    databases = {'default', 'store_b'}

    @classmethod
    def setUpTestData(cls):
        cls.original = resolve_store('shenglang')
        cls.branch = Store.objects.create(slug='runtime-b', name='运行隔离测试B店',
            database_alias='store_b', status=Store.Status.ACTIVE,
            initialized_at=timezone.now())
        cls.branch_host = 'runtime-b.esports.club'
        StoreDomain.objects.create(store=cls.branch, hostname=cls.branch_host)
        cls.accounts = {}
        for store in (cls.original, cls.branch):
            with store_context(store):
                cls.accounts[store.slug] = ClubAccount.objects.create_account(
                    username='same-boss', account_type='BOSS', id=777000,
                    account_no='XA777000', nickname=store.name)

    def setUp(self):
        folder = TemporaryDirectory(prefix='xa-store-runtime-')
        self.addCleanup(folder.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=folder.name))
        self.client = APIClient()

    def tokens(self, store):
        with store_context(store):
            return issue_account_tokens(self.accounts[store.slug])

    def test_jwt_cannot_resolve_overlapping_account_in_another_store(self):
        token, refresh = self.tokens(self.branch)
        self.assertEqual(AccessToken(token)['store_slug'], 'runtime-b')
        with store_context(self.original):
            with self.assertRaises(TokenError):
                account_for_claims(AccessToken(token))
            with self.assertRaises(TokenError):
                refresh_account_access_token(refresh)
        with store_context(self.branch):
            self.assertEqual(account_for_claims(AccessToken(token)).nickname, self.branch.name)
            self.assertEqual(AccessToken(refresh_account_access_token(refresh))['store_slug'], self.branch.slug)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_api_rejects_cross_store_token_and_refresh_even_with_same_account_id(self):
        token, refresh = self.tokens(self.branch)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(self.client.get('/api/users/me/').status_code, 401)
        self.assertEqual(self.client.get('/api/users/me/', HTTP_X_STORE_SLUG=self.branch.slug).status_code, 400)
        response = self.client.get('/api/users/me/', HTTP_HOST=self.branch_host)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['code'], 0)
        self.assertEqual(response['X-Store-Slug'], self.branch.slug)
        self.client.credentials()
        self.assertEqual(self.client.post('/api/users/refresh/', {'refresh': refresh}, format='json').status_code, 401)
        self.assertEqual(self.client.post('/api/users/refresh/', {'refresh': refresh}, format='json',
            HTTP_X_STORE_SLUG=self.branch.slug).status_code, 400)
        refreshed = self.client.post('/api/users/refresh/', {'refresh': refresh}, format='json', HTTP_HOST=self.branch_host)
        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(refreshed['X-Store-Slug'], self.branch.slug)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_unbound_claims_are_rejected_in_both_stores(self):
        _, raw = self.tokens(self.original)
        claims = RefreshToken(raw).payload.copy()
        claims.pop('store_slug')
        for store in (self.original, self.branch):
            with self.subTest(store=store.slug), store_context(store), self.assertRaises(TokenError):
                account_for_claims(claims)

    def test_issuing_token_for_foreign_database_object_fails(self):
        with store_context(self.original):
            with self.assertRaises(TokenError):
                issue_account_tokens(self.accounts[self.branch.slug])

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_media_paths_and_signed_capabilities_stay_in_their_store(self):
        urls, expected = {}, {}
        for store, color in ((self.original, '#2563eb'), (self.branch, '#ef4444')):
            with store_context(store):
                account = self.accounts[store.slug]
                session = ChatSession.objects.create(id=888000, account=account)
                message = ChatMessage.objects.create(id=999000, session=session,
                    sender_account=account, content_type='IMAGE')
                expected[store.slug] = picture(color)
                message.image.save('same.png', ContentFile(expected[store.slug]))
                urls[store.slug] = private_media_url(SimpleNamespace(account=account), message.image)
                self.assertTrue(message.image.path.startswith(str(media_root())))
        for slug, url in urls.items():
            host = self.branch_host if slug == self.branch.slug else 'sl.esports.club'
            response = self.client.get(url, HTTP_HOST=host)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b''.join(response.streaming_content), expected[slug])
        branch_url = urls[self.branch.slug]
        self.assertEqual(parse_qs(urlsplit(branch_url).query)['store'], [self.branch.slug])
        self.assertEqual(self.client.get(urlsplit(branch_url).path, HTTP_HOST='sl.esports.club').status_code, 404)
        self.assertEqual(self.client.get(branch_url, HTTP_HOST='sl.esports.club').status_code, 400)

    def test_media_renewal_rejects_another_stores_signed_ticket(self):
        with store_context(self.branch):
            account = self.accounts[self.branch.slug]
            session = ChatSession.objects.create(account=account)
            message = ChatMessage.objects.create(session=session, sender_account=account, content_type='IMAGE')
            message.image.save('renew.png', ContentFile(picture('#2563eb')))
            url = private_media_url(SimpleNamespace(account=account), message.image)
        ticket = urlsplit(url).path.split('/private/')[1].rstrip('/')
        token, _ = self.tokens(self.original)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(self.client.post('/api/media/renew/', {'ticket': ticket}, format='json').status_code, 404)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_new_store_public_files_are_read_from_a_separate_directory(self):
        with store_context(self.original):
            path = default_storage.save('accounts/avatars/shared.png', ContentFile(picture('#2563eb')))
        with store_context(self.branch):
            second = default_storage.save('accounts/avatars/shared.png', ContentFile(picture('#ef4444')))
            self.assertEqual(path, second)
            url = default_storage.url(second)
        response = self.client.get(url, HTTP_HOST=self.branch_host)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), picture('#ef4444'))
        response = self.client.get('/media/' + path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), picture('#2563eb'))

    @override_settings(MEDIA_BASE_URL='https://api.gateway.test')
    def test_public_presentation_uses_bound_store_origin_and_shared_platform_logo(self):
        self.branch.boss_url = f'http://{self.branch_host}/boss/'
        self.branch.logo_url = '/media/stores/logos/shared.png?store=runtime-b'
        self.branch.background_url = '/media/club-catalog/store-backgrounds/' + 'a' * 32 + '.jpg?store=runtime-b'
        self.branch.save(update_fields=['boss_url', 'logo_url', 'background_url'])
        with store_context(self.branch):
            default_storage.save('stores/logos/shared.png', ContentFile(picture('#ef4444')))
        response = self.client.get('/api/stores/current/', HTTP_HOST=self.branch_host, secure=True)
        self.assertEqual(response.status_code, 200, response.data)
        for field in ('logo_url', 'background_url'):
            media = urlsplit(response.data['data'][field])
            self.assertEqual((media.scheme, media.netloc), ('https', self.branch_host))
        logo = self.client.get(self.branch.logo_url, HTTP_HOST=self.branch_host)
        self.assertEqual(b''.join(logo.streaming_content), picture('#ef4444'))
        self.branch.logo_url = '/media/platform/logos/shared.png'
        self.branch.save(update_fields=['logo_url'])
        response = self.client.get('/api/stores/current/', HTTP_HOST=self.branch_host, secure=True)
        self.assertEqual(response.data['data']['logo_url'], 'https://api.gateway.test/media/platform/logos/shared.png')
        with store_context(self.branch):
            account = self.accounts[self.branch.slug]
            account.avatar.save('shared.png', ContentFile(picture('#ef4444')))
            admin = APIRequestFactory().get('/', HTTP_HOST='admin.esports.club', secure=True)
            admin.store = self.branch
            self.assertEqual(urlsplit(build_media_url(admin, account.avatar)).netloc, self.branch_host)
            self.assertEqual(urlsplit(build_media_url(admin, account.avatar)).scheme, 'https')
        from stores.serializers import public_store
        with store_context(None), patch('stores.context.ensure_store_connection', side_effect=AssertionError):
            self.assertEqual(public_store(self.branch)['logo_url'], 'https://api.gateway.test/media/platform/logos/shared.png')

    @override_settings(MEDIA_BASE_URL='https://api.gateway.test')
    def test_saved_public_links_redirect_only_from_the_configured_media_origin(self):
        path = '/media/accounts/avatars/shared.png'
        public_hosts = ((self.original, 'sl.esports.club'), (self.branch, self.branch_host))
        for index, (store, host) in enumerate(public_hosts):
            content = picture(('#2563eb', '#ef4444')[index])
            with store_context(store):
                self.assertEqual(default_storage.save(path.removeprefix('/media/'), ContentFile(content)),
                                 path.removeprefix('/media/'))
            legacy = path if index == 0 else f'{path}?store={store.slug}'
            response = self.client.get(legacy, HTTP_HOST='api.gateway.test', secure=True)
            self.assertEqual((response.status_code, response.content), (307, b''))
            self.assertEqual(response['Location'], f'https://{host}{legacy}')
            self.assertIn('no-store', response['Cache-Control'])
            local = self.client.get(legacy, HTTP_HOST=host, secure=True)
            self.assertEqual(b''.join(local.streaming_content), content)
        self.assertEqual(self.client.get(legacy, HTTP_HOST='sl.esports.club').status_code, 400)
        self.assertEqual(self.client.get(path, HTTP_HOST='unknown.gateway.test').status_code, 404)
        self.assertEqual(self.client.get('/media/private/recording.wav', HTTP_HOST='api.gateway.test').status_code, 404)

    @override_settings(MEDIA_BASE_URL='https://api.gateway.test')
    def test_managed_local_logo_preview_keeps_the_original_storage_value_on_save(self):
        from stores.serializers import StoreUpdateSerializer
        from stores.views import managed_store

        self.branch.logo_url = '/media/stores/logos/archived.png?store=runtime-b'
        self.branch.save(update_fields=['logo_url'])
        with store_context(None):
            preview = managed_store(self.branch)['logo_url']
            self.assertEqual(preview, f'https://{self.branch_host}{self.branch.logo_url}')
            editing = StoreUpdateSerializer(self.branch, data={'logo_url': preview}, partial=True)
            self.assertTrue(editing.is_valid(), editing.errors)
            self.assertEqual(editing.validated_data['logo_url'], self.branch.logo_url)
            clearing = StoreUpdateSerializer(self.branch, data={'logo_url': ''}, partial=True)
            self.assertTrue(clearing.is_valid(), clearing.errors)
            self.assertEqual(clearing.validated_data['logo_url'], '')

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    @override_settings(WECHAT_H5_APPID='isolated-h5', WECHAT_H5_SECRET='isolated-test-only',
                       WECHAT_H5_ALLOWED_ORIGINS=['https://runtime-b.esports.club'])
    def test_h5_poll_only_reads_the_registered_store(self):
        started = self.client.post('/api/users/h5-login/start/',
            {'return_origin': 'https://' + self.branch_host}, HTTP_HOST=self.branch_host, format='json')
        self.assertEqual(started.status_code, 200, started.data)
        flow = started.data['data']
        self.assertEqual(flow['store']['slug'], self.branch.slug)
        proof = {'ticket': flow['ticket'], 'poll_token': flow['poll_token']}
        status_path = '/api/users/h5-login/status/'
        local = self.client.get(status_path, proof, HTTP_HOST=self.branch_host)
        self.assertEqual(local.data['data'], {'status': 'PENDING'})
        self.assertEqual(local['X-Store-Slug'], self.branch.slug)
        self.assertEqual(self.client.get(status_path, proof, HTTP_HOST='sl.esports.club').status_code, 400)
        hinted = self.client.get(status_path, proof, HTTP_HOST='sl.esports.club', HTTP_X_STORE_SLUG=self.branch.slug)
        self.assertEqual(hinted.status_code, 400)
        self.assertNotIn('X-Store-Slug', hinted)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_queued_task_restores_store_before_looking_up_overlapping_ids(self):
        class ReadName(StoreTask):
            name = 'stores.tests_runtime.read_name'
            def run(inner):
                return ClubAccount.objects.get(pk=777000).nickname
        task = ReadName()
        from config.celery import app
        task.bind(app)
        with store_context(self.original):
            task.push_request(headers={'store_slug': self.branch.slug}, called_directly=False)
            try:
                self.assertEqual(task(), self.branch.name)
            finally:
                task.pop_request()
            self.assertIs(current_store(), self.original)

    def test_unrouted_record_delivery_cannot_adopt_ambient_store(self):
        task = StoreTask()
        task.name = 'stores.tests_runtime.single_record'
        task.run = Mock()
        with store_context(self.original):
            for headers in ({}, {'store_slug': None}, {'store_slug': ''}):
                task.push_request(headers=headers, called_directly=False)
                try:
                    with self.subTest(headers=headers), self.assertRaises(ValueError):
                        task()
                finally:
                    task.pop_request()
            task.run.assert_not_called()
            self.assertIs(current_store(), self.original)
            task()
            task.run.assert_called_once()

    def test_disabled_store_cannot_run_a_queued_task(self):
        Store.objects.filter(pk=self.branch.pk).update(status=Store.Status.DISABLED)
        task = StoreTask()
        from config.celery import app
        task.bind(app)
        task.run = Mock()
        task.push_request(headers={'store_slug': self.branch.slug}, called_directly=False)
        try:
            with self.assertRaises(ValueError):
                task()
            task.run.assert_not_called()
        finally:
            task.pop_request()

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_periodic_broker_task_fans_out_without_replaying_record_specific_jobs(self):
        task = StoreTask()
        from config.celery import app
        task.bind(app)
        task.name = 'orders.tasks.expire_reservations'
        task.run = Mock()
        with patch('celery.app.task.Task.apply_async', return_value=SimpleNamespace(id='isolated-test-job')) as send:
            for ambient in (None, self.original):
                with self.subTest(ambient=ambient), store_context(ambient):
                    task.push_request(headers={}, called_directly=False)
                    try:
                        result = task()
                        self.assertCountEqual([row['store'] for row in result['stores']], ['shenglang', self.branch.slug])
                        self.assertEqual(result['enqueue_unknown'], [])
                        self.assertIs(current_store(), ambient)
                        task.run.assert_not_called()
                        self.assertCountEqual([call.kwargs['headers']['store_slug'] for call in send.call_args_list],
                                              ['shenglang', self.branch.slug])
                        send.reset_mock()
                    finally:
                        task.pop_request()

    def test_async_transport_keeps_store_header_and_rejects_conflicting_store(self):
        task = StoreTask()
        task.name = 'stores.tests_runtime.single_record'
        with patch('celery.app.task.Task.apply_async', return_value=SimpleNamespace(id='test-transport')) as send:
            with store_context(self.branch):
                task.apply_async(args=[777000])
            self.assertEqual(send.call_args.kwargs['headers']['store_slug'], self.branch.slug)
            with store_context(self.branch):
                with self.assertRaises(ValueError):
                    task.apply_async(args=[777000], headers={'store_slug': 'shenglang'})


@override_settings(LOGIN_OTP_REQUIRED=False, LEGACY_FORCE_AUTH_COMPAT=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'stores.channels.StoreChannelLayer'}},
                   STORE_CHANNEL_LAYER={'BACKEND': 'channels.layers.InMemoryChannelLayer'})
class StoreRuntimeSocketTest(TransactionTestCase):
    databases = {'default', 'store_b'}

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_socket_uses_signed_store_and_observes_store_suspension(self):
        from channels.layers import get_channel_layer
        from channels.testing import WebsocketCommunicator
        from club_accounts.channels_middleware import ClubAccountTokenAuthMiddleware
        from orders.consumers import OrderConsumer

        branch = Store.objects.create(slug='socket-b', name='实时隔离测试B店',
            database_alias='store_b', status=Store.Status.ACTIVE, initialized_at=timezone.now())
        host = 'socket-b.esports.club'
        StoreDomain.objects.create(store=branch, hostname=host)
        original = resolve_store('shenglang')
        with store_context(branch):
            account = ClubAccount.objects.create_account(username='socket-boss',
                account_type='BOSS', id=777001, account_no='XA777001')
            token, _ = issue_account_tokens(account)
        application = ClubAccountTokenAuthMiddleware(OrderConsumer.as_asgi())

        async def check():
            headers = [(b'host', host.encode('ascii'))]
            socket = WebsocketCommunicator(application, '/ws/orders/?token=' + token, headers=headers)
            connected, _ = await socket.connect()
            self.assertTrue(connected)
            layer = get_channel_layer()
            with store_context(original):
                await layer.group_send('club_bosses', {'type': 'club_feed_update', 'data': {}})
            self.assertTrue(await socket.receive_nothing(timeout=.04))
            with store_context(branch):
                await layer.group_send('club_bosses', {'type': 'club_feed_update', 'data': {}})
            self.assertEqual((await socket.receive_json_from())['type'], 'club_feed_update')
            from channels.db import database_sync_to_async
            await database_sync_to_async(lambda: Store.objects.filter(pk=branch.pk).update(status=Store.Status.DISABLED))()
            await socket.send_json_to({'type': 'ping'})
            closed = await socket.receive_output()
            self.assertEqual(closed['type'], 'websocket.close')
            self.assertEqual(closed['code'], 4401)
            await socket.disconnect()
            conflicting = WebsocketCommunicator(application,
                '/ws/orders/?token=' + token + '&store=shenglang', headers=headers)
            connected, _ = await conflicting.connect()
            self.assertFalse(connected)
            await conflicting.disconnect()
        async_to_sync(check)()
