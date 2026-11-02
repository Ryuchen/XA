from django.test import override_settings
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership
from orders.tests.factories import make_console_user, make_provider, make_service
from users.registration_models import ProviderRegistration


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class EscortLevelWorkflowTest(APITestCase):
    def setUp(self):
        staff = make_console_user(['escort_level:view', 'escort_level:edit', 'escort_level:delete'])
        account = get_or_create_account_for_legacy_user(staff)
        AdminMembership.objects.filter(user=staff).update(account=account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])

    def test_create_edit_filter_and_delete_with_real_relations(self):
        url = '/api/admin/escort-levels/'
        created = self.client.post(url, {'name': '隔离等级', 'commission_rate': 20, 'sort_order': 1, 'is_active': True}, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        level_id = created.data['data']['id']
        provider = make_provider(level_id=level_id)
        service = make_service(required_level_id=level_id)
        registration = ProviderRegistration.objects.create(
            username='isolated_level_signup', phone='19900001234', password_hash='test',
            real_name='测试', id_number_ciphertext='test', id_number_hash='isolated-level-test',
            id_number_last4='1234', display_name='隔离报名', id_front='test-front.png',
            id_back='test-back.png', level_id=level_id,
        )
        changed = self.client.patch(f'{url}{level_id}/', {'commission_rate': 15, 'is_active': False}, format='json')
        self.assertEqual(changed.data['data']['commission_rate'], 15)
        filtered = self.client.get(url, {'is_active': 'false'})
        self.assertEqual([row['id'] for row in filtered.data['data']['list']], [level_id])
        impact = self.client.get(f'{url}{level_id}/deletion-impact/')
        self.assertEqual(impact.data['data'], {'id': level_id, 'name': '隔离等级', 'providers': 1, 'services': 1, 'registrations': 1})
        deleted = self.client.delete(f'{url}{level_id}/')
        self.assertEqual(deleted.status_code, 200, deleted.data)
        provider.escort_profile.refresh_from_db()
        service.refresh_from_db()
        registration.refresh_from_db()
        self.assertIsNone(provider.escort_profile.level_id)
        self.assertIsNone(service.required_level_id)
        self.assertIsNone(registration.level_id)
