"""Store service prices use the confirmed whole-ten-coin list-price rule."""
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import ServiceItem
from orders.tests.factories import make_console_user, make_service, make_service_category


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class ServicePriceRulesTest(APITestCase):
    def setUp(self):
        staff = make_console_user(['service:view', 'service:edit'])
        account = get_or_create_account_for_legacy_user(staff)
        link_legacy_relations(staff, account)
        staff.admin_membership.account = account
        staff.admin_membership.save(update_fields=['account'])
        self.client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])

    def test_new_or_changed_service_prices_use_ten_coin_steps(self):
        invalid = self.client.post('/api/admin/service-items/', {'name': '非整十服务', 'price': 1010}, format='json')
        self.assertEqual(invalid.status_code, 400)
        self.assertIn('10 店币', str(invalid.data))
        self.assertFalse(ServiceItem.objects.filter(name='非整十服务').exists())
        # 10,100 ledger units = 1,010 store coins = 101 yuan.
        valid = self.client.post('/api/admin/service-items/', {'name': '整十服务', 'price': 10100}, format='json')
        self.assertEqual(valid.data['code'], 0, valid.data)
        service = ServiceItem.objects.get(pk=valid.data['data']['id'])
        response = self.client.patch(f'/api/admin/service-items/{service.pk}/', {'price': 10110}, format='json')
        self.assertEqual(response.status_code, 400)
        service.refresh_from_db()
        self.assertEqual(service.price, 10100)
        response = self.client.patch(f'/api/admin/service-items/{service.pk}/', {'price': 10200}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        service.refresh_from_db()
        self.assertEqual(service.price, 10200)

    def test_legacy_price_is_not_rewritten_and_unrelated_edit_is_allowed(self):
        service = make_service(price=150, is_active=False)
        url = f'/api/admin/service-items/{service.pk}/'
        response = self.client.patch(url, {'description': '保留旧价待核对'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        self.assertEqual(self.client.patch(url, {'is_active': True}, format='json').status_code, 400)
        service.refresh_from_db()
        self.assertEqual((service.price, service.is_active), (150, False))
        response = self.client.patch(url, {'price': 200, 'is_active': True}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        service.refresh_from_db()
        self.assertEqual((service.price, service.is_active), (200, True))

    def test_gift_price_uses_gift_rules_not_service_price_steps(self):
        category = make_service_category(is_gift=True)
        result = self.client.post('/api/admin/service-items/', {'name': '礼物独立价格',
            'price': 350, 'service_category': category.pk}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        item = ServiceItem.objects.get(pk=result.data['data']['id'])
        self.assertEqual(item.price, 350)
        url = f'/api/admin/service-items/{item.pk}/'
        self.assertEqual(self.client.patch(url, {'service_category': None}, format='json').status_code, 400)
        item.refresh_from_db()
        self.assertEqual((item.service_category_id, item.price), (category.pk, 350))
        changed = self.client.patch(url, {'service_category': None, 'price': 400}, format='json')
        self.assertEqual(changed.data['code'], 0, changed.data)
