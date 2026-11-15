from django.db import transaction
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase
from rest_framework.exceptions import ValidationError

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from coupons.models import Coupon, UserCoupon
from engagement.services import config
from orders.models import Order
from orders.tests.factories import make_boss_type, make_promotion, make_service, make_user
from wallet.models import Transaction, Wallet
from wallet.services import get_wallet


@override_settings(DISCOUNT_OPTIONS_PREVIEW_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False)
class DiscountOptionsPreviewApiTest(APITestCase):
    def setUp(self):
        boss = make_user(); self.account = get_or_create_account_for_legacy_user(boss)
        self.account.boss_type = make_boss_type(name='报价预览VIP', discount_rate=90)
        self.account.save(update_fields=['boss_type'])
        self.service = make_service(price=10000)
        config()
        coupon = Coupon.objects.create(name='预览减300', discount_type='DIRECT', amount=3000)
        self.owned = UserCoupon.objects.create(account=self.account, coupon=coupon)
        make_promotion(title='预览八五折', discount_rate=85)
        self.client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
        self.url = '/api/orders/discount-options-preview/'

    def test_preview_recommends_best_combination_without_consuming_anything(self):
        before = (Order.objects.count(), Transaction.objects.count(), Wallet.objects.count())
        response = self.client.post(self.url, {'service_id': self.service.pk, 'game_rounds': 1}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        data = response.data['data']
        self.assertTrue(data['preview_only'])
        self.assertEqual(data['recommended_key'], data['options'][0]['key'])
        self.assertEqual((data['options'][0]['amount'], data['options'][0]['coupon_id']), (5500, self.owned.pk))
        self.owned.refresh_from_db(); self.assertEqual(self.owned.status, 'UNUSED')
        self.assertEqual(before, (Order.objects.count(), Transaction.objects.count(), Wallet.objects.count()))

    @override_settings(DISCOUNT_OPTIONS_PREVIEW_ENABLED=False)
    def test_endpoint_is_closed_by_default(self):
        self.assertEqual(self.client.post(self.url, {'service_id': self.service.pk}, format='json').status_code, 404)

    def test_another_boss_cannot_see_my_coupon(self):
        other = get_or_create_account_for_legacy_user(make_user())
        client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(other)[0])
        response = client.post(self.url, {'service_id': self.service.pk}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        self.assertFalse(any(item['coupon_id'] == self.owned.pk for item in response.data['data']['options']))

    def test_selected_option_can_be_revalidated_but_not_silently_repriced(self):
        from orders.discount_selection import lock_selected_discount
        result = self.client.post(self.url, {'service_id': self.service.pk}, format='json').data['data']
        token = result['options'][0]['selection_token']
        with transaction.atomic():
            get_wallet(account=self.account, for_update=True)
            selected, promo, coupon = lock_selected_discount(self.account, self.service, 1, token)
        self.assertEqual((selected.amount, coupon.pk), (5500, self.owned.pk))
        self.assertIsNotNone(promo)
        # Even if the total price stays the same, changed coupon terms need a
        # fresh confirmation rather than reusing the old token.
        Coupon.objects.filter(pk=self.owned.coupon_id).update(threshold=100)
        with self.assertRaises(ValidationError), transaction.atomic():
            get_wallet(account=self.account, for_update=True)
            lock_selected_discount(self.account, self.service, 1, token)

    def test_selection_is_bound_to_buyer_and_coupon_availability(self):
        from orders.discount_selection import lock_selected_discount
        token = self.client.post(self.url, {'service_id': self.service.pk}, format='json').data['data']['options'][0]['selection_token']
        other = get_or_create_account_for_legacy_user(make_user())
        with self.assertRaises(ValidationError), transaction.atomic():
            get_wallet(account=other, for_update=True)
            lock_selected_discount(other, self.service, 1, token)
        self.owned.status = 'USED'; self.owned.save(update_fields=['status'])
        with self.assertRaises(ValidationError), transaction.atomic():
            get_wallet(account=self.account, for_update=True)
            lock_selected_discount(self.account, self.service, 1, token)

    def test_customer_can_choose_a_different_legal_option_and_forgery_fails(self):
        from orders.discount_selection import lock_selected_discount
        result = self.client.post(self.url, {'service_id': self.service.pk}, format='json').data['data']
        vip_option = next(item for item in result['options'] if item['key'] == 'vip')
        with transaction.atomic():
            get_wallet(account=self.account, for_update=True)
            selected, promo, coupon = lock_selected_discount(self.account, self.service, 1, vip_option['selection_token'])
        self.assertEqual((selected.amount, promo, coupon), (9000, None, None))
        with self.assertRaises(ValidationError), transaction.atomic():
            get_wallet(account=self.account, for_update=True)
            lock_selected_discount(self.account, self.service, 1, vip_option['selection_token'] + 'tampered')

    def test_expired_promotion_or_disabled_vip_invalidates_selected_option(self):
        from orders.discount_selection import lock_selected_discount
        result = self.client.post(self.url, {'service_id': self.service.pk}, format='json').data['data']
        token = result['options'][0]['selection_token']
        from promotions.models import Promotion
        Promotion.objects.update(is_active=False)
        with self.assertRaises(ValidationError), transaction.atomic():
            get_wallet(account=self.account, for_update=True)
            lock_selected_discount(self.account, self.service, 1, token)
        # A VIP-only selection similarly fails if the store disables VIP.
        vip = next(item for item in result['options'] if item['key'] == 'vip')
        operation = config(); operation.vip_enabled = False; operation.save(update_fields=['vip_enabled'])
        with self.assertRaises(ValidationError), transaction.atomic():
            get_wallet(account=self.account, for_update=True)
            lock_selected_discount(self.account, self.service, 1, vip['selection_token'])
