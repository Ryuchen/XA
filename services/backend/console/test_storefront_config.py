from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from engagement.models import ProviderNaming
from engagement.serializers import ProviderNamingSerializer
from orders.tests.factories import make_service, make_service_category
from .serializers import AdminBannerSerializer, AdminServiceItemSerializer


class StorefrontConfigTests(TestCase):
    def test_festival_price_window_and_naming_validation(self):
        gift = make_service(service_category=make_service_category(is_gift=True), price=2100)
        for payload in [{'gift_festival': '春节', 'price': 2099}, {'gift_naming_days': -1},
                {'gift_starts_at': timezone.now(), 'gift_ends_at': timezone.now() - timedelta(days=1)}]:
            self.assertFalse(AdminServiceItemSerializer(gift, data=payload, partial=True).is_valid())
        serializer = AdminServiceItemSerializer(gift, data={'gift_festival': '春节', 'gift_naming_days': 3}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        regular = make_service()
        self.assertFalse(AdminServiceItemSerializer(regular, data={'gift_naming_days': 1}, partial=True).is_valid())

    def test_banner_business_destination_is_whitelisted(self):
        from banners.models import Banner
        banner = Banner(title='礼物', image='banners/test.png')
        valid = AdminBannerSerializer(banner, data={'link_type': 'PAGE', 'link_value': 'gifts'}, partial=True)
        self.assertTrue(valid.is_valid(), valid.errors)
        for target in ['../admin', '/pages/login/index', 'https://example.com']:
            self.assertFalse(AdminBannerSerializer(banner, data={'link_type': 'PAGE', 'link_value': target}, partial=True).is_valid())

    def test_naming_moderation_cannot_reassign_ownership_or_expiry(self):
        row = ProviderNaming(title='原冠名', expires_at=timezone.now())
        serializer = ProviderNamingSerializer(row, data={'title': '更新冠名', 'is_visible': False,
            'provider': 999, 'sponsor': 999, 'expires_at': timezone.now() + timedelta(days=999)}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(set(serializer.validated_data), {'title', 'is_visible'})
