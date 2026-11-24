"""A setup-only session against disposable SQLite exercises the real setup APIs."""
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.tokens import issue_account_tokens
from console.permissions import ALL_PERMISSION_SET
from stores.context import store_context
from stores.models import Store, StoreProvisioningClaim
from stores.tests_foundation import admin


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class SetupBrowserTest(LiveServerTestCase):
    def test_first_admin_sees_only_setup_and_can_save_capacity(self):
        store = Store.objects.get(slug='shenglang')
        store.status = Store.Status.CONFIGURING
        store.save(update_fields=['status'])
        with store_context(store):
            account = admin('setup-browser-owner', super_admin=True)
            account.phone = '13800001111'; account.is_phone_verified = True
            account.save(update_fields=['phone', 'is_phone_verified'])
            # The migrated bootstrap seeds super_admin with every permission;
            # the unopened store, not the role, restricts this session.
            role = account.admin_membership.roles.get(code='super_admin')
            role.permissions = sorted(ALL_PERMISSION_SET)
            role.save(update_fields=['permissions'])
            token = issue_account_tokens(account, auth_method='password_sms')[0]
        StoreProvisioningClaim.objects.create(store=store, admin_username=account.username, database_fingerprint='a' * 64)
        client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        rules = client.get('/api/admin/config/provider-capacity/')
        self.assertEqual(rules.data['code'], 0, rules.data)
        saved = client.put('/api/admin/config/provider-capacity/',
            {'max_concurrent_orders': rules.data['data']['max_concurrent_orders'],
             'version': rules.data['data']['version']}, format='json')
        self.assertEqual(saved.data['code'], 0, saved.data)
        self.assertEqual(saved.data['msg'], '接单名额已保存')
        confirmed = client.get('/api/admin/config/provider-capacity/')
        self.assertEqual(confirmed.data['data']['version'], rules.data['data']['version'] + 1)
        blocked = client.get('/api/admin/wallets/')
        self.assertEqual(blocked.status_code, 403)