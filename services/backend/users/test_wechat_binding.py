"""Native mini-program login/binding contract and account ownership regressions."""

import json
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from django.test import override_settings
from rest_framework.test import APITestCase

from club_accounts.models import ClubAccount, LegacyAccountMap
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import User, make_user
from users.views import WechatLoginError, _wechat_get_phone


@override_settings(
    WECHAT_MOCK_LOGIN=False,
    WECHAT_MINIAPP_APPID='test-appid',
    WECHAT_MINIAPP_SECRET='test-secret',
    LEGACY_FORCE_AUTH_COMPAT=False,
)
class WechatBindingContractTest(APITestCase):
    LOGIN_URL = '/api/users/wechat-login/'
    BIND_URL = '/api/users/wechat-bind/'

    def setUp(self):
        self.session = patch('users.views._wechat_code2session', return_value='wx-current').start()
        self.phone = patch('users.views._wechat_get_phone', return_value='13800001111').start()
        self.addCleanup(patch.stopall)

    def account(self, **kwargs):
        user = make_user(role=User.Role.CUSTOMER, **kwargs)
        return get_or_create_account_for_legacy_user(user), user

    def authenticate(self, account):
        access, refresh = issue_account_tokens(account)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        return refresh

    def login(self, **data):
        return self.client.post(self.LOGIN_URL, {'code': 'fresh-code', **data}, format='json')

    def bind(self, **data):
        return self.client.post(self.BIND_URL, {'code': 'fresh-code', **data}, format='json')

    def test_unknown_wechat_check_does_not_create_account_or_token(self):
        # post_migrate may seed a platform operator; login must not add users.
        before_accounts = ClubAccount.objects.count()
        before_users = User.objects.count()
        response = self.login(checkOnly=True)
        self.assertEqual(response.data, {'code': 0, 'data': {
            'requiresBinding': True, 'bindingState': 'UNBOUND',
        }})
        self.assertEqual(ClubAccount.objects.count(), before_accounts)
        self.assertEqual(User.objects.count(), before_users)

    def test_check_does_not_consume_phone_authorization(self):
        self.login(checkOnly=True, phoneCode='phone-code')
        self.phone.assert_not_called()

    def test_existing_wechat_check_returns_tokens_and_verified_flags(self):
        account, _ = self.account(openid='wx-current', phone='13800001111', is_phone_verified=True)
        response = self.login(checkOnly=True, nickname='must-not-change')
        self.assertEqual(response.data['code'], 0)
        self.assertFalse(response.data['data']['requiresBinding'])
        info = response.data['data']['userInfo']
        self.assertEqual(info['id'], str(account.id))
        self.assertEqual(info['bindStatus'], 'openid')
        self.assertEqual(info['bindingState'], 'PHONE_BOUND')
        self.assertTrue(info['isWechatBound'])
        self.assertTrue(info['isPhoneVerified'])
        account.refresh_from_db()
        self.assertNotEqual(account.nickname, 'must-not-change')

    def test_expired_authorization_header_does_not_block_fresh_login(self):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer expired.invalid.token')
        self.assertEqual(self.login().data['code'], 0)

    def test_explicit_new_account_has_wechat_only_binding(self):
        info = self.login().data['data']['userInfo']
        self.assertEqual(info['bindingState'], 'WECHAT_BOUND')
        self.assertTrue(info['isWechatBound'])
        self.assertFalse(info['isPhoneVerified'])
        account = ClubAccount.objects.get(pk=info['id'])
        self.assertEqual((account.boss_type.name, account.boss_type.discount_rate), ('散客', 100))
        mapping = LegacyAccountMap.objects.get(account=account)
        self.assertEqual(User.objects.get(pk=mapping.legacy_user_id).boss_type_id, account.boss_type_id)

    def test_returning_vip_keeps_existing_type(self):
        from users.models import BossType
        vip = BossType.objects.create(name='VIP2', discount_rate=97)
        account, user = self.account(openid='wx-current', boss_type=vip)
        self.login()
        account.refresh_from_db(); user.refresh_from_db()
        self.assertEqual((account.boss_type_id, user.boss_type_id), (vip.pk, vip.pk))

    def test_existing_phone_cannot_replace_another_wechat(self):
        account, user = self.account(openid='wx-other', phone='13800001111', is_openid_bound=True)
        response = self.login(phoneCode='phone-code')
        self.assertEqual(response.data['errorCode'], 'ACCOUNT_CONFLICT')
        account.refresh_from_db()
        user.refresh_from_db()
        self.assertEqual(account.openid, 'wx-other')
        self.assertEqual(user.openid, 'wx-other')
        self.assertEqual(ClubAccount.objects.count(), 1)

    def test_disabled_legacy_phone_binding_rolls_back_lazy_migration(self):
        user = make_user(role=User.Role.CUSTOMER, openid=None, phone='13800001111', can_login=False)
        response = self.login(phoneCode='phone-code')
        self.assertEqual(response.data['errorCode'], 'ACCOUNT_DISABLED')
        user.refresh_from_db()
        self.assertIsNone(user.openid)
        self.assertFalse(user.is_phone_verified)
        self.assertFalse(LegacyAccountMap.objects.filter(legacy_user_id=user.pk).exists())

    def test_disabled_account_binding_keeps_original_fields(self):
        account, user = self.account(openid=None, phone='13800001111', can_login=False)
        response = self.login(phoneCode='phone-code')
        self.assertEqual(response.data['code'], 403)
        account.refresh_from_db()
        user.refresh_from_db()
        self.assertIsNone(account.openid)
        self.assertIsNone(user.openid)
        self.assertFalse(account.is_phone_verified)

    def test_existing_wechat_cannot_adopt_another_boss_phone(self):
        account, _ = self.account(openid='wx-current', phone='13900001111')
        other, _ = self.account(openid=None, phone='13800001111')
        response = self.login(phoneCode='phone-code')
        self.assertEqual(response.data['errorCode'], 'ACCOUNT_CONFLICT')
        account.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(account.phone, '13900001111')
        self.assertIsNone(other.openid)

    def test_account_without_legacy_mapping_can_login(self):
        account = ClubAccount.objects.create_account(
            username='without-mapping', account_type='BOSS', openid='wx-current',
        )
        self.assertEqual(self.login().data['data']['userInfo']['id'], str(account.pk))

    def test_missing_and_structured_login_codes_are_rejected(self):
        for code in (None, 123, [], {}, ' ', 'a' * 257):
            with self.subTest(code=code):
                response = self.client.post(self.LOGIN_URL, {'code': code}, format='json')
                self.assertEqual(response.data['errorCode'], 'WECHAT_CODE_INVALID')
        self.session.assert_not_called()

    def test_non_boolean_check_only_is_rejected(self):
        for value in ('false', 1, {}, []):
            with self.subTest(value=value):
                self.assertEqual(self.login(checkOnly=value).data['errorCode'], 'INVALID_INPUT')

    def test_raw_upstream_exception_is_not_exposed(self):
        self.session.side_effect = URLError('secret=test-secret&js_code=private-code')
        response = self.login()
        self.assertEqual(response.data['errorCode'], 'WECHAT_UNAVAILABLE')
        self.assertNotIn('test-secret', str(response.data))
        self.assertNotIn('private-code', str(response.data))

    def test_expired_wechat_code_has_machine_readable_error(self):
        self.session.side_effect = WechatLoginError('请重新登录', 'WECHAT_CODE_INVALID')
        response = self.login()
        self.assertEqual(response.data['errorCode'], 'WECHAT_CODE_INVALID')
        self.assertNotIn('data', response.data)

    def test_binding_requires_authentication(self):
        response = self.bind()
        self.assertEqual(response.status_code, 401)
        self.session.assert_not_called()

    def test_authenticated_unbound_boss_can_bind_and_repeat(self):
        account, user = self.account(openid=None)
        self.authenticate(account)
        first = self.bind()
        second = self.bind()
        self.assertEqual(first.data['data']['bindStatus'], 'bound')
        self.assertEqual(second.data['data']['bindStatus'], 'already_bound')
        account.refresh_from_db()
        user.refresh_from_db()
        self.assertEqual(account.openid, 'wx-current')
        self.assertEqual(user.openid, 'wx-current')
        self.assertTrue(self.client.get('/api/users/me/').data['data']['isWechatBound'])

    def test_authenticated_binding_never_overwrites_current_wechat(self):
        account, _ = self.account(openid='wx-other', is_openid_bound=True)
        self.authenticate(account)
        self.assertEqual(self.bind().data['errorCode'], 'ACCOUNT_CONFLICT')
        account.refresh_from_db()
        self.assertEqual(account.openid, 'wx-other')

    def test_authenticated_binding_rejects_wechat_owned_by_another_account(self):
        account, _ = self.account(openid=None)
        self.account(openid='wx-current')
        self.authenticate(account)
        self.assertEqual(self.bind().data['errorCode'], 'ACCOUNT_CONFLICT')
        account.refresh_from_db()
        self.assertIsNone(account.openid)

    def test_authenticated_binding_rejects_wechat_owned_by_unmigrated_user(self):
        account, _ = self.account(openid=None)
        make_user(role=User.Role.CUSTOMER, openid='wx-current')
        self.authenticate(account)
        self.assertEqual(self.bind().data['errorCode'], 'ACCOUNT_CONFLICT')

    def test_authenticated_phone_conflict_leaves_openid_unchanged(self):
        account, _ = self.account(openid=None)
        self.account(openid=None, phone='13800001111')
        self.authenticate(account)
        self.assertEqual(self.bind(phoneCode='phone-code').data['errorCode'], 'ACCOUNT_CONFLICT')
        account.refresh_from_db()
        self.assertIsNone(account.openid)
        self.assertFalse(account.is_phone_verified)

    def test_authenticated_phone_authorization_updates_account_and_legacy(self):
        account, user = self.account(openid='wx-current', is_openid_bound=True)
        self.authenticate(account)
        response = self.bind(phoneCode='phone-code')
        self.assertEqual(response.data['data']['userInfo']['bindingState'], 'PHONE_BOUND')
        account.refresh_from_db()
        user.refresh_from_db()
        self.assertEqual(account.phone, '13800001111')
        self.assertEqual(user.phone, '13800001111')
        self.assertTrue(user.is_phone_verified)

    def test_provider_cannot_bind_via_boss_endpoint(self):
        user = make_user(role=User.Role.PROVIDER, openid=None)
        account = get_or_create_account_for_legacy_user(user)
        self.authenticate(account)
        self.assertEqual(self.bind().data['errorCode'], 'ACCOUNT_ROLE_MISMATCH')
        self.session.assert_not_called()

    def test_disabled_authenticated_account_cannot_bind(self):
        account, _ = self.account(openid=None)
        self.authenticate(account)
        account.can_login = False
        account.save(update_fields=['can_login'])
        self.assertEqual(self.bind().status_code, 401)
        account.refresh_from_db()
        self.assertIsNone(account.openid)
        self.session.assert_not_called()

    def test_boss_cannot_manually_change_phone_or_forge_verification(self):
        account, _ = self.account(openid='wx-current', phone='13900001111', is_phone_verified=True)
        self.authenticate(account)
        response = self.client.patch('/api/users/me/', {
            'phone': '13800001111', 'is_phone_verified': True, 'nickname': 'must-not-change',
        }, format='json')
        self.assertEqual(response.data['errorCode'], 'PHONE_VERIFICATION_REQUIRED')
        account.refresh_from_db()
        self.assertEqual(account.phone, '13900001111')
        self.assertNotEqual(account.nickname, 'must-not-change')

    def test_manual_provider_phone_change_clears_verification(self):
        user = make_user(role=User.Role.PROVIDER, phone='13900001111', is_phone_verified=True)
        account = get_or_create_account_for_legacy_user(user)
        self.authenticate(account)
        response = self.client.patch('/api/users/me/', {'phone': '13800001111'}, format='json')
        self.assertEqual(response.data['code'], 0)
        account.refresh_from_db()
        user.refresh_from_db()
        self.assertFalse(account.is_phone_verified)
        self.assertFalse(user.is_phone_verified)

    def test_refresh_works_with_expired_header_but_rejects_disabled_account(self):
        account, _ = self.account(openid='wx-current')
        refresh = self.authenticate(account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer expired.invalid.token')
        response = self.client.post('/api/users/refresh/', {'refresh': refresh}, format='json')
        self.assertEqual(response.data['code'], 0)
        account.can_login = False
        account.save(update_fields=['can_login'])
        response = self.client.post('/api/users/refresh/', {'refresh': refresh}, format='json')
        self.assertEqual(response.status_code, 401)


@override_settings(WECHAT_MINIAPP_APPID='test-appid', WECHAT_MINIAPP_SECRET='test-secret')
class WechatUpstreamValidationTest(APITestCase):
    def test_wechat_invalid_code_is_mapped_without_raw_error(self):
        from users.views import _wechat_code2session
        for errcode in (40029, 40163):
            with self.subTest(errcode=errcode), patch('users.views._wechat_http_get', return_value={
                'errcode': errcode, 'errmsg': 'upstream private details',
            }):
                with self.assertRaises(WechatLoginError) as caught:
                    _wechat_code2session('fresh-code')
                self.assertEqual(caught.exception.error_code, 'WECHAT_CODE_INVALID')
                self.assertNotIn('private details', str(caught.exception))

    @patch('users.views._wechat_get_access_token', return_value='private-token')
    def test_phone_result_must_have_valid_phone_and_matching_watermark(self, _token):
        invalid_responses = [
            {'errcode': 0, 'phone_info': {}},
            {'errcode': 0, 'phone_info': {'purePhoneNumber': '13800001111', 'watermark': {'appid': 'other-app'}}},
            {'errcode': 40029, 'errmsg': 'invalid phone code'},
        ]
        for data in invalid_responses:
            with self.subTest(data=data):
                response = MagicMock()
                response.__enter__.return_value.read.return_value = json.dumps(data).encode()
                with patch('users.views.urllib.request.urlopen', return_value=response):
                    with self.assertRaises(WechatLoginError) as caught:
                        _wechat_get_phone('phone-code')
                    self.assertEqual(caught.exception.error_code, 'WECHAT_PHONE_CODE_INVALID')
