from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from audition.models import AuditionLink, AuditionSignup
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from club_accounts.otp import hotp
from console.models import AdminMembership
from orders.tests.factories import make_console_user, make_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, LOGIN_OTP_REQUIRED=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class AuditionProviderBindingTest(APITestCase):
    def setUp(self):
        staff = make_console_user(['audition:view', 'audition:edit', 'audition:bind_provider', 'audition:bind_boss'])
        admin = get_or_create_account_for_legacy_user(staff)
        AdminMembership.objects.filter(user=staff).update(account=admin)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(admin)[0])
        self.accounts = []
        self.providers = []
        for index in range(2):
            user = make_user(role='PROVIDER', nickname='同名陪玩')
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            self.providers.append(user)
            self.accounts.append(account)
        self.created = self.client.post('/api/admin/audition-links/', {'title': '隔离陪玩入口'}, format='json').data['data']
        self.link_id = self.created['id']

    def client_for(self, account):
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        return client

    def bind(self, account_id, version=None):
        return self.client.post(f'/api/admin/audition-links/{self.link_id}/bind-provider/',
            {'account_id': account_id, 'expected_updated_at': version or self.created['updated_at']}, format='json')

    def test_real_signup_binding_rotation_and_other_provider_signup(self):
        original = self.created['provider_token']
        first = self.client_for(self.accounts[0])
        submitted = first.post('/api/audition/signup', {'token': original, 'contact': '隔离联系方式', 'game': '隔离游戏'}, format='json')
        self.assertEqual(submitted.data['code'], 0, submitted.data)
        signup_id = submitted.data['data']['id']
        result = self.bind(self.accounts[0].pk)
        self.assertEqual(result.data['code'], 0, result.data)
        state = result.data['data']
        self.assertEqual(state['provider_binding']['account_no'], self.accounts[0].account_no)
        self.assertNotEqual(state['provider_token'], original)
        self.assertEqual(state['boss_token'], self.created['boss_token'])
        self.assertTrue(AuditionSignup.objects.filter(pk=signup_id, link_id=self.link_id).exists())
        anonymous = APIClient()
        old = anonymous.get('/api/audition/info', {'token': original, 'role': 'provider'})
        self.assertEqual(old.data['code'], 404)
        exchanged = anonymous.post('/api/audition/exchange', {'token': state['provider_token'], 'role': 'provider'}, format='json')
        self.assertEqual(exchanged.data['data']['userInfo']['id'], str(self.accounts[0].pk))
        second = self.client_for(self.accounts[1])
        second_result = second.post('/api/audition/signup', {'token': state['provider_token'], 'contact': '另一测试账号', 'game': '隔离游戏'}, format='json')
        self.assertEqual(second_result.data['code'], 0, second_result.data)
        self.assertEqual(AuditionSignup.objects.filter(link_id=self.link_id).count(), 2)
        self.assertEqual(self.bind(self.accounts[1].pk).status_code, 409)
        self.assertEqual(AuditionLink.objects.get(pk=self.link_id).provider_account_id, self.accounts[0].pk)
        unbound = self.bind(None, state['updated_at'])
        self.assertEqual(unbound.data['data']['provider_binding']['kind'], 'unbound')
        self.assertEqual(anonymous.post('/api/audition/exchange', {'token': state['provider_token'], 'role': 'provider'}, format='json').data['code'], 404)
        existing = APIClient(); existing.credentials(HTTP_AUTHORIZATION='Bearer ' + exchanged.data['data']['token'])
        self.assertEqual(existing.get('/api/users/me/').data['code'], 0)

    def test_permissions_generic_write_and_otp_restriction(self):
        viewer = make_console_user(['audition:view', 'audition:edit'])
        account = get_or_create_account_for_legacy_user(viewer)
        AdminMembership.objects.filter(user=viewer).update(account=account)
        client = self.client_for(account)
        unbound = client.get(f'/api/admin/audition-links/{self.link_id}/').data['data']
        self.assertIsNotNone(unbound['provider_token'])
        self.assertEqual(client.post(f'/api/admin/audition-links/{self.link_id}/bind-provider/', {'account_id': self.accounts[0].pk, 'expected_updated_at': self.created['updated_at']}, format='json').status_code, 403)
        self.assertEqual(client.patch(f'/api/admin/audition-links/{self.link_id}/', {'provider_user': self.providers[0].pk}, format='json').status_code, 400)
        self.assertEqual(client.get('/api/admin/options/audition-providers/', {'keyword': self.accounts[0].account_no}).status_code, 403)
        bound = self.bind(self.accounts[0].pk).data['data']
        hidden = client.get(f'/api/admin/audition-links/{self.link_id}/').data['data']
        self.assertIsNone(hidden['provider_token'])
        self.assertIsNone(hidden['provider_url'])
        with self.settings(LOGIN_OTP_REQUIRED=True):
            result = APIClient().post('/api/audition/exchange', {'token': bound['provider_token'], 'role': 'provider'}, format='json')
            self.assertEqual(result.data['code'], 403)
            def otp_login(account, url):
                login = APIClient()
                initial = login.post(url, {'username': account.username, 'password': 'pass1234'}, format='json').data['data']
                code = hotp(initial['otpSecret'], int(timezone.now().timestamp()) // 30)
                response = login.post(url, {'username': account.username, 'password': 'pass1234', 'otpChallenge': initial['otpChallenge'], 'otpCode': code}, format='json').data
                self.assertEqual(response['code'], 0, response)
                login.credentials(HTTP_AUTHORIZATION='Bearer ' + response['data']['token'])
                return login
            verified_admin = otp_login(account, '/api/admin/auth/login')
            state = verified_admin.get(f'/api/admin/audition-links/{self.link_id}/').data['data']
            self.assertTrue(state['provider_binding']['otp_required'])
            client2 = otp_login(self.accounts[1], '/api/users/account-login/')
            self.assertEqual(client2.post('/api/audition/signup', {'token': bound['provider_token'], 'contact': '测试', 'game': '测试'}, format='json').data['code'], 0)
