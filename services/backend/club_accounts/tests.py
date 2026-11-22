from django.contrib.auth import get_user_model
from django.db import DatabaseError, InterfaceError, connection
from django.test import override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
from rest_framework.test import APITestCase

from console.models import AdminMembership, AdminRole

from .models import ClubAccount, LegacyAccountMap
from .tokens import (
    ACCOUNT_TOKEN_KIND,
    issue_account_tokens,
    refresh_account_access_token,
)


class ClubAccountAuthenticationTest(APITestCase):
    def create_account(self, account_type, username='club-user', password='pass1234'):
        account = ClubAccount.objects.create_account(
            username=username,
            password=password,
            account_type=account_type,
        )
        legacy_user = get_user_model().objects.create_user(
            username=f'legacy-{username}',
            password=None,
        )
        LegacyAccountMap.objects.create(
            legacy_user_id=legacy_user.id,
            account=account,
        )
        return account, legacy_user

    def test_issue_tokens_contains_only_club_account_identity(self):
        account, _ = self.create_account(ClubAccount.AccountType.PROVIDER)

        access_token, refresh_token = issue_account_tokens(account)
        access = AccessToken(access_token)
        refresh = RefreshToken(refresh_token)

        self.assertEqual(access['token_kind'], ACCOUNT_TOKEN_KIND)
        self.assertEqual(access['account_id'], account.id)
        self.assertEqual(access['account_type'], ClubAccount.AccountType.PROVIDER)
        self.assertNotIn('user_id', access)
        self.assertEqual(refresh['account_id'], account.id)

    def test_refresh_preserves_club_account_claims(self):
        account, _ = self.create_account(ClubAccount.AccountType.BOSS)
        _, refresh_token = issue_account_tokens(account)

        refreshed = AccessToken(refresh_account_access_token(refresh_token))

        self.assertEqual(refreshed['token_kind'], ACCOUNT_TOKEN_KIND)
        self.assertEqual(refreshed['account_id'], account.id)
        self.assertEqual(refreshed['account_type'], ClubAccount.AccountType.BOSS)

    def test_business_token_authenticates_with_request_account(self):
        account, _ = self.create_account(ClubAccount.AccountType.PROVIDER)
        access_token, _ = issue_account_tokens(account)

        response = self.client.get(
            '/api/users/me/',
            HTTP_AUTHORIZATION=f'Bearer {access_token}',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['id'], str(account.id))
        self.assertEqual(response.data['data']['role'], 'provider')

    def test_account_and_legacy_lookup_outages_return_retryable_503(self):
        account, _ = self.create_account(ClubAccount.AccountType.PROVIDER)
        access_token, _ = issue_account_tokens(account)
        headers = {'HTTP_AUTHORIZATION': f'Bearer {access_token}'}
        for model, error in ((ClubAccount, InterfaceError), (LegacyAccountMap, DatabaseError),
                             (get_user_model(), InterfaceError)):
            def disconnect(execute, sql, params, many, context):
                if sql.lstrip().upper().startswith('SELECT') and model._meta.db_table in sql.lower():
                    raise error('isolated account lookup failed')
                return execute(sql, params, many, context)

            with self.subTest(model=model.__name__), connection.execute_wrapper(disconnect):
                response = self.client.get('/api/users/me/', **headers)
                self.assertEqual(response.status_code, 503, response.data)
                self.assertNotIn('data', response.data)
                self.assertNotIn('WWW-Authenticate', response)
                self.assertEqual(response.data['errors']['detail'].code, 'club_account_auth_unavailable')
        restored = self.client.get('/api/users/me/', **headers)
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.data['data']['id'], str(account.id))

    def test_store_account_refresh_recovers_after_lookup_disconnect(self):
        account, _ = self.create_account(ClubAccount.AccountType.PROVIDER)
        _, refresh_token = issue_account_tokens(account)

        def disconnect(execute, sql, params, many, context):
            if sql.lstrip().upper().startswith('SELECT') and ClubAccount._meta.db_table in sql.lower():
                raise InterfaceError('isolated refresh lookup failed')
            return execute(sql, params, many, context)

        with connection.execute_wrapper(disconnect):
            unavailable = self.client.post('/api/users/refresh/', {'refresh': refresh_token}, format='json')
        self.assertEqual(unavailable.status_code, 503, unavailable.data)
        self.assertNotIn('data', unavailable.data)
        restored = self.client.post('/api/users/refresh/', {'refresh': refresh_token}, format='json')
        self.assertEqual(restored.status_code, 200, restored.data)
        self.assertEqual(AccessToken(restored.data['data']['token'])['account_id'], account.pk)

    def test_legacy_django_jwt_is_rejected(self):
        _, legacy_user = self.create_account(ClubAccount.AccountType.PROVIDER)
        legacy_access = RefreshToken.for_user(legacy_user).access_token

        response = self.client.get(
            '/api/users/me/',
            HTTP_AUTHORIZATION=f'Bearer {legacy_access}',
        )

        self.assertEqual(response.status_code, 401)

    def test_disabled_account_token_is_rejected(self):
        account, _ = self.create_account(ClubAccount.AccountType.PROVIDER)
        access_token, _ = issue_account_tokens(account)
        account.can_login = False
        account.save(update_fields=['can_login'])

        response = self.client.get(
            '/api/users/me/',
            HTTP_AUTHORIZATION=f'Bearer {access_token}',
        )

        self.assertEqual(response.status_code, 401)


class ConsoleClubAccountAuthenticationTest(APITestCase):
    def setUp(self):
        self.password = 'pass1234'
        self.staff = ClubAccount.objects.create_account(
            username='console-staff',
            password=self.password,
            account_type=ClubAccount.AccountType.STAFF,
        )
        legacy_user = get_user_model().objects.create_user(
            username='legacy-console-staff',
            password=None,
        )
        LegacyAccountMap.objects.create(
            legacy_user_id=legacy_user.id,
            account=self.staff,
        )
        role = AdminRole.objects.create(
            name='测试管理员',
            code='test_admin',
            permissions=['dashboard:view'],
        )
        membership = AdminMembership.objects.create(
            user=legacy_user,
            account=self.staff,
        )
        membership.roles.add(role)

    @override_settings(LOGIN_OTP_REQUIRED=False)
    def test_console_refresh_preserves_session_on_store_permission_lookup_failure(self):
        _, refresh_token = issue_account_tokens(self.staff)

        def disconnect(execute, sql, params, many, context):
            if sql.lstrip().upper().startswith('SELECT') and AdminMembership._meta.db_table in sql.lower():
                raise InterfaceError('isolated console permission lookup failed')
            return execute(sql, params, many, context)

        payload = {'refresh': refresh_token}
        with connection.execute_wrapper(disconnect):
            unavailable = self.client.post('/api/admin/auth/refresh', payload, format='json')
        self.assertEqual(unavailable.status_code, 503, unavailable.data)
        self.assertNotIn('data', unavailable.data)
        restored = self.client.post('/api/admin/auth/refresh', payload, format='json')
        self.assertEqual(restored.status_code, 200, restored.data)
        self.assertEqual(AccessToken(restored.data['data']['token'])['account_id'], self.staff.pk)

    @override_settings(LOGIN_OTP_REQUIRED=True)
    def test_staff_login_returns_club_account_tokens(self):
        from .models import OtpCredential
        from .otp import encrypt_secret, hotp
        secret = 'JBSWY3DPEHPK3PXP'
        OtpCredential.objects.create(account=self.staff, secret_ciphertext=encrypt_secret(secret))
        first = self.client.post(
            '/api/admin/auth/login',
            {'username': self.staff.username, 'password': self.password},
            format='json',
        )
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.data['data']['requiresOTP'])
        response = self.client.post('/api/admin/auth/login', {
            'username': self.staff.username, 'password': self.password,
            'otpChallenge': first.data['data']['otpChallenge'],
            'otpCode': hotp(secret, int(timezone.now().timestamp()) // 30),
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['code'], 0)
        access = AccessToken(response.data['data']['token'])
        self.assertEqual(access['token_kind'], ACCOUNT_TOKEN_KIND)
        self.assertEqual(access['account_id'], self.staff.id)
        self.assertNotIn('user_id', access)

    def test_boss_account_cannot_login_to_console(self):
        ClubAccount.objects.create_account(
            username='boss-account',
            password=self.password,
            account_type=ClubAccount.AccountType.BOSS,
        )

        response = self.client.post(
            '/api/admin/auth/login',
            {'username': 'boss-account', 'password': self.password},
            format='json',
        )

        self.assertEqual(response.data['code'], 400)
