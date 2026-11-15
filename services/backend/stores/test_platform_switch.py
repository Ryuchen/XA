"""Authorized platform admins can switch stores without authenticating again."""
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens

from .context import resolve_store, store_context
from .models import Store, StorePlatformGrant
from .tests_foundation import admin


@override_settings(STORE_DATABASE_ALIASES=('store_b',), LOGIN_OTP_REQUIRED=False,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class PlatformStoreSwitchTest(TransactionTestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.owner = admin(username='platform-switch-owner', super_admin=True)
        self.grant = StorePlatformGrant.objects.create(account_id=self.owner.pk)
        self.original = resolve_store()
        self.branch = Store.objects.create(
            slug='switch-branch', name='免密切换店', database_alias='store_b',
            status=Store.Status.ACTIVE, initialized_at=timezone.now(),
        )
        self.token, _ = issue_account_tokens(self.owner)

    def api_client(self, token, slug):
        client = APIClient()
        client.credentials(HTTP_X_STORE_SLUG=slug, HTTP_AUTHORIZATION='Bearer ' + token)
        return client

    def test_platform_switch_issues_target_session_and_preserves_identity(self):
        with store_context(self.branch):
            # Overlapping IDs in another store must never change the verified identity.
            ClubAccount.objects.create_account(
                username='unrelated-target-user', account_type=ClubAccount.AccountType.BOSS,
                id=self.owner.pk, account_no='OVERLAP-TARGET-USER',
            )
        switched = self.api_client(self.token, self.original.slug).post('/api/stores/switch/', {
            'store_slug': self.branch.slug,
        }, format='json')
        self.assertEqual(switched.status_code, 200, switched.data)
        data = switched.data['data']
        self.assertEqual(data['store_slug'], self.branch.slug)
        self.assertEqual(data['profile']['username'], self.owner.username)
        claims = AccessToken(data['token'])
        self.assertEqual(claims['store_slug'], self.branch.slug)
        self.assertEqual(claims['auth_method'], 'platform')
        local = self.api_client(data['token'], self.branch.slug)
        current = local.get('/api/stores/current/')
        self.assertEqual(current.status_code, 200, current.data)
        self.assertEqual(current.data['data']['slug'], self.branch.slug)
        self.assertTrue(current.data['data']['can_manage_stores'])
        available = local.get('/api/stores/available/')
        self.assertEqual(available.status_code, 200, available.data)
        self.assertIn(self.original.slug, {row['slug'] for row in available.data['data']['results']})
        profile = local.get('/api/admin/auth/profile')
        self.assertEqual(profile.status_code, 200, profile.data)
        self.assertEqual(profile.data['data']['username'], self.owner.username)
        self.assertTrue(profile.data['data']['is_superuser'])
        self.assertEqual(local.get('/api/admin/dashboard/stats').status_code, 200)
        returned = local.post('/api/stores/switch/', {'store_slug': self.original.slug}, format='json')
        self.assertEqual(returned.status_code, 200, returned.data)
        self.assertEqual(returned.data['data']['store_slug'], self.original.slug)
        self.grant.is_active = False
        self.grant.save(update_fields=['is_active'])
        self.assertEqual(local.get('/api/stores/current/').status_code, 401)

    def test_switch_requires_grant_and_initialized_target(self):
        ordinary = admin(username='ordinary-switch-user', super_admin=False)
        token, _ = issue_account_tokens(ordinary)
        response = self.api_client(token, self.original.slug).post('/api/stores/switch/', {
            'store_slug': self.branch.slug,
        }, format='json')
        self.assertEqual(response.status_code, 403, response.data)
        self.branch.status = Store.Status.PROVISIONING
        self.branch.initialized_at = None
        self.branch.save(update_fields=['status', 'initialized_at'])
        rejected = self.api_client(self.token, self.original.slug).post('/api/stores/switch/', {
            'store_slug': self.branch.slug,
        }, format='json')
        self.assertEqual(rejected.status_code, 400, rejected.data)
