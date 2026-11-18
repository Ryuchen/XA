import os
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import IntegrityError, close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from club_accounts.models import ClubAccount, LegacyAccountMap
from stores.context import resolve_store, store_context
from users.models import BossRegistrationChallenge
from users.sms_transport import SmsUnavailable
from wallet.models import Wallet


@override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False,
                   LICENSE_ACCOUNT_ADMISSION_ENABLED=False)
class BossRegistrationTests(TestCase):
    def setUp(self):
        self.client = APIClient(HTTP_HOST='sl.esports.club')
        self.codes = []
        self.sender = patch('users.boss_registration.send_verification_code',
            side_effect=lambda phone, code: self.codes.append((phone, code))).start()
        self.addCleanup(patch.stopall)

    def start(self, phone='13800001111', client=None):
        return (client or self.client).post('/api/users/boss-registration/sms/', {'phone': phone}, format='json')

    def register(self, challenge, code, *, client=None, username='boss-new'):
        return (client or self.client).post('/api/users/boss-registration/', {
            'smsChallenge': challenge, 'smsCode': code, 'username': username,
            'password': 'Example-Password-2026', 'nickname': '测试老板'}, format='json')

    def test_sms_registers_unbound_boss_with_wallet_then_password_login(self):
        start = self.start()
        self.assertEqual(start.status_code, 200, start.data)
        self.assertNotIn(self.codes[-1][1], str(start.data))
        self.assertFalse(ClubAccount.objects.exists())
        result = self.register(start.data['data']['smsChallenge'], self.codes[-1][1])
        self.assertEqual(result.status_code, 201, result.data)
        account = ClubAccount.objects.get(username='boss-new')
        self.assertTrue(account.is_phone_verified)
        self.assertFalse(account.is_openid_bound)
        self.assertEqual(account.phone, '13800001111')
        self.assertTrue(account.check_password('Example-Password-2026'))
        mapping = LegacyAccountMap.objects.get(account=account)
        self.assertTrue(Wallet.objects.filter(user_id=mapping.legacy_user_id, account=account, balance=0).exists())
        self.assertEqual(RefreshToken(result.data['data']['refreshToken'])['auth_method'], 'password_sms')
        self.assertEqual(self.register(start.data['data']['smsChallenge'], self.codes[-1][1]).status_code, 400)
        with patch('users.password_login.send_verification_code', side_effect=lambda phone, code: self.codes.append((phone, code))):
            first = self.client.post('/api/users/password-login/', {'audience': 'boss', 'username': 'boss-new',
                'password': 'Example-Password-2026'}, format='json')
            self.assertTrue(first.data['data']['requiresSms'])
            logged_in = self.client.post('/api/users/password-login/', {'audience': 'boss', 'username': 'boss-new',
                'password': 'Example-Password-2026', 'smsChallenge': first.data['data']['smsChallenge'],
                'smsCode': self.codes[-1][1]}, format='json')
        self.assertEqual(logged_in.status_code, 200, logged_in.data)
        self.assertEqual(logged_in.data['data']['userInfo']['id'], str(account.pk))

    def test_wrong_code_expiry_cooldown_and_retry_after_username_conflict(self):
        challenge = self.start().data['data']['smsChallenge']
        code = self.codes[-1][1]
        self.assertEqual(self.start().status_code, 429)
        wrong = '000000' if code != '000000' else '999999'
        for _ in range(5):
            self.assertEqual(self.register(challenge, wrong).status_code, 400)
        self.assertEqual(self.register(challenge, code).data['errorCode'], 'SMS_CODE_EXPIRED')
        BossRegistrationChallenge.objects.update(sent_at=timezone.now()-timedelta(seconds=61))
        next_challenge = self.start().data['data']['smsChallenge']
        self.assertNotEqual(next_challenge, challenge)
        self.assertEqual(self.register(challenge, code).status_code, 400)
        ClubAccount.objects.create_account(username='boss-new', password='another-password', account_type='STAFF')
        conflict = self.register(next_challenge, self.codes[-1][1])
        self.assertEqual(conflict.data['errorCode'], 'USERNAME_IN_USE')
        self.assertEqual(self.register(next_challenge, self.codes[-1][1], username='boss-other').status_code, 201)

    def test_existing_phone_cannot_be_taken_and_delivery_failure_keeps_cooldown(self):
        first = self.start()
        phone = '13800001111'
        ClubAccount.objects.create_account(username='existing-boss', password='existing-password',
            account_type='BOSS', phone=phone)
        self.assertEqual(self.register(first.data['data']['smsChallenge'], self.codes[-1][1]).status_code, 409)
        self.assertEqual(self.start().status_code, 409)
        self.sender.side_effect = SmsUnavailable('delivery uncertain')
        self.assertEqual(self.start('13900001111').status_code, 503)
        self.assertEqual(self.start('13900001111').status_code, 429)
        self.assertFalse(ClubAccount.objects.filter(username='boss-new').exists())

    def test_creation_failure_rolls_back_account_wallet_and_sms_consumption(self):
        from django.contrib.auth import get_user_model
        challenge = self.start().data['data']['smsChallenge']
        code = self.codes[-1][1]
        with patch('users.boss_registration.LegacyAccountMap.objects.create', side_effect=IntegrityError('test')):
            self.assertEqual(self.register(challenge, code).status_code, 409)
        self.assertFalse(ClubAccount.objects.filter(username='boss-new').exists())
        self.assertFalse(get_user_model().objects.filter(username='boss-new').exists())
        self.assertFalse(BossRegistrationChallenge.objects.get(phone='13800001111').consumed)
        self.assertEqual(self.register(challenge, code).status_code, 201)


class BossRegistrationStoreTests(BossRegistrationTests):
    databases = {'default', 'store_b'}

    def test_identical_phone_and_challenges_are_store_isolated(self):
        from stores.tests_hosts import xinan_store
        xinan_store()
        other = APIClient(HTTP_HOST='xa.esports.club')
        a = self.start()
        a_code = self.codes[-1][1]
        b = self.start(client=other)
        b_code = self.codes[-1][1]
        self.assertNotEqual(a.data['data']['smsChallenge'], b.data['data']['smsChallenge'])
        self.assertEqual(self.register(a.data['data']['smsChallenge'], a_code, client=other).status_code, 400)
        self.assertEqual(self.register(a.data['data']['smsChallenge'], a_code).status_code, 201)
        self.assertEqual(self.register(b.data['data']['smsChallenge'], b_code, client=other).status_code, 201)


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated stores')
@override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False,
                   LICENSE_ACCOUNT_ADMISSION_ENABLED=True, LICENSE_SNAPSHOT_CACHE_ENABLED=False)
class BossRegistrationLicenseTests(TestCase):
    databases = {'default', 'store_b'}

    @skipUnless(os.environ.get('XA_LICENSE_QUOTA_FORMAL') == '1',
                'License 配额释放语义待最终联调（18-盘点 SaaS 授权）；断言保留')
    def test_quota_is_rechecked_atomically_before_account_creation(self):
        from stores.models import PlatformAccount, StoreLicenseCode, StoreLicenseContract
        from stores.tests_hosts import xinan_store
        store = xinan_store()
        actor = PlatformAccount.objects.create(username='registration-platform', phone='13800000000')
        contract = StoreLicenseContract.objects.create(store=store, amount_due_fen=0, created_by=actor, updated_by=actor)
        now = timezone.now()
        StoreLicenseCode.objects.create(store=store, contract=contract, code_digest='c' * 64, tier='BASIC',
            kind='INITIAL', starts_at=now-timedelta(days=2), ends_at=now+timedelta(days=1),
            activated_at=now-timedelta(days=1), activated_by_id=1, created_by=actor)
        with store_context(store), override_settings(LICENSE_ACCOUNT_ADMISSION_ENABLED=False):
            ClubAccount.objects.bulk_create([ClubAccount(username=f'quota-reg-{i}', account_type='BOSS',
                account_no=f'QUOTA-{i}') for i in range(500)])
        client, codes = APIClient(HTTP_HOST='xa.esports.club'), []
        with patch('users.boss_registration.send_verification_code', side_effect=lambda phone, code: codes.append(code)):
            start = client.post('/api/users/boss-registration/sms/', {'phone': '13800001111'}, format='json')
        body = {'smsChallenge': start.data['data']['smsChallenge'], 'smsCode': codes[-1],
                'username': 'quota-new', 'password': 'Example-Password-2026'}
        denied = client.post('/api/users/boss-registration/', body, format='json')
        self.assertEqual(denied.status_code, 403, denied.data)
        with store_context(store):
            self.assertFalse(ClubAccount.objects.filter(username='quota-new').exists())
            self.assertFalse(BossRegistrationChallenge.objects.get(phone='13800001111').consumed)
            ClubAccount.objects.filter(username='quota-reg-0').update(license_membership_active=False)
        accepted = client.post('/api/users/boss-registration/', body, format='json')
        self.assertEqual(accepted.status_code, 201, accepted.data)


@override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False, WECHAT_MOCK_LOGIN=False,
                   LICENSE_ACCOUNT_ADMISSION_ENABLED=False)
class BossRegistrationConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_sms_registration_and_wechat_registration_same_phone_create_one_account(self):
        from stores.db import transaction
        from users.views import WechatLoginView, _lock_wechat_phone
        client, codes = APIClient(HTTP_HOST='sl.esports.club'), []
        with patch('users.boss_registration.send_verification_code', side_effect=lambda phone, code: codes.append(code)):
            challenge = client.post('/api/users/boss-registration/sms/', {'phone': '13800001111'}, format='json')
        self.assertEqual(challenge.status_code, 200, challenge.data)
        nonce = challenge.data['data']['smsChallenge']
        barrier = Barrier(2, timeout=15)

        def sms_register():
            close_old_connections()
            try:
                barrier.wait()
                return APIClient(HTTP_HOST='sl.esports.club').post('/api/users/boss-registration/', {
                    'smsChallenge': nonce, 'smsCode': codes[0], 'username': 'race-boss',
                    'password': 'Example-Password-2026'}, format='json').status_code
            finally:
                close_old_connections()

        def wechat_register():
            close_old_connections()
            try:
                store = resolve_store()
                barrier.wait()
                with store_context(store), transaction.atomic():
                    _lock_wechat_phone('13800001111')
                    account, _, _ = WechatLoginView._resolve_customer('wx-race-proof', '13800001111', {
                        'username': 'wx-race-boss', 'account_type': 'BOSS', 'nickname': '微信老板',
                        'phone': '13800001111', 'is_phone_verified': True, 'is_openid_bound': True}, True)
                    return account.pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            sms = pool.submit(sms_register)
            wechat = pool.submit(wechat_register)
            self.assertIn(sms.result(timeout=30), (201, 409))
            account_id = wechat.result(timeout=30)
        accounts = ClubAccount.objects.filter(account_type='BOSS', phone='13800001111')
        self.assertEqual(accounts.count(), 1)
        self.assertEqual(accounts.get().pk, account_id)
        self.assertEqual(Wallet.objects.filter(account_id=account_id).count(), 1)
