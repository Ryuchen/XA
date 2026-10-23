from io import BytesIO
from random import Random
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from common.catalog_images import DISPLAY_MAX_BYTES, catalog_display_url, compress_catalog_image
from console.gameplay import GameplaySerializer
from engagement.models import ClubContent
from engagement.serializers import ContentSerializer
from orders.models import Gameplay, ServiceItem
from orders.serializers import ServiceItemDetailSerializer
from orders.tests.factories import make_console_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=True)
class CatalogImageTests(TestCase):
    def image(self):
        data = BytesIO()
        Image.new('RGB', (3200, 9600), '#fffdf5').save(data, 'PNG')
        return SimpleUploadedFile('poster.png', data.getvalue(), content_type='image/png')

    def test_display_is_bounded_and_original_is_archived(self):
        client = APIClient()
        client.force_authenticate(make_console_user(['service:edit', 'engagement:edit']))
        with TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=folder):
            for endpoint in ('catalog-image', 'club-content-image'):
                response = client.post(f'/api/admin/{endpoint}/', {'image': self.image()}, format='multipart')
                self.assertEqual(response.status_code, 200, response.data)
                data = response.data['data']
                self.assertIn('/thumbnails/v4/', data['url'])
                self.assertTrue(data['url'].endswith('.jpg'))
                self.assertLessEqual(data['width'], 1080)
                self.assertLessEqual(data['bytes'], DISPLAY_MAX_BYTES)
                with default_storage.open(data['url'].removeprefix('/media/')) as display:
                    self.assertEqual(Image.open(display).format, 'JPEG')
                with default_storage.open(data['original_url'].removeprefix('/media/')) as original:
                    self.assertEqual(Image.open(original).size, (3200, 9600))

    def test_noisy_long_poster_cannot_escape_the_limit_at_small_width(self):
        source = BytesIO()
        image = Image.frombytes('RGB', (640, 4000), Random(1).randbytes(640 * 4000 * 3))
        image.save(source, 'PNG'); source.seek(0)
        output, (width, height) = compress_catalog_image(source)
        self.assertLessEqual(len(output), 200_000)
        self.assertLess(width, 640)
        self.assertAlmostEqual(height / width, 4000 / 640, delta=.01)
        self.assertEqual(Image.open(BytesIO(output)).size, (width, height))

    def test_old_400kb_cache_cannot_bypass_the_200kb_display_limit(self):
        with TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=folder):
            source = BytesIO()
            Image.frombytes('RGB', (800, 800), Random(2).randbytes(800 * 800 * 3)).save(source, 'JPEG', quality=95)
            self.assertGreater(len(source.getvalue()), 200_000)
            old = 'club-catalog/thumbnails/v3/old.jpg'
            default_storage.save(old, ContentFile(source.getvalue()))
            url = catalog_display_url('/media/' + old)
            self.assertIn('/thumbnails/v4/', url)
            self.assertLessEqual(default_storage.size(url.removeprefix('/media/')), 200_000)
            with default_storage.open(old, 'rb') as original:
                self.assertEqual(original.read(), source.getvalue())

    def test_legacy_images_are_shared_cached_and_replacement_invalidates(self):
        with TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=folder):
            default_storage.save('club-catalog/prices/old.png', self.image())
            first = catalog_display_url('/media/club-catalog/prices/old.png')
            self.assertTrue(first.endswith('.jpg'))
            with patch('common.catalog_images.compress_catalog_image', side_effect=AssertionError('cache miss')):
                self.assertEqual(catalog_display_url('https://api.xaesports.club/media/club-catalog/prices/old.png'), first)
                self.assertEqual(catalog_display_url(first), first)
            with default_storage.open('club-catalog/prices/old.png', 'wb') as target:
                Image.new('RGB', (20, 30), '#002fa7').save(target, 'PNG')
            self.assertNotEqual(catalog_display_url('/media/club-catalog/prices/old.png'), first)

    def test_public_catalog_fields_and_rich_text_only_use_display_assets(self):
        with TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=folder):
            default_storage.save('club-catalog/old.png', self.image())
            url = '/media/club-catalog/old.png'
            play = Gameplay.objects.create(name='图片玩法', code='bounded-image', cover_url=url)
            service = ServiceItem.objects.create(name='图片套餐', price=12000, billing_provider_count=2,
                gameplay=play, cover_url=url, images=[url], rules_images=[url], introduction_images=[url],
                details_html=f'<p>完整规则</p><img src="{url}" data-original="{url}" />')
            data = ServiceItemDetailSerializer(service).data
            display = catalog_display_url(url)
            for field in ('cover_url', 'gameplay_cover_url'):
                self.assertEqual(data[field], display)
            for field in ('images', 'rules_images', 'introduction_images', 'details_images'):
                self.assertEqual(data[field], [display])
            self.assertIn(display, data['details_html'])
            self.assertNotIn('data-original', data['details_html'])
            self.assertNotIn(url, str(data))
            self.assertEqual(data['price'], 24000)
            self.assertEqual(GameplaySerializer(play).data['cover_url'], url)
            service.refresh_from_db(); self.assertEqual(service.cover_url, url)
            content = ClubContent.objects.create(code='bounded-content', title='价目', image_url=url,
                content=f'<p>说明</p><img src="{url}" data-original="{url}" />')
            public = ContentSerializer(content, context={'display_images': True}).data
            self.assertEqual(public['image_url'], display)
            self.assertNotIn(url, public['content'])
            self.assertNotIn('data-original', public['content'])
            self.assertEqual(ContentSerializer(content).data['image_url'], url)

    def test_missing_private_foreign_and_corrupt_assets_never_fall_back(self):
        with TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=folder):
            default_storage.save('club-catalog/corrupt.png', ContentFile(b'broken image'))
            values = ['/media/provider-registration/secret.png', '/media/club-catalog/%2e%2e/secret.png',
                      'https://foreign.test/media/club-catalog/a.png', '//evil.test/media/club-catalog/a.png',
                      '/media/club-catalog/missing.png', '/media/club-catalog/corrupt.png',
                      '/media/club-catalog/a.png?original=1']
            for value in values:
                with self.subTest(value=value):
                    self.assertEqual(catalog_display_url(value), '')

    def test_view_permission_cannot_upload(self):
        client = APIClient(); client.force_authenticate(make_console_user(['service:view']))
        response = client.post('/api/admin/catalog-image/', {'image': self.image()}, format='multipart')
        self.assertEqual(response.status_code, 403)

    def test_invalid_file_is_rejected(self):
        client = APIClient(); client.force_authenticate(make_console_user(['service:edit']))
        response = client.post('/api/admin/catalog-image/', {'image': SimpleUploadedFile('a.png', b'not an image', content_type='image/png')}, format='multipart')
        self.assertEqual(response.status_code, 400)
