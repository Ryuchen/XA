from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from console.models import AdminAuditLog
from coupons.models import VipMonthlyCutover
from stores.context import store_context
from stores.models import PlatformAccount, PlatformRoleGrant, Store
from stores.platform_auth import issue_tokens


@override_settings(PLATFORM_IDENTITY_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False)
class PlatformMonthlyCutoverBrowserTest(LiveServerTestCase):
    databases = {'default', 'store_new'}

    def test_platform_super_admin_confirms_cutover_in_store_setup(self):
        actor = PlatformAccount(username='monthly-ui-admin', phone='13800001111', is_phone_verified=True)
        actor.set_password('Synthetic-Platform-Password-2026!'); actor.save()
        PlatformRoleGrant.objects.create(account=actor, role='SUPER_ADMIN', scope_slug='*')
        token = issue_tokens(actor, 'SMS', actor.phone)[0]
        store = Store.objects.create(slug='monthly-ui-store', name='月券页面隔离店', entity_prefix='MU',
            currency_name='测试币', database_alias='store_new', status=Store.Status.CONFIGURING, initialized_at=timezone.now())
        client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        setup = client.get(f'/api/admin/platform/store-management/{store.pk}/setup')
        self.assertEqual(setup.data['code'], 0, setup.data)
        self.assertFalse(setup.data['data']['monthly_cutover']['configured'])
        confirmed = client.post(f'/api/admin/platform/store-management/{store.pk}/setup/monthly-cutover',
            {'confirm': True}, format='json')
        self.assertEqual(confirmed.data['code'], 0, confirmed.data)
        after = client.get(f'/api/admin/platform/store-management/{store.pk}/setup')
        self.assertEqual(after.data['code'], 0, after.data)
        self.assertTrue(after.data['data']['monthly_cutover']['configured'])
        with store_context(store):
            marker = VipMonthlyCutover.objects.get(pk=1)
            audit = AdminAuditLog.objects.get(resource='vip-monthly-cutover')
            self.assertEqual((marker.platform_actor_id, marker.operation_id), (str(actor.pk), audit.request_body['operation_id']))