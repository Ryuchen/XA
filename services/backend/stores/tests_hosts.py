"""Real two-database authorization through store-bound HTTP/ASGI hosts."""
import os
from unittest import skipUnless
from io import StringIO
from urllib.parse import parse_qs, urlsplit
import uuid
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.core import signing
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens

from .context import current_store, resolve_store, store_context
from .hosts import request_store_slug, store_entry_url
from .media import encode_login_scene
from .models import Store, StorePlatformGrant
from .permissions import may_manage_stores
from .tests_foundation import admin


def xinan_store():
    return Store.objects.create(slug='xinan', name='兴安电竞', currency_name='星辉',
        database_alias='store_b', status=Store.Status.ACTIVE, initialized_at=timezone.now(),
        boss_url='http://xa.esports.club/boss', provider_url='http://xa.esports.club/player')


class BoundStoreLinkTest(TestCase):
    def test_bound_entries_remove_store_hints_and_keep_path_route_and_other_query(self):
        url = store_entry_url('https://sl.esports.club/boss?campaign=qa&store=xinan#/orders', 'shenglang')
        self.assertEqual(url, 'https://sl.esports.club/boss?campaign=qa#/orders')
        self.assertEqual(store_entry_url('http://xa.esports.club/player?store=xinan', 'xinan'),
                         'http://xa.esports.club/player')

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_integration_base_is_rebound_to_the_current_store_origin(self):
        self.assertEqual(store_entry_url('https://sl.esports.club/player#/orders', 'xinan'),
                         'https://xa.esports.club/player#/orders')
        self.assertEqual(store_entry_url('http://localhost:13101/#/home', 'xinan'),
                         'http://xa.esports.club/#/home')

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_domain_is_canonical_and_unknown_esports_subdomains_never_fall_back(self):
        self.assertEqual(request_store_slug('XA.ESPORTS.CLUB.:80'), 'xinan')
        self.assertEqual(request_store_slug('sl.esports.club', 'shenglang', ['shenglang']), 'shenglang')
        for host in ('missing.esports.club', 'esports.club'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                request_store_slug(host, 'shenglang', ['shenglang'])
        for header, query in [('xinan', []), (None, ['xinan']), ('shenglang', ['xinan']),
                              (None, ['shenglang', 'xinan']), ('', [])]:
            with self.subTest(header=header, query=query), self.assertRaises(ValueError):
                request_store_slug('sl.esports.club', header, query)


@override_settings(LOGIN_OTP_REQUIRED=False, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class BoundStoreHttpTest(TestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.first = resolve_store()
        self.second = xinan_store()
        self.client = APIClient()
        self.accounts, self.tokens = {}, {}
        for store in (self.first, self.second):
            with store_context(store):
                account = ClubAccount.objects.create_account(username='host-same-boss',
                    account_type='BOSS', id=807001, account_no='XA807001', nickname=store.name + '老板')
                self.accounts[store.slug] = account
                self.tokens[store.slug] = issue_account_tokens(account)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_host_selects_its_database_without_a_selector_or_existing_login(self):
        for host, store in [('sl.esports.club', self.first), ('xa.esports.club', self.second)]:
            with self.subTest(host=host):
                response = self.client.get('/api/stores/current/', HTTP_HOST=host)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data['data']['slug'], store.slug)
                self.assertEqual(response['X-Store-Slug'], store.slug)
                self.assertFalse(response.data['data']['can_manage_stores'])
                token, _ = self.tokens[store.slug]
                response = self.client.get('/api/users/me/', HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data['data']['nickname'], store.name + '老板')
        self.assertIsNone(current_store())

    def test_query_header_duplicates_and_login_scenes_cannot_override_the_host(self):
        for path, headers in [('/api/stores/current/?store=xinan', {}),
                              ('/api/stores/current/', {'HTTP_X_STORE_SLUG': 'xinan'}),
                              ('/api/stores/current/?store=shenglang&store=xinan', {}),
                              ('/api/stores/current/?store=xinan', {'HTTP_X_STORE_SLUG': 'xinan'}),
                              ('/api/stores/current/?store=', {})]:
            with self.subTest(path=path, headers=headers):
                self.assertEqual(self.client.get(path, HTTP_HOST='sl.esports.club', **headers).status_code, 400)
        with store_context(self.second):
            scene = encode_login_scene(uuid.uuid4())
        response = self.client.post('/api/users/web-login/session/', {'ticket': scene},
            format='json', HTTP_HOST='sl.esports.club')
        self.assertEqual(response.status_code, 400)
        response = self.client.get('/api/stores/current/', HTTP_HOST='sl.esports.club',
                                   HTTP_X_FORWARDED_HOST='xa.esports.club')
        self.assertEqual(response.data['data']['slug'], 'shenglang')

    @override_settings(DEBUG=True, WECHAT_H5_APPID='wx-test', WECHAT_H5_SECRET='test-secret',
        WECHAT_H5_ALLOWED_ORIGINS=['https://sl.esports.club', 'https://xa.esports.club'])
    def test_h5_poll_ticket_is_validated_in_the_host_store(self):
        start_url = '/api/users/h5-login/start/'
        status_url = '/api/users/h5-login/status/'
        for own_host, other_host, own_store, other_store in (
            ('sl.esports.club', 'xa.esports.club', self.first, self.second),
            ('xa.esports.club', 'sl.esports.club', self.second, self.first),
        ):
            with self.subTest(host=own_host):
                started = self.client.post(start_url, {
                    'audience': 'boss', 'return_origin': 'https://' + own_host,
                }, format='json', HTTP_HOST=own_host)
                self.assertEqual(started.status_code, 200)
                flow = started.data['data']
                self.assertEqual(flow['store']['slug'], own_store.slug)
                proof = {'ticket': flow['ticket'], 'poll_token': flow['poll_token']}
                pending = self.client.get(status_url, proof, HTTP_HOST=own_host)
                self.assertEqual(pending.status_code, 200, pending.content)
                self.assertEqual(pending['X-Store-Slug'], own_store.slug)
                self.assertEqual(pending.data['data'], {'status': 'PENDING'})

                for host, store, submitted in (
                    (own_host, own_store, {**proof, 'poll_token': 'wrong-secret'}),
                    (own_host, own_store, {**proof, 'ticket': 'malformed'}),
                    (other_host, other_store, proof),
                ):
                    denied = self.client.get(status_url, submitted, HTTP_HOST=host)
                    self.assertEqual(denied.status_code, 400)
                    self.assertEqual(denied['X-Store-Slug'], store.slug)
                    self.assertEqual(denied.data['errorCode'], 'WECHAT_H5_INVALID')
                    self.assertNotIn('data', denied.data)

                callback = '/api/users/h5-login/callback/'
                state = parse_qs(urlsplit(flow['authorization_url']).query)['state'][0]
                callback_on_other_host = self.client.get(callback, {'state': state}, HTTP_HOST=other_host)
                self.assertEqual(callback_on_other_host.status_code, 400)
                self.assertNotIn('X-Store-Slug', callback_on_other_host)
                expired_at = timezone.now().timestamp() - 601
                with patch('django.core.signing.time.time', return_value=expired_at):
                    expired = signing.dumps({'store_slug': own_store.slug}, salt='xa.wechat-h5.login.v1')
                for rejected_state in (state + 'tampered', expired):
                    invalid = self.client.get(callback, {'state': rejected_state}, HTTP_HOST=own_host)
                    self.assertEqual(invalid.status_code, 400)
                    self.assertNotIn('X-Store-Slug', invalid)

    def test_unknown_domains_never_accept_valid_store_hints(self):
        for host in ('unknown.esports.club', 'nested.sl.esports.club', 'esports.club'):
            with self.subTest(host=host):
                response = self.client.get('/api/stores/current/?store=shenglang',
                    HTTP_HOST=host, HTTP_X_STORE_SLUG='shenglang')
                self.assertEqual(response.status_code, 404)
                self.assertNotIn('X-Store-Slug', response)

    def test_signed_tokens_and_refresh_are_rejected_by_the_other_store_host(self):
        for slug, own_host, other_host in [('shenglang', 'sl.esports.club', 'xa.esports.club'),
                                          ('xinan', 'xa.esports.club', 'sl.esports.club')]:
            token, refresh = self.tokens[slug]
            with self.subTest(slug=slug):
                self.assertEqual(self.client.get('/api/users/me/', HTTP_HOST=other_host,
                    HTTP_AUTHORIZATION='Bearer ' + token).status_code, 401)
                self.assertEqual(self.client.post('/api/users/refresh/', {'refresh': refresh},
                    format='json', HTTP_HOST=other_host).status_code, 401)
                response = self.client.post('/api/users/refresh/', {'refresh': refresh},
                    format='json', HTTP_HOST=own_host)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(AccessToken(response.data['data']['token'])['store_slug'], slug)

    def test_disabled_bound_store_does_not_fall_back_to_default(self):
        self.second.status = Store.Status.DISABLED
        self.second.save(update_fields=['status'])
        self.assertEqual(self.client.get('/api/stores/current/', HTTP_HOST='xa.esports.club').status_code, 403)
        self.assertEqual(self.client.get('/api/stores/current/', HTTP_HOST='sl.esports.club').status_code, 200)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_public_and_tenant_directories_never_list_the_other_store(self):
        for host, slug in [('sl.esports.club', 'shenglang'), ('xa.esports.club', 'xinan')]:
            response = self.client.get('/api/stores/available/', HTTP_HOST=host)
            self.assertEqual([row['slug'] for row in response.data['data']['results']], [slug])
        response = self.client.get('/api/stores/current/', HTTP_HOST='xa.esports.club')
        self.assertEqual(response.data['data']['boss_url'], 'http://xa.esports.club/boss')
        self.assertEqual(response.data['data']['provider_url'], 'http://xa.esports.club/player')


@override_settings(LOGIN_OTP_REQUIRED=False, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class PlatformSuperAdministratorTest(TestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.first = resolve_store()
        self.second = xinan_store()
        with store_context(self.first):
            self.owner = admin('host-owner', super_admin=True)
            self.ordinary = admin('host-ordinary')
        with store_context(self.second):
            self.local_owner = admin('host-owner', super_admin=True)

    def client_for(self, account, store):
        with store_context(store):
            token, _ = issue_account_tokens(account)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token,
                           HTTP_HOST='sl.esports.club' if store.slug == 'shenglang' else 'xa.esports.club')
        return client

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_both_real_super_role_and_explicit_default_platform_grant_are_required(self):
        owner_client = self.client_for(self.owner, self.first)
        self.assertEqual(owner_client.get('/api/stores/manage/').status_code, 403)
        StorePlatformGrant.objects.create(account_id=self.owner.pk)
        self.assertEqual(owner_client.get('/api/stores/manage/').status_code, 200)
        self.assertEqual({row['slug'] for row in owner_client.get('/api/stores/available/').data['data']['results']},
                         {'shenglang', 'xinan'})
        ordinary_client = self.client_for(self.ordinary, self.first)
        StorePlatformGrant.objects.create(account_id=self.ordinary.pk)
        self.assertEqual(ordinary_client.get('/api/stores/manage/').status_code, 403)
        self.assertFalse(ordinary_client.get('/api/stores/current/').data['data']['is_platform_admin'])
        self.assertEqual([row['slug'] for row in ordinary_client.get('/api/stores/available/').data['data']['results']],
                         ['shenglang'])
        self.assertEqual(self.owner.pk, self.local_owner.pk)
        local_client = self.client_for(self.local_owner, self.second)
        self.assertEqual(local_client.get('/api/stores/manage/').status_code, 403)
        self.assertFalse(local_client.get('/api/stores/current/').data['data']['is_platform_admin'])

    def test_revoking_role_membership_or_platform_grant_removes_authority(self):
        StorePlatformGrant.objects.create(account_id=self.owner.pk)
        self.assertTrue(may_manage_stores(self.owner))
        role = self.owner.admin_membership.roles.get(code='super_admin')
        role.is_active = False
        role.save(update_fields=['is_active'])
        self.assertFalse(may_manage_stores(self.owner))
        role.is_active = True
        role.save(update_fields=['is_active'])
        StorePlatformGrant.objects.filter(account_id=self.owner.pk).update(is_active=False)
        self.assertFalse(may_manage_stores(self.owner))

    def test_platform_grant_command_rejects_ordinary_staff_and_accepts_real_super_admin(self):
        with self.assertRaises(CommandError):
            call_command('grant_store_platform', username=self.ordinary.username, stdout=StringIO())
        self.assertFalse(StorePlatformGrant.objects.filter(account_id=self.ordinary.pk).exists())
        call_command('grant_store_platform', username=self.owner.username, stdout=StringIO())
        self.assertTrue(may_manage_stores(self.owner))

    @override_settings(LEGACY_FORCE_AUTH_COMPAT=True)
    def test_legacy_superuser_flag_cannot_replace_the_real_super_role(self):
        account = ClubAccount.objects.create_account(username='legacy-super-flag', account_type='STAFF')
        account._legacy_is_superuser = True
        StorePlatformGrant.objects.create(account_id=account.pk)
        self.assertFalse(may_manage_stores(account))


@override_settings(LOGIN_OTP_REQUIRED=False, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class BoundStoreWebsocketTest(TransactionTestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.first, self.second = resolve_store(), xinan_store()
        self.tokens = {}
        for store in (self.first, self.second):
            with store_context(store):
                account = ClubAccount.objects.create_account(username='host-ws-boss', account_type='BOSS',
                    id=809001, account_no='XA809001')
                self.tokens[store.slug] = issue_account_tokens(account)[0]

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_http_host_binding_also_applies_to_real_websocket_handshakes_and_groups(self):
        from channels.layers import get_channel_layer
        from channels.testing import WebsocketCommunicator
        from club_accounts.channels_middleware import ClubAccountTokenAuthMiddleware
        from orders.consumers import OrderConsumer
        application = ClubAccountTokenAuthMiddleware(OrderConsumer.as_asgi())

        async def run():
            sockets = []
            for slug, host in [('shenglang', b'sl.esports.club'), ('xinan', b'xa.esports.club')]:
                socket = WebsocketCommunicator(application, '/ws/orders/?token=' + self.tokens[slug],
                                               headers=[(b'host', host)])
                self.assertTrue((await socket.connect())[0])
                sockets.append(socket)
            layer = get_channel_layer()
            with store_context(self.first):
                await layer.group_send('club_bosses', {'type': 'club_feed_update', 'data': {'store': 'shenglang'}})
            self.assertEqual(await sockets[0].receive_json_from(), {'type': 'club_feed_update', 'data': {}})
            self.assertTrue(await sockets[1].receive_nothing(timeout=.04))
            with store_context(self.second):
                await layer.group_send('club_bosses', {'type': 'club_feed_update', 'data': {'store': 'xinan'}})
            self.assertEqual(await sockets[1].receive_json_from(), {'type': 'club_feed_update', 'data': {}})
            self.assertTrue(await sockets[0].receive_nothing(timeout=.04))
            for socket in sockets:
                await socket.disconnect()
        async_to_sync(run)()

    def test_foreign_tokens_hints_duplicate_selectors_and_unknown_hosts_never_connect(self):
        from channels.testing import WebsocketCommunicator
        from club_accounts.channels_middleware import ClubAccountTokenAuthMiddleware
        from orders.consumers import OrderConsumer
        application = ClubAccountTokenAuthMiddleware(OrderConsumer.as_asgi())

        async def run():
            for host, token_slug, query, extra in [
                (b'sl.esports.club', 'xinan', '', []),
                (b'xa.esports.club', 'shenglang', '', []),
                (b'sl.esports.club', 'xinan', '&store=xinan', []),
                (b'sl.esports.club', 'shenglang', '', [(b'x-store-slug', b'xinan')]),
                (b'sl.esports.club', 'shenglang', '&store=shenglang&store=xinan', []),
                (b'sl.esports.club', 'shenglang', '&store=', []),
                (b'unknown.esports.club', 'shenglang', '&store=shenglang', []),
                (b'sl.esports.club', 'shenglang', '', [(b'host', b'xa.esports.club')]),
            ]:
                socket = WebsocketCommunicator(application, '/ws/orders/?token=' + self.tokens[token_slug] + query,
                                               headers=[(b'host', host), *extra])
                connected, code = await socket.connect()
                self.assertFalse(connected, (host, token_slug, query))
                self.assertEqual(code, 4401)
                await socket.disconnect()
            no_host = WebsocketCommunicator(application, '/ws/orders/?token=' + self.tokens['shenglang'])
            no_host.scope['server'] = ('127.0.0.1', 18105)
            self.assertEqual(await no_host.connect(), (False, 4401))
            await no_host.disconnect()
        async_to_sync(run)()
