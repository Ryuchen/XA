"""audition 公开接口单测：免登录凭 token 拉取试音活动信息。"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import AuditionLink, AuditionSignup

URL = '/api/audition/info'
EXCHANGE_URL = '/api/audition/exchange'
SIGNUP_URL = '/api/audition/signup'
MY_SIGNUP_URL = '/api/audition/my-signups'

User = get_user_model()


class AuditionPublicViewTest(APITestCase):
    def setUp(self):
        self.link = AuditionLink.objects.create(title='春季招募', remark='王者荣耀专场')

    def test_boss_token_returns_info_without_tokens(self):
        res = self.client.get(URL, {'token': self.link.boss_token, 'role': 'boss'})
        self.assertEqual(res.data['code'], 0)
        data = res.data['data']
        self.assertEqual(data['title'], '春季招募')
        self.assertEqual(data['remark'], '王者荣耀专场')
        self.assertEqual(data['role'], 'boss')
        # 绝不回传任何 token
        self.assertNotIn('boss_token', data)
        self.assertNotIn('provider_token', data)

    def test_provider_token_returns_info(self):
        res = self.client.get(URL, {'token': self.link.provider_token, 'role': 'provider'})
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data']['role'], 'provider')

    def test_role_token_mismatch_returns_404(self):
        # 用 boss_token 配 provider 角色应查不到
        res = self.client.get(URL, {'token': self.link.boss_token, 'role': 'provider'})
        self.assertEqual(res.data['code'], 404)

    def test_unknown_token_returns_404(self):
        res = self.client.get(URL, {'token': 'nope', 'role': 'boss'})
        self.assertEqual(res.data['code'], 404)

    def test_invalid_role_returns_400(self):
        res = self.client.get(URL, {'token': self.link.boss_token, 'role': 'admin'})
        self.assertEqual(res.data['code'], 400)

    def test_missing_params_returns_400(self):
        self.assertEqual(self.client.get(URL).data['code'], 400)
        self.assertEqual(self.client.get(URL, {'role': 'boss'}).data['code'], 400)

    def test_inactive_link_returns_410(self):
        self.link.is_active = False
        self.link.save(update_fields=['is_active'])
        res = self.client.get(URL, {'token': self.link.boss_token, 'role': 'boss'})
        self.assertEqual(res.data['code'], 410)

    def test_expired_link_returns_410(self):
        self.link.expire_at = timezone.now() - timedelta(hours=1)
        self.link.save(update_fields=['expire_at'])
        res = self.client.get(URL, {'token': self.link.boss_token, 'role': 'boss'})
        self.assertEqual(res.data['code'], 410)

    def test_future_expire_is_valid(self):
        self.link.expire_at = timezone.now() + timedelta(days=1)
        self.link.save(update_fields=['expire_at'])
        res = self.client.get(URL, {'token': self.link.boss_token, 'role': 'boss'})
        self.assertEqual(res.data['code'], 0)

    def test_accessible_without_authentication(self):
        # 未登录可访问（AllowAny），不应 401/403
        res = self.client.get(URL, {'token': self.link.provider_token, 'role': 'provider'})
        self.assertEqual(res.status_code, 200)


class AuditionExchangeViewTest(APITestCase):
    def setUp(self):
        self.boss = User.objects.create_user(
            username='boss_bound', password='pass1234', role=User.Role.CUSTOMER, nickname='张老板'
        )
        self.provider = User.objects.create_user(
            username='provider_bound', password='pass1234', role=User.Role.PROVIDER, nickname='小陪'
        )
        self.link = AuditionLink.objects.create(
            title='春季招募', boss_user=self.boss, provider_user=self.provider
        )

    def test_boss_token_exchanges_jwt_for_bound_user(self):
        res = self.client.post(EXCHANGE_URL, {'token': self.link.boss_token, 'role': 'boss'})
        self.assertEqual(res.data['code'], 0)
        data = res.data['data']
        self.assertTrue(data['token'])
        self.assertEqual(data['userInfo']['id'], str(self.boss.id))
        self.assertEqual(data['userInfo']['role'], 'customer')
        self.assertEqual(data['userInfo']['backendRole'], User.Role.CUSTOMER)

    def test_provider_token_exchanges_jwt_for_bound_user(self):
        res = self.client.post(EXCHANGE_URL, {'token': self.link.provider_token, 'role': 'provider'})
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data']['userInfo']['id'], str(self.provider.id))
        self.assertEqual(res.data['data']['userInfo']['role'], 'provider')

    def test_unbound_entry_returns_409(self):
        link = AuditionLink.objects.create(title='未绑定活动')
        res = self.client.post(EXCHANGE_URL, {'token': link.boss_token, 'role': 'boss'})
        self.assertEqual(res.data['code'], 409)

    def test_inactive_link_returns_410(self):
        self.link.is_active = False
        self.link.save(update_fields=['is_active'])
        res = self.client.post(EXCHANGE_URL, {'token': self.link.boss_token, 'role': 'boss'})
        self.assertEqual(res.data['code'], 410)

    def test_expired_link_returns_410(self):
        self.link.expire_at = timezone.now() - timedelta(hours=1)
        self.link.save(update_fields=['expire_at'])
        res = self.client.post(EXCHANGE_URL, {'token': self.link.provider_token, 'role': 'provider'})
        self.assertEqual(res.data['code'], 410)

    def test_unknown_token_returns_404(self):
        res = self.client.post(EXCHANGE_URL, {'token': 'nope', 'role': 'boss'})
        self.assertEqual(res.data['code'], 404)

    def test_invalid_params_returns_400(self):
        res = self.client.post(EXCHANGE_URL, {'token': self.link.boss_token, 'role': 'admin'})
        self.assertEqual(res.data['code'], 400)

    def test_disabled_bound_user_returns_403(self):
        self.boss.is_active = False
        self.boss.save(update_fields=['is_active'])
        res = self.client.post(EXCHANGE_URL, {'token': self.link.boss_token, 'role': 'boss'})
        self.assertEqual(res.data['code'], 403)

    def test_issued_token_authenticates_subsequent_request(self):
        res = self.client.post(EXCHANGE_URL, {'token': self.link.provider_token, 'role': 'provider'})
        token = res.data['data']['token']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        me = self.client.get('/api/users/escorts/me/')
        # provider 绑定用户可访问陪玩资料接口（非 401）
        self.assertNotEqual(me.status_code, 401)


class AuditionSignupViewTest(APITestCase):
    def setUp(self):
        self.provider = User.objects.create_user(
            username='provider_signup', password='pass1234', role=User.Role.PROVIDER, nickname='小陪'
        )
        self.customer = User.objects.create_user(
            username='customer_signup', password='pass1234', role=User.Role.CUSTOMER, nickname='张老板'
        )
        self.link = AuditionLink.objects.create(title='春季招募', provider_user=self.provider)

    def _payload(self, **overrides):
        data = {'token': self.link.provider_token, 'contact': '微信abc', 'game': '王者荣耀', 'remark': '可晚上试音'}
        data.update(overrides)
        return data

    def test_provider_signup_success(self):
        self.client.force_authenticate(self.provider)
        res = self.client.post(SIGNUP_URL, self._payload())
        self.assertEqual(res.data['code'], 0)
        signup = AuditionSignup.objects.get(link=self.link, applicant=self.provider)
        self.assertEqual(signup.status, AuditionSignup.Status.PENDING)
        self.assertEqual(signup.contact, '微信abc')
        self.assertEqual(signup.game, '王者荣耀')

    def test_requires_authentication(self):
        res = self.client.post(SIGNUP_URL, self._payload())
        self.assertEqual(res.status_code, 401)

    def test_non_provider_rejected_403(self):
        self.client.force_authenticate(self.customer)
        res = self.client.post(SIGNUP_URL, self._payload())
        self.assertEqual(res.data['code'], 403)
        self.assertFalse(AuditionSignup.objects.filter(applicant=self.customer).exists())

    def test_duplicate_signup_returns_409(self):
        self.client.force_authenticate(self.provider)
        first = self.client.post(SIGNUP_URL, self._payload())
        self.assertEqual(first.data['code'], 0)
        second = self.client.post(SIGNUP_URL, self._payload(contact='另一个'))
        self.assertEqual(second.data['code'], 409)
        self.assertEqual(AuditionSignup.objects.filter(link=self.link, applicant=self.provider).count(), 1)

    def test_unknown_token_returns_404(self):
        self.client.force_authenticate(self.provider)
        res = self.client.post(SIGNUP_URL, self._payload(token='nope'))
        self.assertEqual(res.data['code'], 404)

    def test_missing_token_returns_400(self):
        self.client.force_authenticate(self.provider)
        res = self.client.post(SIGNUP_URL, self._payload(token=''))
        self.assertEqual(res.data['code'], 400)

    def test_inactive_link_returns_410(self):
        self.link.is_active = False
        self.link.save(update_fields=['is_active'])
        self.client.force_authenticate(self.provider)
        res = self.client.post(SIGNUP_URL, self._payload())
        self.assertEqual(res.data['code'], 410)

    def test_expired_link_returns_410(self):
        self.link.expire_at = timezone.now() - timedelta(hours=1)
        self.link.save(update_fields=['expire_at'])
        self.client.force_authenticate(self.provider)
        res = self.client.post(SIGNUP_URL, self._payload())
        self.assertEqual(res.data['code'], 410)


class MyAuditionSignupViewTest(APITestCase):
    def setUp(self):
        self.provider = User.objects.create_user(
            username='provider_my', password='pass1234', role=User.Role.PROVIDER, nickname='小陪'
        )
        self.other = User.objects.create_user(
            username='provider_other', password='pass1234', role=User.Role.PROVIDER, nickname='别人'
        )
        self.link = AuditionLink.objects.create(title='春季招募', provider_user=self.provider)

    def test_requires_authentication(self):
        res = self.client.get(MY_SIGNUP_URL)
        self.assertEqual(res.status_code, 401)

    def test_returns_only_own_signups(self):
        AuditionSignup.objects.create(link=self.link, applicant=self.provider, contact='我的')
        AuditionSignup.objects.create(link=self.link, applicant=self.other, contact='别人的')
        self.client.force_authenticate(self.provider)
        res = self.client.get(MY_SIGNUP_URL)
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(len(res.data['data']), 1)
        item = res.data['data'][0]
        self.assertEqual(item['contact'], '我的')
        self.assertEqual(item['link_title'], '春季招募')
        self.assertEqual(item['status'], AuditionSignup.Status.PENDING)
        self.assertEqual(item['status_display'], '待审核')

    def test_empty_when_no_signup(self):
        self.client.force_authenticate(self.provider)
        res = self.client.get(MY_SIGNUP_URL)
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data'], [])

    def test_reflects_audit_result(self):
        signup = AuditionSignup.objects.create(
            link=self.link, applicant=self.provider, contact='我的',
            status=AuditionSignup.Status.REJECTED, audit_remark='资料不全',
        )
        self.client.force_authenticate(self.provider)
        res = self.client.get(MY_SIGNUP_URL)
        item = res.data['data'][0]
        self.assertEqual(item['id'], signup.id)
        self.assertEqual(item['status'], AuditionSignup.Status.REJECTED)
        self.assertEqual(item['audit_remark'], '资料不全')
