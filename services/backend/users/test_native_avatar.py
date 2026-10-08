"""Authenticated native avatar upload, format validation and ownership checks."""
from io import BytesIO
from tempfile import TemporaryDirectory

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APITestCase

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_user, User


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class NativeAvatarTest(APITestCase):
    url = '/api/users/me/avatar/'

    def setUp(self):
        folder = TemporaryDirectory(prefix='xa-avatar-test-')
        self.addCleanup(folder.cleanup)
        settings = override_settings(MEDIA_ROOT=folder.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.user = make_user()
        self.account = get_or_create_account_for_legacy_user(self.user)

    def authenticate(self, account=None):
        token, _ = issue_account_tokens(account or self.account)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def image(self):
        data = BytesIO()
        Image.new('RGB', (12, 12), '#087f68').save(data, format='PNG')
        return SimpleUploadedFile('avatar.png', data.getvalue(), content_type='image/png')

    def test_uploaded_avatar_is_visible_in_me_and_only_changes_current_account(self):
        other = get_or_create_account_for_legacy_user(make_user())
        self.authenticate()
        response = self.client.post(self.url, {'avatar': self.image(), 'account_id': other.pk}, format='multipart')
        self.assertEqual(response.data['code'], 0, response.data)
        self.account.refresh_from_db()
        self.user.refresh_from_db()
        other.refresh_from_db()
        self.assertTrue(self.account.avatar.storage.exists(self.account.avatar.name))
        self.assertTrue(self.account.avatar.name.endswith('.png'))
        self.assertEqual(response.data['data']['avatar'], self.account.avatar_url)
        self.assertEqual(self.user.avatar_url, self.account.avatar_url)
        self.assertEqual(self.client.get('/api/users/me/').data['data']['avatar'], self.account.avatar_url)
        self.assertFalse(other.avatar)

    def test_requires_boss_login(self):
        self.assertEqual(self.client.post(self.url, {'avatar': self.image()}, format='multipart').status_code, 401)
        provider = get_or_create_account_for_legacy_user(make_user(role=User.Role.PROVIDER))
        self.authenticate(provider)
        self.assertEqual(self.client.post(self.url, {'avatar': self.image()}, format='multipart').data['code'], 403)

    def test_rejects_missing_fake_and_oversized_upload_without_mutation(self):
        self.authenticate()
        cases = [{}, {'avatar': SimpleUploadedFile('fake.png', b'<script>bad</script>')},
                 {'avatar': SimpleUploadedFile('big.png', b'0' * (5 * 1024 * 1024 + 1))}]
        for data in cases:
            with self.subTest(name=data.get('avatar')):
                self.assertEqual(self.client.post(self.url, data, format='multipart').data['code'], 400)
        self.account.refresh_from_db()
        self.assertFalse(self.account.avatar)

    @override_settings(WECHAT_MOCK_LOGIN=True)
    def test_legacy_login_rejects_invalid_avatar_before_creating_account(self):
        before = ClubAccount.objects.count()
        response = self.client.post('/api/users/wechat-login/', {
            'code': 'avatar-upload-new', 'avatar': SimpleUploadedFile('fake.png', b'not an image'),
        }, format='multipart')
        self.assertEqual(response.data['errorCode'], 'INVALID_AVATAR')
        self.assertEqual(ClubAccount.objects.count(), before)
