"""Store logo uploads use validated local media and platform-only authorization."""
from io import BytesIO

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase, override_settings
from PIL import Image, ImageDraw
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens

from .context import resolve_store
from .models import Store, StorePlatformGrant
from .tests_foundation import admin


@override_settings(STORE_DATABASE_ALIASES=('store_b',), LOGIN_OTP_REQUIRED=False,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class StoreLogoUploadTest(TransactionTestCase):
    databases = {'default', 'store_b'}

    def platform_client(self, username, granted=True):
        account = admin(username=username, super_admin=True)
        if granted:
            StorePlatformGrant.objects.create(account_id=account.pk)
        token, _ = issue_account_tokens(account)
        client = APIClient()
        client.credentials(HTTP_X_STORE_SLUG=resolve_store().slug, HTTP_AUTHORIZATION='Bearer ' + token)
        return client

    def image(self, name='store-logo.png'):
        output = BytesIO()
        image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        ImageDraw.Draw(image).rectangle((12, 12, 51, 51), fill='#2563eb')
        image.save(output, 'PNG')
        return SimpleUploadedFile(name, output.getvalue(), content_type='image/png')

    def test_upload_and_attach_logo_to_store(self):
        client = self.platform_client('platform-logo-owner')
        uploaded = client.post('/api/stores/manage/logo-upload/', {'image': self.image()}, format='multipart')
        self.assertEqual(uploaded.status_code, 200, uploaded.data)
        url = uploaded.data['data']['url']
        self.assertTrue(url.endswith('.png'))
        path = url.rsplit('/media/', 1)[-1]
        self.assertTrue(default_storage.exists(path))
        with default_storage.open(path, 'rb') as stored:
            with Image.open(stored) as image:
                self.assertEqual(image.mode, 'RGBA')
                self.assertEqual(image.convert('RGBA').getpixel((0, 0))[3], 0)
        created = client.post('/api/stores/manage/', {
            'slug': 'logo-branch', 'name': '门店 Logo 测试店', 'database_alias': 'store_b', 'logo_url': url,
        }, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data['data']['logo_url'], url)
        self.assertEqual(Store.objects.get(slug='logo-branch').logo_url, url)
        remote = client.post('/api/stores/manage/', {
            'slug': 'remote-logo-branch', 'name': '外部 Logo 店', 'database_alias': 'store_b',
            'logo_url': 'https://example.com/tracking.png',
        }, format='json')
        self.assertEqual(remote.status_code, 400, remote.data)
        self.assertFalse(Store.objects.filter(slug='remote-logo-branch').exists())

    def test_upload_requires_platform_permission_and_real_image(self):
        denied = self.platform_client('ordinary-logo-user', granted=False)
        self.assertEqual(denied.post('/api/stores/manage/logo-upload/', {'image': self.image()}, format='multipart').status_code, 403)
        allowed = self.platform_client('platform-logo-validator')
        invalid = SimpleUploadedFile('not-an-image.png', b'not an image', content_type='image/png')
        self.assertEqual(allowed.post('/api/stores/manage/logo-upload/', {'image': invalid}, format='multipart').status_code, 400)
