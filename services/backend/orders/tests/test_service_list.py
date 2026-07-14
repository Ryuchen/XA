from rest_framework.test import APITestCase

from orders.tests.factories import make_service, make_service_category, make_user


class ServiceListFilterTest(APITestCase):
    URL = '/api/orders/services/'

    def setUp(self):
        self.client.force_authenticate(make_user())

    def test_gift_category_only_returns_active_gift_services(self):
        gift_category = make_service_category(name='礼物', is_gift=True)
        normal_category = make_service_category(name='陪玩', is_gift=False)
        gift = make_service(name='心动礼物', service_category=gift_category)
        make_service(name='王者陪玩', service_category=normal_category)

        response = self.client.get(self.URL, {'category': 'GIFT'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.data['data']], [gift.id])
