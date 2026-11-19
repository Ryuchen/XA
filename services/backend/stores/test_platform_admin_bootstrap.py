"""Platform-created stores can bootstrap their first administrator without a shared password store."""
import os
from unittest import skipUnless
from unittest.mock import patch

from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens

from .context import resolve_store, store_context
from .models import Store, StorePlatformGrant
from .tests_foundation import admin


@override_settings(STORE_DATABASE_ALIASES=('store_b',), LOGIN_OTP_REQUIRED=False,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class PlatformAdminBootstrapTest(TransactionTestCase):
    databases = {'default', 'store_b'}

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_platform_initializes_store_with_admin_profile_and_sms_challenge(self):
        owner = admin(username='platform-bootstrap-owner', super_admin=True)
        StorePlatformGrant.objects.create(account_id=owner.pk)
        token, _ = issue_account_tokens(owner)
        original = resolve_store()
        platform = APIClient()
        platform.credentials(HTTP_X_STORE_SLUG=original.slug, HTTP_AUTHORIZATION='Bearer ' + token)
        password = 'Platform-Bootstrap-Password-2026!'
        response = platform.post('/api/stores/manage/', {
            'slug': 'platform-ui-branch', 'name': '平台接口初始化店', 'database_alias': 'store_b',
            'admin_username': 'platform-ui-admin', 'admin_password': password,
            'admin_phone': '13900000000', 'admin_nickname': '平台初始化管理员',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['data']['status'], 'CONFIGURING')
        self.assertNotIn(password, response.content.decode())
        branch = Store.objects.get(slug='platform-ui-branch')
        self.assertIsNotNone(branch.initialized_at)
        with store_context(branch):
            account = ClubAccount.objects.get(username='platform-ui-admin')
            self.assertEqual(account.nickname, '平台初始化管理员')
            self.assertEqual(account.phone, '13900000000')
            self.assertFalse(account.is_phone_verified)
            self.assertTrue(account.admin_membership.roles.filter(code='super_admin', is_active=True).exists())
        local = APIClient(HTTP_HOST='admin.esports.club')
        with patch('users.password_login.send_verification_code', lambda phone, code: None):
            challenge = local.post('/api/admin/auth/password-login', {
                'username': 'platform-ui-admin', 'password': password,
            }, format='json')
        self.assertEqual(challenge.status_code, 200, challenge.data)
        self.assertEqual(challenge.data['code'], 0, challenge.data)
        self.assertTrue(challenge.data['data']['requiresSms'])
        self.assertEqual(challenge.data['data']['maskedPhone'], '139****0000')

    def test_admin_profile_must_be_complete_before_creating_store(self):
        owner = admin(username='platform-validation-owner', super_admin=True)
        StorePlatformGrant.objects.create(account_id=owner.pk)
        token, _ = issue_account_tokens(owner)
        original = resolve_store()
        platform = APIClient()
        platform.credentials(HTTP_X_STORE_SLUG=original.slug, HTTP_AUTHORIZATION='Bearer ' + token)
        response = platform.post('/api/stores/manage/', {
            'slug': 'incomplete-admin-branch', 'name': '资料不完整店', 'database_alias': 'store_b',
            'admin_username': 'only-username',
        }, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Store.objects.filter(slug='incomplete-admin-branch').exists())
