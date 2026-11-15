"""Anonymous users may browse the public catalog, never private account data or orders."""
from rest_framework.test import APITestCase

from orders.models import Order
from orders.tests.factories import make_service


class PublicCatalogAccessTest(APITestCase):
    def setUp(self):
        self.service = make_service(name='公开浏览测试服务')
        self.client.force_authenticate(None)

    def assertAnonymous(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data['data']

    def test_public_catalog_companion_and_content_endpoints_allow_anonymous_reads(self):
        services = self.assertAnonymous('/api/orders/services/')
        self.assertEqual([row['id'] for row in services], [self.service.pk])
        detail = self.assertAnonymous(f'/api/orders/services/{self.service.pk}/')
        self.assertEqual(detail['name'], '公开浏览测试服务')
        self.assertFalse(detail['is_favorited'])
        self.assertAnonymous(f'/api/orders/services/{self.service.pk}/evaluations/')
        self.assertAnonymous('/api/orders/game-categories/')
        self.assertAnonymous('/api/users/escorts/?directory=1')
        self.assertAnonymous('/api/users/qualification-options/')
        self.assertAnonymous('/api/announcements/')
        self.assertAnonymous('/api/club/content/')

    def test_personalized_catalog_queries_still_require_login(self):
        for path in (
            '/api/orders/services/?coupon_id=1',
            f'/api/orders/services/{self.service.pk}/?coupon_id=1',
            '/api/users/escorts/?shortcuts=1',
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 401, response.data)

    def test_anonymous_users_cannot_read_private_data_or_create_business_records(self):
        for path in ('/api/orders/orders/', '/api/users/me/', '/api/wallet/info/'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)
        for path in ('/api/orders/quote/', '/api/orders/create/', '/api/orders/reservations/'):
            with self.subTest(path=path):
                response = self.client.post(path, {'service_id': self.service.pk}, format='json')
                self.assertEqual(response.status_code, 401, response.data)
        self.assertFalse(Order.objects.exists())
