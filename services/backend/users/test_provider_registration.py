from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image
from rest_framework.test import APITestCase

from club_accounts.models import ClubAccount, LegacyAccountMap, OtpCredential
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user
from users.models import EscortLevel, EscortProfile, ProviderRegistration
from users.provider_registration import decrypt_identity, validate_identity
from wallet.models import Transaction, Wallet, get_platform_wallet


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'], WECHAT_MOCK_LOGIN=False)
class ProviderRegistrationTests(APITestCase):
    url = '/api/provider-registration/'

    def setUp(self):
        self.folder = TemporaryDirectory(prefix='xa-registration-')
        self.addCleanup(self.folder.cleanup)
        setting = override_settings(MEDIA_ROOT=self.folder.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.level = EscortLevel.objects.create(name='审核测试等级', commission_rate=20, sort_order=1)

    def picture(self, name='proof.png'):
        data = BytesIO()
        Image.new('RGB', (64, 40), '#a2b5cc').save(data, 'PNG')
        return SimpleUploadedFile(name, data.getvalue(), content_type='image/png')

    def identity(self, sequence='001'):
        stem = '11010119900101' + sequence
        weights = (7,9,10,5,8,4,2,1,6,3,7,9,10,5,8,4,2)
        return stem + '10X98765432'[sum(int(n)*w for n,w in zip(stem, weights)) % 11]

    def payload(self, **changes):
        return {
            'username':'register_test', 'password':'test-password-only', 'phone':'13900001001',
            'real_name':'注册测试', 'id_number':self.identity(), 'display_name':'测试结单昵称',
            **{name:self.picture(name+'.png') for name in ('id_front','id_back','deposit_proof')},
            **changes,
        }

    def submit(self, **changes):
        response = self.client.post(self.url, self.payload(**changes), format='multipart')
        self.assertEqual(response.status_code, 201, response.data)
        return ProviderRegistration.objects.get(pk=response.data['data']['id'])

    def authenticate_admin(self, perms=None):
        user = make_console_user(perms=perms or ['registration:view','registration:audit'])
        account = get_or_create_account_for_legacy_user(user)
        from console.models import AdminMembership
        AdminMembership.objects.filter(user=user).update(account=account)
        token,_ = issue_account_tokens(account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer '+token)
        return account

    def approve(self, row, **changes):
        return self.client.post(
            f'/api/admin/provider-registrations/{row.pk}/approve/',
            {'revision':row.revision,'level_id':self.level.pk,'identity_confirmed':True,
             'deposit_confirmed':True,'deposit_paid':20000,**changes},format='json',
        )

    def test_pending_is_encrypted_private_and_cannot_login(self):
        row = self.submit()
        self.assertNotIn(self.identity(), row.id_number_ciphertext)
        self.assertEqual(decrypt_identity(row.id_number_ciphertext), self.identity())
        self.assertFalse(ClubAccount.objects.filter(username=row.username).exists())
        login = self.client.post('/api/users/account-login/',{'username':row.username,'password':'test-password-only'})
        self.assertNotEqual(login.data['code'],0)
        with override_settings(WECHAT_MOCK_LOGIN=True):
            self.assertNotEqual(self.client.post('/api/users/account-login/',{'username':row.username,'password':'test-password-only'}).data['code'],0)
        self.assertFalse(ClubAccount.objects.filter(username=row.username).exists())
        status = self.client.post(self.url+'status/',{'username':row.username,'password':'test-password-only'})
        self.assertEqual(status.data['code'],0)
        self.assertFalse(any(k in status.data['data'] for k in ('id_number','password_hash','id_front_url','phone','real_name')))
        self.assertEqual(self.client.get('/media/'+row.id_front.name).status_code,404)

    def test_missing_corrupt_and_oversize_photos_and_invalid_identity_are_rejected(self):
        bad_identity = self.payload(id_number=self.identity()[:-1]+'A')
        missing = self.payload()
        del missing['id_back']
        corrupt = self.payload(id_front=SimpleUploadedFile('id.png',b'not-an-image',content_type='image/png'))
        too_large = self.picture()
        too_large.size = 5*1024*1024+1
        from users.provider_registration import validate_picture
        from rest_framework.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            validate_picture(too_large)
        for data in (bad_identity, missing, corrupt):
            response = self.client.post(self.url,data,format='multipart')
            self.assertEqual(response.status_code,400)
        self.assertFalse(ProviderRegistration.objects.exists())
        with self.assertRaises(ValidationError):
            validate_identity('110101209001010015')

    def test_duplicate_username_phone_and_identity_cannot_create_extra_accounts(self):
        self.submit()
        for data in (
            self.payload(username='REGISTER_TEST',phone='13900001002',id_number=self.identity('002')),
            self.payload(username='other_test',id_number=self.identity('003')),
            self.payload(username='other_test',phone='13900001003'),
        ):
            self.assertEqual(self.client.post(self.url,data,format='multipart').status_code,409)
        self.assertEqual(ProviderRegistration.objects.count(),1)

    def test_approval_is_atomic_idempotent_and_requires_self_bound_otp(self):
        row = self.submit()
        self.authenticate_admin()
        before = get_platform_wallet().balance
        self.assertEqual(self.approve(row).data['code'],0)
        row.refresh_from_db()
        self.assertEqual(self.approve(row).data['code'],0)
        account = ClubAccount.objects.get(username=row.username)
        self.assertTrue(account.check_password('test-password-only'))
        self.assertEqual(LegacyAccountMap.objects.filter(account=account).count(),1)
        profile = EscortProfile.objects.get(account=account)
        self.assertEqual((profile.display_name,profile.is_verified,profile.deposit_mode,profile.deposit_paid),
                         ('测试结单昵称',True,'CASH',20000))
        self.assertEqual(Wallet.objects.get(account=account).balance,0)
        self.assertEqual(get_platform_wallet().balance-before,20000)
        self.assertEqual(Transaction.objects.filter(tx_type='DEPOSIT_INCOME').count(),1)
        self.assertEqual(Transaction.objects.filter(tx_type='DEPOSIT',wallet__account=account).count(),1)
        self.assertFalse(OtpCredential.objects.filter(account=account).exists())
        self.client.credentials()
        with override_settings(LOGIN_OTP_REQUIRED=True):
            response=self.client.post('/api/users/account-login/',{'username':row.username,'password':'test-password-only'})
            self.assertTrue(response.data['data']['requiresOTPSetup'])
            self.assertNotIn('token',response.data['data'])

    def test_audit_requires_read_and_audit_permissions(self):
        row=self.submit()
        for permissions in (['registration:view'],['registration:audit'],['escort:edit']):
            self.authenticate_admin(permissions)
            self.assertEqual(self.approve(row).status_code,403)
        self.assertFalse(ClubAccount.objects.filter(username=row.username).exists())

    def test_receipt_alone_never_confirms_payment_or_identity(self):
        row=self.submit()
        self.authenticate_admin()
        for changes in ({'identity_confirmed':False},{'deposit_confirmed':False},{'deposit_paid':19999}):
            self.assertEqual(self.approve(row,**changes).status_code,400)
        row.refresh_from_db()
        self.assertEqual(row.status,'PENDING')
        self.assertFalse(ClubAccount.objects.filter(username=row.username).exists())

    def test_detail_only_exposes_documents_to_authorized_staff(self):
        row=self.submit()
        self.authenticate_admin(['registration:view'])
        listing=self.client.get('/api/admin/provider-registrations/').data['data']['list'][0]
        self.assertEqual(listing['id_number'],'')
        self.assertEqual(listing['id_front_url'],'')
        detail=self.client.get(f'/api/admin/provider-registrations/{row.pk}/').data['data']
        self.assertEqual(detail['id_number'],self.identity())
        ticket_path=detail['id_front_url'].replace('http://testserver','')
        image=self.client.get(ticket_path)
        self.assertEqual(image.status_code,200)
        if image.streaming:
            self.assertTrue(b''.join(image.streaming_content))
        self.authenticate_admin(['escort:view'])
        self.assertEqual(self.client.get(f'/api/admin/provider-registrations/{row.pk}/').status_code,403)
        from console.models import AdminMembership
        # The signed image rechecks the original reader's current permissions.
        original=ClubAccount.objects.filter(username__startswith='user').order_by('id').first()
        AdminMembership.objects.filter(account=original).update(is_active=False)
        self.assertEqual(self.client.get(ticket_path).status_code,404)

    def test_rejection_requires_reason_and_resubmit_invalidates_stale_review(self):
        row=self.submit()
        self.authenticate_admin()
        action=f'/api/admin/provider-registrations/{row.pk}/reject/'
        self.assertEqual(self.client.post(action,{'revision':1,'review_note':''}).status_code,400)
        self.assertEqual(self.client.post(action,{'revision':1,'review_note':'身份证照片不清晰'}).data['code'],0)
        self.client.credentials()
        result=self.client.post(self.url+'status/',{'username':row.username,'password':'test-password-only'})
        self.assertEqual(result.data['data']['review_note'],'身份证照片不清晰')
        with self.captureOnCommitCallbacks(execute=True):
            response=self.client.post(self.url+'resubmit/',self.payload(display_name='补充后昵称'),format='multipart')
        self.assertEqual(response.data['code'],0,response.data)
        row.refresh_from_db()
        self.assertEqual((row.status,row.revision,row.review_note),('PENDING',2,''))
        self.authenticate_admin()
        self.assertEqual(self.approve(row,revision=1).status_code,409)
        self.assertEqual(self.approve(row).data['code'],0)

    def test_approval_failure_rolls_back_account_wallet_and_deposit(self):
        row=self.submit()
        self.authenticate_admin()
        before=get_platform_wallet().balance
        with patch('console.provider_registration.EscortProfile.objects.create',side_effect=RuntimeError('test failure')):
            with self.assertRaises(RuntimeError):
                self.approve(row)
        row.refresh_from_db()
        self.assertEqual(row.status,'PENDING')
        self.assertFalse(ClubAccount.objects.filter(username=row.username).exists())
        self.assertEqual(get_platform_wallet().balance,before)

    def test_password_protected_status_and_post_approval_resubmit_refused(self):
        row=self.submit()
        self.assertEqual(self.client.post(self.url+'status/',{'username':row.username,'password':'wrong-password'}).status_code,400)
        self.authenticate_admin()
        self.approve(row)
        self.client.credentials()
        self.assertEqual(self.client.post(self.url+'resubmit/',self.payload(),format='multipart').status_code,409)

    def test_earnings_registration_needs_no_cash_receipt_or_cash_credit(self):
        data=self.payload(deposit_mode='EARNINGS')
        del data['deposit_proof']
        response=self.client.post(self.url,data,format='multipart')
        self.assertEqual(response.status_code,201,response.data)
        row=ProviderRegistration.objects.get(pk=response.data['data']['id'])
        self.assertEqual(row.deposit_required,40000)
        self.assertFalse(row.deposit_proof)
        self.authenticate_admin()
        self.assertEqual(self.approve(row,deposit_paid=20000).status_code,400)
        result=self.approve(row,deposit_paid=0,deposit_confirmed=False)
        self.assertEqual(result.status_code,200,result.data)
        row.refresh_from_db()
        profile=EscortProfile.objects.get(account=row.account)
        self.assertEqual((profile.deposit_mode,profile.deposit_required,profile.deposit_paid),('EARNINGS',40000,0))
        self.assertFalse(Transaction.objects.filter(wallet__account=row.account).exists())
        self.assertEqual(get_platform_wallet().balance,0)

    def test_registration_view_permission_can_read_grade_options(self):
        self.authenticate_admin(['registration:view'])
        response=self.client.get('/api/admin/options/escort-levels/')
        self.assertEqual(response.status_code,200,response.data)


from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import close_old_connections, connections
from django.test import skipUnlessDBFeature
from rest_framework.test import APIClient, APITransactionTestCase


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'], WECHAT_MOCK_LOGIN=False)
@skipUnlessDBFeature('has_select_for_update')
class ConcurrentRegistrationApprovalTests(APITransactionTestCase):
    url = ProviderRegistrationTests.url
    setUp = ProviderRegistrationTests.setUp
    picture = ProviderRegistrationTests.picture
    identity = ProviderRegistrationTests.identity
    payload = ProviderRegistrationTests.payload
    submit = ProviderRegistrationTests.submit
    authenticate_admin = ProviderRegistrationTests.authenticate_admin

    def test_two_reviewers_create_only_one_account_and_one_cash_deposit(self):
        row = self.submit()
        self.authenticate_admin()
        token = self.client._credentials['HTTP_AUTHORIZATION']
        barrier = Barrier(2)
        url = f'/api/admin/provider-registrations/{row.pk}/approve/'
        payload = {'revision':1,'level_id':self.level.pk,'identity_confirmed':True,'deposit_confirmed':True,'deposit_paid':20000}
        def approve_once():
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION=token)
                barrier.wait(timeout=15)
                response = client.post(url,payload,format='json')
                return response.status_code,response.data
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(lambda _: approve_once(),range(2)))
        self.assertEqual([code for code,data in results],[200,200],results)
        self.assertEqual(ClubAccount.objects.filter(username=row.username).count(),1)
        self.assertEqual(EscortProfile.objects.filter(account__username=row.username).count(),1)
        self.assertEqual(Transaction.objects.filter(tx_type='DEPOSIT_INCOME').count(),1)
        self.assertEqual(get_platform_wallet().balance,20000)
