from django.contrib.auth import get_user_model
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

    def test_staff_login_returns_club_account_tokens(self):
        response = self.client.post(
            '/api/admin/auth/login',
            {'username': self.staff.username, 'password': self.password},
            format='json',
        )

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
