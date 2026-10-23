from django.test import override_settings
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from .factories import make_user, make_order, make_provider


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class NativeOrderDetailTest(APITestCase):
    def setUp(self):
        self.customer = make_user()
        self.account = get_or_create_account_for_legacy_user(self.customer)
        self.order = make_order(self.customer, customer_account=self.account)
        self.url = f'/api/orders/orders/{self.order.pk}/'

    def login(self, user):
        account = get_or_create_account_for_legacy_user(user)
        token, _ = issue_account_tokens(account)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_owner_gets_same_detail_and_actions_as_list(self):
        self.login(self.customer)
        detail = self.client.get(self.url)
        listing = self.client.get('/api/orders/orders/')
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data['data'], listing.data['data'][0])
        self.assertIn('allowed_actions', detail.data['data'])

    def test_other_boss_cannot_read_order(self):
        self.login(make_user())
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_unassigned_provider_cannot_read_order(self):
        self.login(make_provider())
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_anonymous_cannot_read_order(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_missing_order_returns_not_found(self):
        self.login(self.customer)
        self.assertEqual(self.client.get('/api/orders/orders/99999999/').status_code, 404)
