from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Store
from .serializers import StoreCreateSerializer, public_store


class StorePresentationTests(TestCase):
    databases = {'default'}

    def setUp(self):
        self.first, _ = Store.objects.update_or_create(database_alias='default', defaults={
            'slug': 'shenglang', 'name': '声浪电竞', 'currency_name': '浪花',
            'status': Store.Status.ACTIVE, 'initialized_at': timezone.now(),
        })
        self.second, _ = Store.objects.update_or_create(database_alias='store_b', defaults={
            'slug': 'xingan-preview', 'name': '兴安电竞', 'currency_name': '星辉',
            'status': Store.Status.ACTIVE, 'initialized_at': timezone.now(),
        })

    def directory(self):
        response = APIClient().get('/api/stores/available/', HTTP_X_STORE_SLUG='shenglang')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['code'], 0)
        return response.data['data']['results']

    def test_public_directory_contains_only_the_current_stores_presentation(self):
        rows = self.directory()
        self.assertEqual({row['name'] for row in rows}, {'声浪电竞'})
        self.assertEqual({row['currency_name'] for row in rows}, {'浪花'})
        allowed = {'id', 'slug', 'name', 'currency_name', 'entity_prefix', 'provider_name_prefix',
                   'logo_url', 'background_url', 'introduction', 'content_compliance_enabled',
                   'status', 'is_active', 'boss_url', 'provider_url', 'admin_url'}
        for row in rows:
            self.assertEqual(set(row), allowed)
            self.assertIsInstance(row['introduction'], str)
            self.assertIs(type(row['content_compliance_enabled']), bool)

    def test_disabled_store_is_not_offered(self):
        self.second.status = Store.Status.DISABLED
        self.second.save(update_fields=['status'])
        self.assertEqual([row['slug'] for row in self.directory()], ['shenglang'])

    def test_uninitialized_store_is_not_offered(self):
        self.second.initialized_at = None
        self.second.save(update_fields=['initialized_at'])
        self.assertEqual([row['slug'] for row in self.directory()], ['shenglang'])

    def test_unknown_database_is_not_offered(self):
        Store.objects.create(slug='unconfigured', name='不可用门店', database_alias='not_configured',
                             status=Store.Status.ACTIVE, initialized_at=timezone.now())
        self.assertNotIn('unconfigured', [row['slug'] for row in self.directory()])

    def test_currency_is_metadata_and_defaults_for_existing_stores(self):
        self.assertEqual(Store._meta.get_field('currency_name').get_default(), '浪花')
        self.assertEqual(public_store(self.second)['currency_name'], '星辉')
        self.assertIn('currency_name', StoreCreateSerializer.Meta.fields)
