"""Favorites remain manageable when the purchase catalog changes."""
from django.test import override_settings
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_service, make_service_category, make_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class FavoriteCatalogAvailabilityTest(APITestCase):
    def test_parent_disabled_keeps_favorite_manageable_without_allowing_purchase(self):
        user = make_user()
        account = get_or_create_account_for_legacy_user(user)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        category = make_service_category(name='隔离服务分类')
        service = make_service(name='隔离服务', price=1000, service_category=category)
        toggle_url = '/api/orders/favorites/toggle/'

        self.assertEqual(self.client.get(f'/api/orders/services/{service.pk}/').data['code'], 0)
        self.assertTrue(self.client.post(toggle_url, {'service_id': service.pk}, format='json').data['data']['favorited'])
        category.is_active = False
        category.save(update_fields=['is_active'])

        self.assertNotEqual(self.client.get(f'/api/orders/services/{service.pk}/').data['code'], 0)
        unavailable = self.client.get('/api/orders/favorites/').data['data']
        self.assertEqual([(row['id'], row['available']) for row in unavailable], [(service.pk, False)])
        result = self.client.post(toggle_url, {'service_id': service.pk}, format='json').data
        self.assertNotEqual(result['code'], 0, result)

        category.is_active = True
        category.save(update_fields=['is_active'])
        restored = self.client.get('/api/orders/favorites/').data['data']
        self.assertEqual([(row['id'], row['available']) for row in restored], [(service.pk, True)])
        category.is_active = False
        category.save(update_fields=['is_active'])
        remove_url = f'/api/orders/favorites/{service.pk}/'
        self.assertEqual(self.client.delete(remove_url).data['code'], 0)
        self.assertEqual(self.client.delete(remove_url).data['code'], 0)
        self.assertEqual(self.client.get('/api/orders/favorites/').data['data'], [])
        self.assertNotEqual(self.client.post(toggle_url, {'service_id': service.pk}, format='json').data['code'], 0)
        category.is_active = True
        category.save(update_fields=['is_active'])
        self.assertEqual(self.client.get('/api/orders/favorites/').data['data'], [])

    def test_service_disabled_can_be_removed_without_affecting_another_boss(self):
        first, second = make_user(), make_user()
        accounts = [get_or_create_account_for_legacy_user(user) for user in (first, second)]
        service = make_service(name='隔离服务', price=1000)
        for account in accounts:
            self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            self.assertTrue(self.client.post('/api/orders/favorites/toggle/', {'service_id': service.pk}, format='json').data['data']['favorited'])
        service.is_active = False
        service.save(update_fields=['is_active'])
        self.assertFalse(self.client.get('/api/orders/favorites/').data['data'][0]['available'])
        self.assertEqual(self.client.delete(f'/api/orders/favorites/{service.pk}/').data['code'], 0)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[0])[0])
        self.assertEqual(self.client.get('/api/orders/favorites/').data['data'][0]['id'], service.pk)
        self.assertEqual(self.client.delete(f'/api/orders/favorites/{service.pk}/').data['code'], 0)
