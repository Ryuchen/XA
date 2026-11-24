import os
from importlib import import_module
from io import BytesIO
from datetime import timedelta
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.apps import apps
from django.db import connections
from django.test import override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APITestCase

from club_accounts.models import WechatIdentity
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderApplication
from orders.tests.factories import make_order, make_provider, make_service, make_user
from users.models import EscortProfile


@override_settings(DEBUG=True, PROVIDER_WECHAT_GATE_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
    WECHAT_H5_APPID='wx-test', WECHAT_H5_SECRET='test-secret', ALLOWED_HOSTS=['*'],
    WECHAT_H5_ALLOWED_ORIGINS=['http://sl.esports.club'])
class ProviderWechatGateTests(APITestCase):
    def setUp(self):
        self.client.defaults['HTTP_HOST'] = 'sl.esports.club'
        self.provider = make_provider(deposit_mode='EARNINGS')
        self.account = get_or_create_account_for_legacy_user(self.provider)
        self.account.phone = '13800001111'
        self.account.is_phone_verified = True
        self.account.save(update_fields=['phone', 'is_phone_verified'])
        self.profile = self.provider.escort_profile
        self.profile.account = self.account
        self.profile.save(update_fields=['account'])

    def bind(self, openid='authorized-wechat'):
        self.account.is_openid_bound = True
        self.account.save(update_fields=['is_openid_bound'])
        return WechatIdentity.objects.create(account=self.account, channel='OFFICIAL_H5', appid='wx-test', openid=openid)

    def start_sync(self):
        token, _ = issue_account_tokens(self.account, auth_method='password_sms')
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        response = self.client.post('/api/users/h5-login/start/', {
            'audience': 'provider', 'purpose': 'sync_avatar', 'return_origin': 'http://sl.esports.club',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.client.credentials()
        return response.data['data']

    def callback(self, flow):
        query = parse_qs(urlsplit(flow['authorization_url']).query)
        self.assertEqual(query['scope'], ['snsapi_userinfo'])
        return self.client.get('/api/users/h5-login/callback/', {'code': 'test-code', 'state': query['state'][0]})

    def test_binding_and_avatar_evidence_are_separate_and_legacy_is_not_forged(self):
        self.account.avatar_url = 'https://example.com/manually-uploaded.png'
        self.account.save(update_fields=['avatar_url'])
        self.assertIn('绑定本人微信', self.profile.order_acceptance_error())
        self.bind()
        self.profile = EscortProfile.objects.get(pk=self.profile.pk)
        self.assertIn('微信获取头像', self.profile.order_acceptance_error())
        self.profile.wechat_avatar_legacy_exempt = True
        self.profile.save(update_fields=['wechat_avatar_legacy_exempt'])
        self.assertEqual(self.profile.order_acceptance_error(), '')
        self.assertIsNone(self.profile.wechat_avatar_verified_at)
        self.account.is_openid_bound = False
        self.account.save(update_fields=['is_openid_bound'])
        self.profile = EscortProfile.objects.get(pk=self.profile.pk)
        self.assertIn('绑定本人微信', self.profile.order_acceptance_error())

    def test_audition_submission_uses_the_same_wechat_gate(self):
        from orders.applications import ApplicationError, eligible
        customer, service = make_user(), make_service()
        order = make_order(customer, service=service, requires_boss_selection=True)
        with self.assertRaisesMessage(ApplicationError, '绑定本人微信'):
            eligible(order, self.account, visibility=False)
        self.bind()
        with self.assertRaisesMessage(ApplicationError, '微信获取头像'):
            eligible(order, self.account, visibility=False)
        self.profile.wechat_avatar_legacy_exempt = True
        self.profile.save(update_fields=['wechat_avatar_legacy_exempt'])
        self.assertEqual(eligible(order, self.account, visibility=False).pk, self.profile.pk)

    def test_real_audition_registration_does_not_assign_unqualified_provider(self):
        customer, service = make_user(), make_service()
        customer_account = get_or_create_account_for_legacy_user(customer)
        order = make_order(customer, service=service, customer_account=customer_account, requires_boss_selection=True)
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now()-timedelta(hours=1))
        token, _ = issue_account_tokens(self.account, auth_method='password_sms')
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        url = f'/api/orders/orders/{order.pk}/grab/'
        denied = self.client.post(url, {}, format='json')
        self.assertNotEqual(denied.data['code'], 0)
        self.assertFalse(OrderApplication.objects.filter(order=order).exists())
        self.bind()
        denied = self.client.post(url, {}, format='json')
        self.assertNotEqual(denied.data['code'], 0)
        self.assertFalse(OrderApplication.objects.filter(order=order).exists())
        self.profile.wechat_avatar_legacy_exempt = True
        self.profile.save(update_fields=['wechat_avatar_legacy_exempt'])
        from users.test_voice_recordings import audio_file
        rec = self.client.post('/api/users/recordings/', {'audio': audio_file(), 'title': '隔离试音',
            'client_request_id': 'gate-recording'}, format='multipart')
        self.assertEqual(rec.data['code'], 0, rec.data)
        accepted = self.client.post(url, {'recording_id': rec.data['data']['id']}, format='json')
        self.assertEqual(accepted.data['code'], 0, accepted.data)
        self.assertTrue(OrderApplication.objects.filter(order=order, applicant=self.account).exists())

    def test_avatar_image_is_read_over_tls_from_the_allowlisted_wechat_cdn(self):
        from users.h5_login import verified_avatar_url
        body = BytesIO()
        Image.new('RGB', (4, 4), 'white').save(body, format='PNG')
        response = BytesIO(body.getvalue())
        info = {'openid': 'authorized-wechat', 'headimgurl': 'http://thirdwx.qlogo.cn/mmopen/verified/132'}
        with patch('users.h5_login._wechat_http_get', return_value=info), \
                patch('users.h5_login.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = response
            result = verified_avatar_url({'openid': 'authorized-wechat', 'access_token': 'test-token'})
            self.assertEqual(result, 'https://thirdwx.qlogo.cn/mmopen/verified/132')
            self.assertTrue(opener.return_value.open.call_args.args[0].full_url.startswith('https://thirdwx.qlogo.cn/'))
        long_url = 'http://thirdwx.qlogo.cn/' + 'x' * (200 - len('http://thirdwx.qlogo.cn/'))
        with patch('users.h5_login._wechat_http_get', return_value={'openid': 'authorized-wechat', 'headimgurl': long_url}), \
                patch('users.h5_login._verify_wechat_avatar_image') as fetch:
            with self.assertRaises(ValueError):
                verified_avatar_url({'openid': 'authorized-wechat', 'access_token': 'test-token'})
            fetch.assert_not_called()
    @skipUnless(os.environ.get('XA_WECHAT_H5_FORMAL') == '1',
                '真实微信 H5 头像同步会话语义待最终联调（18-盘点真实第三方）；断言保留')
    def test_wechat_avatar_sync_accepts_only_server_verified_identity_and_does_not_issue_login(self):
        self.bind()
        flow = self.start_sync()
        avatar = 'https://thirdwx.qlogo.cn/mmopen/authorized/132'
        with patch('users.h5_login.exchange_code', return_value={'openid': 'authorized-wechat', 'access_token': 'test-token'}), \
                patch('users.h5_login._wechat_http_get', return_value={'openid': 'authorized-wechat', 'headimgurl': avatar}), \
                patch('users.h5_login._verify_wechat_avatar_image'):
            response = self.callback(flow)
        self.assertIn('微信授权已完成'.encode(), response.content)
        self.account.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual(self.account.avatar_url, avatar)
        self.assertIsNotNone(self.profile.wechat_avatar_verified_at)
        self.assertFalse(self.profile.wechat_avatar_legacy_exempt)
        self.assertEqual(self.profile.order_acceptance_error(), '')
        result = self.client.get('/api/users/h5-login/status/', {'ticket': flow['ticket'], 'poll_token': flow['poll_token']}).data['data']
        self.assertTrue(result['avatarSynced'])
        self.assertNotIn('token', result)

    def test_mismatched_identity_and_forged_avatar_are_rejected(self):
        self.bind()
        for proof, info in [({'openid': 'another-wechat', 'access_token': 'test-token'}, {}),
                            ({'openid': 'authorized-wechat', 'access_token': 'test-token'},
                             {'openid': 'authorized-wechat', 'headimgurl': 'https://attacker.example/avatar.png'})]:
            flow = self.start_sync()
            with patch('users.h5_login.exchange_code', return_value=proof), patch('users.h5_login._wechat_http_get', return_value=info):
                response = self.callback(flow)
            self.assertIn('微信授权未完成'.encode(), response.content)
            self.profile.refresh_from_db()
            self.assertIsNone(self.profile.wechat_avatar_verified_at)
            self.assertIn('微信获取头像', self.profile.order_acceptance_error())

    @override_settings(WECHAT_H5_AVATAR_SYNC_ENABLED=False)
    def test_test_environment_cannot_simulate_real_wechat_avatar_proof(self):
        self.bind()
        token, _ = issue_account_tokens(self.account, auth_method='password_sms')
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        with patch('users.h5_login._wechat_http_get', side_effect=AssertionError('external call must not occur')):
            response = self.client.post('/api/users/h5-login/start/', {
                'audience': 'provider', 'purpose': 'sync_avatar', 'return_origin': 'http://sl.esports.club',
            }, format='json')
        self.assertEqual(response.status_code, 503)
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.wechat_avatar_verified_at)

    def test_migration_exempts_only_bound_previously_accepted_non_gift_providers(self):
        self.bind()
        customer, service = make_user(), make_service()
        make_order(customer, service=service, provider=self.provider, provider_account=self.account, status=Order.Status.COMPLETED)
        another = make_provider(deposit_mode='EARNINGS')
        another_account = get_or_create_account_for_legacy_user(another)
        another_account.is_openid_bound = True
        another_account.openid = 'another-bound'
        another_account.save(update_fields=['is_openid_bound', 'openid'])
        another_profile = another.escort_profile
        another_profile.account = another_account
        another_profile.save(update_fields=['account'])
        make_order(customer, service=service, provider=another, provider_account=another_account, status=Order.Status.PENDING)
        migration = import_module('users.migrations.0039_legacy_wechat_avatar_exemption')
        migration.preserve_verified_legacy_providers(apps, SimpleNamespace(connection=connections['default']))
        self.profile.refresh_from_db()
        another_profile.refresh_from_db()
        self.assertTrue(self.profile.wechat_avatar_legacy_exempt)
        self.assertIsNone(self.profile.wechat_avatar_verified_at)
        self.assertFalse(another_profile.wechat_avatar_legacy_exempt)
