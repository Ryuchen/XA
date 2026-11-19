"""A provisioned store is configurable but never open for business."""
import os
from unittest import skipUnless
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from .context import resolve_store, store_context
from .models import Store, StorePlatformGrant, StoreProvisioningClaim
from .tests_foundation import admin
from .tests_hosts import xinan_store


@override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False, LOGIN_OTP_REQUIRED=False)
class SetupStageTests(TestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.original = resolve_store()
        self.branch = xinan_store()
        self.branch.status = Store.Status.CONFIGURING
        self.branch.save(update_fields=['status'])
        with store_context(self.branch):
            # The fast schema-sync runner skips the role data migration; match
            # the permissions provisioned to a real store administrator.
            from console.models import AdminRole
            from console.permissions import ALL_PERMISSION_SET
            self.owner = admin('setup-owner', super_admin=True)
            AdminRole.objects.filter(code='super_admin').update(permissions=sorted(ALL_PERMISSION_SET))
            self.owner.phone = '13800001111'
            self.owner.save(update_fields=['phone'])
            self.other = admin('setup-other', super_admin=True)
            self.other.phone = '13800002222'
            self.other.save(update_fields=['phone'])
        StoreProvisioningClaim.objects.create(store=self.branch, admin_username='setup-owner', database_fingerprint='a' * 64)

    def login(self, username):
        client = APIClient(HTTP_HOST='admin.esports.club')
        credentials = {'username': 'xinan/' + username, 'password': 'test-password'}
        with patch('users.password_login.send_verification_code') as sender:
            challenge = client.post('/api/admin/auth/password-login', credentials, format='json')
        if challenge.status_code != 200:
            return client, challenge
        response = client.post('/api/admin/auth/password-login', {**credentials,
            'smsChallenge': challenge.data['data']['smsChallenge'], 'smsCode': sender.call_args.args[1]}, format='json')
        if response.status_code == 200:
            client.credentials(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + response.data['data']['token'])
        return client, response

    def test_first_admin_real_sms_can_configure_but_not_operate_business(self):
        client, response = self.login('setup-owner')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['data']['store']['status'], 'CONFIGURING')
        refresh_token = response.data['data']['refresh']
        self.assertEqual(client.post('/api/admin/auth/refresh', {'refresh': refresh_token}, format='json').status_code, 200)
        current = client.get('/api/stores/current/')
        self.assertEqual((current.status_code, current.data['data']['status']), (200, 'CONFIGURING'))
        self.assertEqual(client.get('/api/admin/dictionaries/').status_code, 200)
        self.assertEqual(client.put('/api/admin/config/provider-capacity/',
                         {'max_concurrent_orders': 4, 'version': 0}, format='json').status_code, 200)
        points_url = '/api/admin/config/points-redemption/'
        self.assertEqual(client.get(points_url).data['data']['version'], 0)
        points = client.put(points_url, {'group_point_hundredths': 10000, 'group_coin_units': 30,
            'max_ratio_basis_points': 5000, 'version': 0}, format='json')
        self.assertEqual(points.status_code, 200, points.data)
        self.assertFalse(points.data['data']['redemption_available'])
        with store_context(self.original):
            from orders.points_redemption_config import current_rules
            self.assertEqual(current_rules()['version'], 0)
        for method, path in [('get', '/api/admin/orders/'), ('post', '/api/admin/orders/'),
                             ('get', '/api/admin/wallets/'), ('post', '/api/admin/recharge-records/'),
                             ('get', '/api/stores/manage/'), ('post', '/api/admin/config/withdraw/extra/')]:
            result = getattr(client, method)(path, {}, format='json')
            self.assertEqual(result.status_code, 403, (method, path, result.content))
            self.assertEqual(result.json()['errorCode'], 'STORE_SETUP_ONLY')
        for host in ('xa.esports.club', 'sl.esports.club'):
            response = APIClient().get('/api/stores/current/', HTTP_HOST=host,
                HTTP_AUTHORIZATION=client._credentials['HTTP_AUTHORIZATION'])
            self.assertNotEqual(response.status_code, 200)
        for path in ('/api/users/escorts/', '/api/orders/create/', '/api/wallet/withdraw/'):
            self.assertEqual(APIClient().get(path, HTTP_HOST='xa.esports.club').status_code, 403, path)
        self.branch.status = Store.Status.DISABLED
        self.branch.save(update_fields=['status'])
        self.assertEqual(client.get('/api/admin/dictionaries/').status_code, 401)
        self.assertEqual(client.post('/api/admin/auth/refresh', {'refresh': refresh_token}, format='json').status_code, 401)

    def test_only_original_admin_can_enter_and_platform_cannot_skip_opening(self):
        _, response = self.login('setup-other')
        self.assertNotEqual(response.status_code, 200)
        with store_context(self.branch):
            other_token = issue_account_tokens(self.other)[0]
        unauthorized = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + other_token)
        self.assertEqual(unauthorized.get('/api/admin/dictionaries/').status_code, 401)
        with store_context(self.original):
            platform = admin('setup-platform', super_admin=True)
            StorePlatformGrant.objects.create(account_id=platform.pk)
            token, _ = issue_account_tokens(platform)
        client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        result = client.patch(f'/api/stores/manage/{self.branch.pk}/', {'status': 'ACTIVE'}, format='json')
        self.assertEqual(result.status_code, 400, result.content)
        self.branch.refresh_from_db()
        self.assertEqual(self.branch.status, Store.Status.CONFIGURING)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_setup_owner_can_configure_catalog_and_timeout_only_in_own_store(self):
        from orders.models import GameCategory, OrderTimeoutConfig, ServiceCategory, ServiceItem
        client, response = self.login('setup-owner')
        self.assertEqual(response.status_code, 200, response.data)
        timeout = client.put('/api/admin/config/order-timeout/', {'minutes': 45, 'revision': 0}, format='json')
        self.assertEqual(timeout.status_code, 200, timeout.data)
        for path, values in (
            ('reservation-deadlines', {'confirmation_minutes': 30, 'payment_minutes': 60, 'version': 0}),
            ('room-confirmation', {'minutes': 30, 'version': 0}),
        ):
            result = client.put('/api/admin/config/' + path + '/', values, format='json')
            self.assertEqual(result.status_code, 200, result.data)
            key = path.replace('-', '_')
            confirmation_url = '/api/admin/dictionaries/setup-confirmation/' + key + '/'
            snapshot = client.get(confirmation_url)
            self.assertEqual(snapshot.status_code, 200, snapshot.data)
            confirmed = client.post(confirmation_url, {'fingerprint': snapshot.data['data']['fingerprint']}, format='json')
            self.assertEqual(confirmed.status_code, 200, confirmed.data)
        game = client.post('/api/admin/game-categories/', {'name': '初始化游戏'}, format='json')
        self.assertEqual(game.status_code, 201, game.data)
        category = client.post('/api/admin/service-categories/', {'name': '初始化服务'}, format='json')
        self.assertEqual(category.status_code, 201, category.data)
        service = client.post('/api/admin/service-items/', {'name': '初始化套餐', 'price': 1000,
            'game_category': game.data['data']['id'], 'service_category': category.data['data']['id']}, format='json')
        self.assertEqual(service.status_code, 201, service.data)
        with store_context(self.branch):
            self.assertEqual(OrderTimeoutConfig.objects.get(pk=1).minutes, 45)
            self.assertTrue(ServiceItem.objects.filter(name='初始化套餐').exists())
        with store_context(self.original):
            self.assertFalse(GameCategory.objects.filter(name='初始化游戏').exists())
            self.assertFalse(ServiceCategory.objects.filter(name='初始化服务').exists())
            self.assertFalse(ServiceItem.objects.filter(name='初始化套餐').exists())
        self.assertEqual(client.get('/api/admin/orders/').status_code, 403)
        self.assertEqual(client.post('/api/admin/service-items/1/unlisted-action/', {}, format='json').status_code, 403)
        self.branch.refresh_from_db()
        self.assertEqual(self.branch.status, Store.Status.CONFIGURING)

    @override_settings(POINTS_USAGE_SWITCHES_ENABLED=True)
    def test_setup_admin_can_configure_points_usage_in_own_store(self):
        client, response = self.login('setup-owner')
        self.assertEqual(response.status_code, 200, response.data)
        path = '/api/admin/config/points-usage/'
        self.assertEqual(client.get(path).data['data']['version'], 0)
        result = client.put(path, {'redemption_enabled': True, 'shop_enabled': True,
                                   'lottery_enabled': False, 'version': 0, 'issuance_enabled': True,
                                   'expected_issuance_enabled': True}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertFalse(result.data['data']['usage_enabled'])
        with store_context(self.original):
            from engagement.points_usage import current_usage
            self.assertTrue(current_usage()[1]['lottery_enabled'])

    def test_setup_admin_bound_otp_and_sms_fallback_use_real_challenges(self):
        from django.utils import timezone
        from club_accounts.models import OtpCredential
        from club_accounts.otp import encrypt_secret, hotp
        secret = 'JBSWY3DPEHPK3PXP'
        with store_context(self.branch):
            OtpCredential.objects.create(account=self.owner, secret_ciphertext=encrypt_secret(secret))
        client = APIClient(HTTP_HOST='admin.esports.club')
        credentials = {'username': 'xinan/setup-owner', 'password': 'test-password'}
        challenge = client.post('/api/admin/auth/password-login', credentials, format='json')
        self.assertTrue(challenge.data['data']['requiresOTP'])
        response = client.post('/api/admin/auth/password-login', {**credentials,
            'otpChallenge': challenge.data['data']['otpChallenge'],
            'otpCode': hotp(secret, int(timezone.now().timestamp()) // 30)}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        client.credentials(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + response.data['data']['token'])
        self.assertEqual(client.get('/api/admin/dictionaries/').status_code, 200)
        with patch('users.password_login.send_verification_code') as sender:
            sms = client.post('/api/admin/auth/password-login', {**credentials, 'verificationMethod': 'sms'}, format='json')
        self.assertTrue(sms.data['data']['requiresSms'])
        response = client.post('/api/admin/auth/password-login', {**credentials,
            'smsChallenge': sms.data['data']['smsChallenge'], 'smsCode': sender.call_args.args[1]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
