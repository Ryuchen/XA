from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from audition.models import AuditionLink
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership
from orders.tests.factories import make_console_user, make_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, LOGIN_OTP_REQUIRED=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class AuditionBossBindingTest(APITestCase):
    def setUp(self):
        staff = make_console_user(['audition:view', 'audition:edit', 'audition:bind_boss'])
        account = get_or_create_account_for_legacy_user(staff)
        AdminMembership.objects.filter(user=staff).update(account=account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        self.first = make_user(nickname='同名老板')
        self.second = make_user(nickname='同名老板')
        self.boss = get_or_create_account_for_legacy_user(self.first)
        self.other = get_or_create_account_for_legacy_user(self.second)
        link_legacy_relations(self.first, self.boss)
        link_legacy_relations(self.second, self.other)
        created = self.client.post('/api/admin/audition-links/', {'title': '隔离入口'}, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.link_id = created.data['data']['id']
        self.original_boss_token = created.data['data']['boss_token']
        self.provider_token = created.data['data']['provider_token']
        self.version = created.data['data']['updated_at']

    def bind(self, account_id, version=None):
        return self.client.post(f'/api/admin/audition-links/{self.link_id}/bind-boss/',
                                {'account_id': account_id, 'expected_updated_at': version or self.version}, format='json')

    def exchange(self, token):
        return APIClient().post('/api/audition/exchange', {'token': token, 'role': 'boss'}, format='json').data

    def test_bind_rotate_exchange_unbind_and_stale_update(self):
        self.assertEqual(self.exchange(self.original_boss_token)['code'], 409)
        bound = self.bind(self.boss.pk)
        self.assertEqual(bound.data['code'], 0, bound.data)
        self.assertEqual(bound.data['data']['boss_binding']['account_no'], self.boss.account_no)
        new_token = bound.data['data']['boss_token']
        self.assertNotEqual(new_token, self.original_boss_token)
        self.assertEqual(bound.data['data']['provider_token'], self.provider_token)
        self.assertEqual(self.exchange(self.original_boss_token)['code'], 404)
        exchanged = self.exchange(new_token)
        self.assertEqual(exchanged['code'], 0, exchanged)
        self.assertEqual(exchanged['data']['userInfo']['id'], str(self.boss.pk))
        stale = self.bind(self.other.pk)
        self.assertEqual(stale.status_code, 409)
        link = AuditionLink.objects.get(pk=self.link_id)
        self.assertEqual(link.boss_account_id, self.boss.pk)
        unbound = self.bind(None, bound.data['data']['updated_at'])
        self.assertEqual(unbound.data['data']['boss_binding']['kind'], 'unbound')
        self.assertEqual(self.exchange(new_token)['code'], 404)
        self.assertEqual(self.exchange(unbound.data['data']['boss_token'])['code'], 409)
        self.assertEqual(unbound.data['data']['provider_token'], self.provider_token)
        existing_session = APIClient()
        existing_session.credentials(HTTP_AUTHORIZATION='Bearer ' + exchanged['data']['token'])
        self.assertEqual(existing_session.get('/api/users/me/').data['code'], 0)

    def test_permission_and_no_legacy_field_bypass(self):
        editor = make_console_user(['audition:view', 'audition:edit'])
        account = get_or_create_account_for_legacy_user(editor)
        AdminMembership.objects.filter(user=editor).update(account=account)
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        denied = client.post(f'/api/admin/audition-links/{self.link_id}/bind-boss/', {'account_id': self.boss.pk, 'expected_updated_at': self.version}, format='json')
        self.assertEqual(denied.status_code, 403)
        view = client.get(f'/api/admin/audition-links/{self.link_id}/')
        self.assertIsNone(view.data['data']['boss_token'])
        self.assertIsNone(view.data['data']['boss_url'])
        bypass = client.patch(f'/api/admin/audition-links/{self.link_id}/', {'boss_user': self.first.pk}, format='json')
        self.assertEqual(bypass.status_code, 400)
        self.assertIsNone(AuditionLink.objects.get(pk=self.link_id).boss_account_id)
        self.assertEqual(client.get('/api/admin/options/audition-bosses/', {'keyword': self.boss.account_no}).status_code, 403)

    def test_invalid_disabled_or_non_boss_account(self):
        self.boss.can_login = False
        self.boss.save(update_fields=['can_login'])
        self.assertEqual(self.bind(self.boss.pk).status_code, 400)
        provider = make_user(role='PROVIDER')
        account = get_or_create_account_for_legacy_user(provider)
        self.assertEqual(self.bind(account.pk).status_code, 400)

    def test_entry_status_and_placeholder_configuration(self):
        link = AuditionLink.objects.get(pk=self.link_id)
        link.expire_at = timezone.now() - timedelta(seconds=1)
        link.save(update_fields=['expire_at', 'updated_at'])
        result = self.client.get(f'/api/admin/audition-links/{self.link_id}/')
        self.assertEqual(result.data['data']['entry_status'], 'EXPIRED')
        with self.settings(AUDITION_BOSS_LINK_BASE_URL='https://h5.example.com/audition'):
            result = self.client.get(f'/api/admin/audition-links/{self.link_id}/')
            self.assertTrue(result.data['data']['boss_url_placeholder'])
        link.is_active = False
        link.save(update_fields=['is_active', 'updated_at'])
        self.assertEqual(self.client.get(f'/api/admin/audition-links/{self.link_id}/').data['data']['entry_status'], 'DISABLED')
