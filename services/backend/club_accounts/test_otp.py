import base64
from datetime import timedelta
from unittest.mock import patch
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken
from club_accounts.models import OtpCredential,OtpLoginChallenge
from club_accounts.otp import hotp,encrypt_secret
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_provider,make_console_user
@override_settings(LOGIN_OTP_REQUIRED=True,WECHAT_MOCK_LOGIN=False,LEGACY_FORCE_AUTH_COMPAT=False)
class LoginOtpTest(APITestCase):
    def setUp(self):
        self.user=make_provider()
        self.account=get_or_create_account_for_legacy_user(self.user)
        admin_user=make_console_user()
        self.admin=get_or_create_account_for_legacy_user(admin_user)
        from console.models import AdminMembership
        AdminMembership.objects.filter(user=admin_user).update(account=self.admin)
        self.now=timezone.now()
    def post(self,data=None,admin=False):
        a=self.admin if admin else self.account
        with patch('club_accounts.otp.timezone.now',return_value=self.now):
            return self.client.post('/api/admin/auth/login' if admin else '/api/users/account-login/',{'username':a.username,'password':'pass1234',**(data or {})},format='json')
    def enroll(self,admin=False):
        first=self.post(admin=admin).data['data']
        self.assertTrue(first['requiresOTPSetup']);self.assertNotIn('token',first)
        secret=first['otpSecret']
        second=self.post({'otpChallenge':first['otpChallenge'],'otpCode':hotp(secret,int(self.now.timestamp())//30)},admin=admin).data
        self.assertEqual(second['code'],0,second)
        return secret,second['data'],first
    def test_rfc_hotp_totp_vectors(self):
        secret=base64.b32encode(b'12345678901234567890').decode()
        self.assertEqual(hotp(secret,0),'755224')
        self.assertEqual(hotp(secret,59//30,8),'94287082')
        self.assertEqual(hotp(secret,1111111109//30,8),'07081804')
        self.assertEqual(hotp(secret,20000000000//30,8),'65353130')
    def test_provider_enrollment_encryption_claims_and_backup_codes(self):
        secret,data,_=self.enroll()
        credential=OtpCredential.objects.get(account=self.account)
        self.assertNotIn(secret,credential.secret_ciphertext)
        self.assertEqual(len(data['recoveryCodes']),8)
        self.assertFalse(any(code in str(credential.recovery_hashes) for code in data['recoveryCodes']))
        self.assertTrue(AccessToken(data['token'])['otp_verified'])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer '+data['token'])
        self.assertEqual(self.client.get('/api/users/me/').status_code,200)
        self.assertEqual(self.client.post('/api/users/refresh/',{'refresh':data['refreshToken']}).data['code'],0)
    def test_admin_also_requires_binding_and_dynamic_code(self):
        _,data,_=self.enroll(admin=True)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer '+data['token'])
        self.assertEqual(self.client.get('/api/admin/auth/profile').data['code'],0)
    def test_wrong_password_does_not_issue_secret(self):
        response=self.post({'password':'bad'})
        self.assertEqual(response.data['code'],400)
        self.assertFalse(OtpLoginChallenge.objects.exists())
        self.assertFalse(OtpCredential.objects.exists())
    def test_old_access_and_refresh_cannot_bypass_otp(self):
        with override_settings(LOGIN_OTP_REQUIRED=False):
            token,refresh=issue_account_tokens(self.account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer '+token)
        self.assertEqual(self.client.get('/api/users/me/').status_code,401)
        self.client.credentials()
        self.assertEqual(self.client.post('/api/users/refresh/',{'refresh':refresh}).status_code,401)
    def test_code_and_challenge_replay_are_rejected_but_next_period_succeeds(self):
        secret,_,first=self.enroll()
        self.assertEqual(self.post({'otpChallenge':first['otpChallenge'],'otpCode':hotp(secret,int(self.now.timestamp())//30)}).data['code'],400)
        challenge=self.post().data['data']
        self.assertFalse(challenge['requiresOTPSetup']);self.assertNotIn('otpSecret',challenge)
        self.assertEqual(self.post({'otpChallenge':challenge['otpChallenge'],'otpCode':hotp(secret,int(self.now.timestamp())//30)}).data['code'],400)
        self.now+=timedelta(seconds=30)
        self.assertEqual(self.post({'otpChallenge':challenge['otpChallenge'],'otpCode':hotp(secret,int(self.now.timestamp())//30)}).data['code'],0)
    def test_recovery_code_works_once(self):
        _,data,_=self.enroll()
        for index in range(2):
            challenge=self.post().data['data']
            response=self.post({'otpChallenge':challenge['otpChallenge'],'otpCode':data['recoveryCodes'][0]})
            self.assertEqual(response.data['code'],0 if index==0 else 400)
        self.assertEqual(len(OtpCredential.objects.get(account=self.account).recovery_hashes),7)
    def test_attempt_limit_expiration_and_scope(self):
        first=self.post().data['data']
        for _ in range(5):self.assertEqual(self.post({'otpChallenge':first['otpChallenge'],'otpCode':'abcdef'}).data['code'],400)
        self.assertEqual(OtpLoginChallenge.objects.get(pk=first['otpChallenge']).attempts,5)
        self.assertEqual(self.post({'otpChallenge':first['otpChallenge'],'otpCode':hotp(first['otpSecret'],int(self.now.timestamp())//30)}).data['code'],400)
        other=self.post().data['data'];self.now+=timedelta(minutes=11)
        self.assertEqual(self.post({'otpChallenge':other['otpChallenge'],'otpCode':hotp(other['otpSecret'],int(self.now.timestamp())//30)}).data['code'],400)
        self.assertEqual(self.post({'otpChallenge':other['otpChallenge'],'otpCode':'123456'},admin=True).data['code'],400)
    def test_parallel_setup_challenge_cannot_replace_enrolled_secret(self):
        old=self.post().data['data']
        secret,_,_=self.enroll()
        res=self.post({'otpChallenge':old['otpChallenge'],'otpCode':hotp(old['otpSecret'],int(self.now.timestamp())//30)})
        self.assertEqual(res.data['code'],400)
        from club_accounts.otp import decrypt_secret
        self.assertEqual(decrypt_secret(OtpCredential.objects.get(account=self.account).secret_ciphertext),secret)
    def test_changed_password_invalidates_pending_setup(self):
        first=self.post().data['data']
        self.account.set_password('changed-password');self.account.save(update_fields=['password'])
        response=self.post({'password':'changed-password','otpChallenge':first['otpChallenge'],'otpCode':hotp(first['otpSecret'],int(self.now.timestamp())//30)})
        self.assertEqual(response.data['code'],400)


from concurrent.futures import ThreadPoolExecutor
import hashlib,threading
from django.db import close_old_connections,connections
from django.test import TransactionTestCase,skipUnlessDBFeature
from club_accounts.models import ClubAccount
@override_settings(LOGIN_OTP_REQUIRED=True)
@skipUnlessDBFeature('has_select_for_update')
class OtpReplayConcurrencyTest(TransactionTestCase):
    def test_same_dynamic_code_can_only_login_once_across_challenges(self):
        account=ClubAccount.objects.create_account(username='otp-race',password='test-password',account_type='PROVIDER')
        secret='JBSWY3DPEHPK3PXP'
        OtpCredential.objects.create(account=account,secret_ciphertext=encrypt_secret(secret))
        rows=[OtpLoginChallenge.objects.create(account=account,scope='provider',password_fingerprint=hashlib.sha256(account.password.encode()).hexdigest(),expires_at=timezone.now()+timedelta(minutes=5)) for _ in range(2)]
        barrier=threading.Barrier(2)
        code=hotp(secret,int(timezone.now().timestamp())//30)
        def verify(row):
            close_old_connections()
            try:
                from club_accounts.otp import otp_login_gate
                barrier.wait(timeout=5)
                result=otp_login_gate(account,{'otpChallenge':str(row.pk),'otpCode':code},'provider')
                return isinstance(result,dict) and result.get('otp_verified') is True
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            result=list(pool.map(verify,rows))
        self.assertEqual(sum(result),1)


from io import StringIO
import json
import secrets
from pathlib import Path
from tempfile import TemporaryDirectory
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from club_accounts.otp import recovery_hash


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class VirtualOtpProvisionTest(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.plan_path = Path(self.temp.name) / 'plan.json'
        self.output_path = Path(self.temp.name) / 'output.json'
        self.plan = []
        rows = []
        for n in range(1, 101):
            username = f'xa_virtual_{n:03d}'
            account = ClubAccount.objects.create_account(
                username=username, password='test-only-password', account_type='PROVIDER',
            )
            rows.append({'id': account.pk, 'username': username})
            self.plan.append({
                'username': username, 'password': 'test-only-password',
                'otpSecret': base64.b32encode(secrets.token_bytes(20)).decode(),
                'otpRecoveryCodes': ['11111111-11111111'],
            })
        self.plan_path.write_text(json.dumps(self.plan), encoding='utf-8')
        self.output_path.write_text(json.dumps(rows), encoding='utf-8')

    def run_command(self):
        call_command(
            'provision_virtual_provider_otp', plan=str(self.plan_path),
            output=str(self.output_path), stdout=StringIO(),
        )
        return json.loads(self.output_path.read_text(encoding='utf-8'))

    def test_repeat_preserves_cloud_codes_instead_of_original_plan_codes(self):
        first = self.run_command()
        second = self.run_command()
        self.assertEqual(first, second)
        self.assertEqual(OtpCredential.objects.count(), 100)
        for row in second:
            credential = OtpCredential.objects.get(account_id=row['id'])
            self.assertEqual(
                set(map(recovery_hash, row['otpRecoveryCodes'])),
                set(credential.recovery_hashes),
            )
            self.assertNotEqual(row['otpRecoveryCodes'], self.plan[0]['otpRecoveryCodes'])

    def test_repeat_removes_consumed_recovery_code(self):
        first = self.run_command()
        credential = OtpCredential.objects.get(account_id=first[0]['id'])
        used = first[0]['otpRecoveryCodes'][0]
        credential.recovery_hashes.remove(recovery_hash(used))
        credential.save(update_fields=['recovery_hashes'])
        second = self.run_command()
        self.assertNotIn(used, second[0]['otpRecoveryCodes'])
        self.assertEqual(len(second[0]['otpRecoveryCodes']), 7)
        self.assertEqual(first[1:], second[1:])

    def test_missing_valid_codes_refuses_export_without_changing_credentials(self):
        first = self.run_command()
        before = list(OtpCredential.objects.order_by('account_id').values_list(
            'secret_ciphertext', 'version', 'recovery_hashes',
        ))
        first[0]['otpRecoveryCodes'] = self.plan[0]['otpRecoveryCodes']
        self.output_path.write_text(json.dumps(first), encoding='utf-8')
        output_before = self.output_path.read_bytes()
        with self.assertRaisesMessage(CommandError, '交付清单缺少当前有效的恢复码'):
            self.run_command()
        self.assertEqual(output_before, self.output_path.read_bytes())
        self.assertEqual(before, list(OtpCredential.objects.order_by('account_id').values_list(
            'secret_ciphertext', 'version', 'recovery_hashes',
        )))
