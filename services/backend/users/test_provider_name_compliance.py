from io import BytesIO
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import ContentComplianceReceipt
from orders.tests.factories import make_console_user, make_provider
from users.models import ProviderRegistration


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class ProviderNameComplianceTests(APITestCase):
    def setUp(self):
        self.user = make_provider(display_name='原昵称')
        self.account = get_or_create_account_for_legacy_user(self.user)
        self.account.nickname = '原昵称'
        self.account.save(update_fields=['nickname'])
        self.profile = self.user.escort_profile
        self.profile.account = self.account
        self.profile.save(update_fields=['account'])
        token, _ = issue_account_tokens(self.account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def test_self_service_reserved_name_is_rejected_and_legal_change_is_receipted(self):
        response = self.client.patch('/api/users/escorts/me/', {'display_name': '售后小安'}, format='json')
        self.assertEqual(response.status_code, 400, response.content)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.display_name, '原昵称')
        self.assertFalse(ContentComplianceReceipt.objects.exists())
        response = self.client.patch('/api/users/escorts/me/', {'display_name': '合规新昵称'}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        receipt = ContentComplianceReceipt.objects.get(
            content_type='escort_profile', object_id=self.profile.pk, field_name='PROVIDER_DISPLAY_NAME',
        )
        self.assertEqual(receipt.actor_account_id, self.account.pk)
        self.assertEqual(receipt.actor_label, f'ACCOUNT:{self.account.pk}')
        self.assertEqual(receipt.matched_rule_codes, [])
        unchanged = self.client.patch('/api/users/escorts/me/', {'display_name': '合规新昵称'}, format='json')
        self.assertEqual(unchanged.status_code, 200, unchanged.content)
        self.assertEqual(ContentComplianceReceipt.objects.count(), 1)

    def test_receipts_after_rename_never_return_old_or_new_display_name_text(self):
        for display_name in ('甲店合规昵称', '乙店合规昵称'):
            response = self.client.patch('/api/users/escorts/me/', {'display_name': display_name}, format='json')
            self.assertEqual(response.status_code, 200, response.content)
        receipts = ContentComplianceReceipt.objects.filter(
            content_type='escort_profile', object_id=self.profile.pk, field_name='PROVIDER_DISPLAY_NAME',
        )
        self.assertEqual(receipts.count(), 2)
        self.assertEqual({row.actor_label for row in receipts}, {f'ACCOUNT:{self.account.pk}'})

        staff = make_console_user(['content:view'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        reader = APIClient(HTTP_HOST='admin.esports.club')
        reader.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        response = reader.get('/api/admin/content-compliance-receipts/', {
            'content_type': 'escort_profile', 'object_id': self.profile.pk,
            'field_name': 'PROVIDER_DISPLAY_NAME',
        })
        self.assertEqual(response.status_code, 200, response.content)
        serialized = str(response.data)
        for content in ('原昵称', '甲店合规昵称', '乙店合规昵称'):
            self.assertNotIn(content, serialized)
        self.assertEqual({item['actorLabel'] for item in response.data['data']['results']},
                         {f'ACCOUNT:{self.account.pk}'})


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ProviderRegistrationNameComplianceTests(APITestCase):
    def setUp(self):
        folder = TemporaryDirectory(prefix='xa-registration-compliance-')
        self.addCleanup(folder.cleanup)
        setting = override_settings(MEDIA_ROOT=folder.name)
        setting.enable()
        self.addCleanup(setting.disable)

    def picture(self, name):
        data = BytesIO()
        Image.new('RGB', (32, 32), '#a2b5cc').save(data, 'PNG')
        return SimpleUploadedFile(name, data.getvalue(), content_type='image/png')

    def identity(self):
        stem = '11010119900101001'
        weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
        return stem + '10X98765432'[sum(int(n) * w for n, w in zip(stem, weights)) % 11]

    def payload(self, **changes):
        return {
            'username': 'name_compliance', 'password': 'test-password-only',
            'phone': '13900001999', 'real_name': '合规测试', 'id_number': self.identity(),
            'display_name': '合规申请昵称',
            **{name: self.picture(name + '.png') for name in ('id_front', 'id_back', 'deposit_proof')},
            **changes,
        }

    def test_registration_rejects_reserved_name_and_records_public_actor_on_submit_and_resubmit(self):
        rejected = self.client.post('/api/provider-registration/', self.payload(display_name='官方客服'), format='multipart')
        self.assertEqual(rejected.status_code, 400, rejected.content)
        self.assertFalse(ProviderRegistration.objects.exists())
        created = self.client.post('/api/provider-registration/', self.payload(), format='multipart')
        self.assertEqual(created.status_code, 201, created.content)
        row = ProviderRegistration.objects.get(pk=created.data['data']['id'])
        receipt = ContentComplianceReceipt.objects.get(
            content_type='provider_registration', object_id=row.pk,
            field_name='PROVIDER_REGISTRATION_DISPLAY_NAME',
        )
        self.assertIsNone(receipt.actor_account_id)
        self.assertEqual(receipt.actor_label, 'PUBLIC_REGISTRATION')
        self.assertEqual(receipt.field_name, 'PROVIDER_REGISTRATION_DISPLAY_NAME')
        row.status = 'REJECTED'
        row.save(update_fields=['status'])
        with self.captureOnCommitCallbacks(execute=True):
            resubmitted = self.client.post('/api/provider-registration/resubmit/',
                                           self.payload(display_name='补充合规昵称'), format='multipart')
        self.assertEqual(resubmitted.status_code, 200, resubmitted.content)
        self.assertEqual(ContentComplianceReceipt.objects.filter(
            content_type='provider_registration', object_id=row.pk,
            field_name='PROVIDER_REGISTRATION_DISPLAY_NAME').count(), 2)
