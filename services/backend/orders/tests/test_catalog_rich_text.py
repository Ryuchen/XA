from tempfile import TemporaryDirectory
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from PIL import Image
from io import BytesIO
from django.test import TestCase, override_settings
from common.rich_text import sanitize_catalog_html, image_source
from console.gameplay import GameplaySerializer
from console.serializers import AdminServiceItemSerializer
from orders.models import Gameplay, ServiceItem
from orders.serializers import ServiceItemDetailSerializer


class CatalogRichTextTests(TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        media = override_settings(MEDIA_ROOT=folder.name)
        media.enable(); self.addCleanup(media.disable)
        data = BytesIO(); Image.new('RGB', (100, 200), 'white').save(data, 'PNG')
        for name in ['prices/mobile.png', 'display.webp']:
            default_storage.save('club-catalog/' + name, ContentFile(data.getvalue()))
        self.gameplay = Gameplay.objects.create(name='富文本测试', code='rich-text-test', details_html='<h2>玩法介绍</h2><p><strong>平台规则</strong></p>')
        self.service = ServiceItem.objects.create(name='当前价目', price=16800, gameplay=self.gameplay, platform='PC', security_level='TOP_SECRET', rules='原有履约规则')

    def test_gameplay_fallback_and_tier_override(self):
        self.assertEqual(ServiceItemDetailSerializer(self.service).data['details_html'], self.gameplay.details_html)
        self.service.details_html = '<p>手游专属</p><img src="/media/club-catalog/prices/mobile.png" />'
        data = ServiceItemDetailSerializer(self.service).data
        self.assertIn('手游专属', data['details_html'])
        self.assertEqual(len(data['details_images']), 1)
        self.assertIn('/thumbnails/v3/', data['details_images'][0])
        self.assertEqual(data['rules'], '原有履约规则')
        self.assertEqual(data['price'], 16800)

    def test_admin_writes_sanitized_html(self):
        for serializer_class, instance in [(GameplaySerializer, self.gameplay), (AdminServiceItemSerializer, self.service)]:
            serializer = serializer_class(instance, data={'details_html': '<p onclick="alert(1)">详情<strong>加粗</strong></p><script>alert(1)</script><img src="javascript:alert(1)" />'}, partial=True)
            self.assertTrue(serializer.is_valid(), serializer.errors)
            saved = serializer.save()
            self.assertEqual(saved.details_html, '<p>详情<strong>加粗</strong></p>')

    def test_lightweight_display_removes_original_preview(self):
        self.service.details_html = '<img src="/media/club-catalog/display.webp" data-original="/media/club-catalog/original.png" />'
        data = ServiceItemDetailSerializer(self.service).data
        self.assertNotIn('data-original', data['details_html'])
        self.assertIn('/thumbnails/v3/', data['details_images'][0])
        self.assertNotIn('original.png', data['details_html'])
        self.service.details_html = '<img src="/media/club-catalog/display.webp" data-original="/media/provider-registration/secret.png" />'
        data = ServiceItemDetailSerializer(self.service).data
        self.assertNotIn('data-original', data['details_html'])
        self.assertIn('/thumbnails/v3/', data['details_images'][0])

    def test_no_inline_scripts_or_private_images(self):
        dirty = '<style>bad</style><svg><script>bad</script></svg><iframe>bad</iframe><p style="color:red">安全 & 内容</p><img src="https://api.xaesports.club/media/registrations/private.png" /><img src="/media/club-catalog/a.png" onerror="bad" />'
        clean = sanitize_catalog_html(dirty)
        self.assertNotIn('bad', clean)
        self.assertNotIn('private', clean)
        self.assertIn('安全 &amp; 内容', clean)
        self.assertIn('max-width:100%', clean)
        self.assertNotIn('onerror', clean)

    def test_invalid_image_sources(self):
        for value in ['//evil.test/a.png', 'data:image/svg+xml,<svg/>', 'javascript:alert(1)', 'http://a.test/a.png', 'https://[', 'https://name:secret@a.test/a.png', '/media/club-catalog/../private.png', '/media/club-catalog/%2e%2e/private.png']:
            with self.subTest(value=value):
                self.assertEqual(image_source(value), '')

    def test_formatting_and_malformed_tags_are_portable(self):
        clean = sanitize_catalog_html('<h2>标题</h2><ul><li><u>一</u></li><li><em>二</em></li></ul><p><strong>内容</p>')
        self.assertTrue(clean.endswith('<p><strong>内容</strong></p>'))
        self.assertIn('<u>一</u>', clean)

    def test_content_limit(self):
        serializer = GameplaySerializer(self.gameplay, data={'details_html': 'a' * 100001}, partial=True)
        self.assertFalse(serializer.is_valid())
        self.assertIn('details_html', serializer.errors)
