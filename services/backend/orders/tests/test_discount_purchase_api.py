from unittest.mock import patch
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from coupons.models import Coupon, UserCoupon
from engagement.models import PointFundingLot
from engagement.services import config
from orders.models import Order
from orders.tests.factories import make_boss_type, make_promotion, make_service, make_user
from wallet.services import get_wallet


@override_settings(DISCOUNT_OPTIONS_PREVIEW_ENABLED=True, DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True,
                   BOSS_FUNDING_SELECTION_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   SERVICE_POINTS_CHECKOUT_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
                   SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True,
                   SERVICE_POINTS_SETTLEMENT_TESTING=True, SERVICE_ORDINARY_FUNDING_TESTING=True,
                   ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   GRAB_SINGLE_WINDOW_ENABLED=True, GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True,
                   GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True,
                   CELERY_TASK_ALWAYS_EAGER=False)
class DiscountPurchaseApiTest(APITestCase):
    def setUp(self):
        user = make_user(); self.account = get_or_create_account_for_legacy_user(user)
        self.account.boss_type = make_boss_type(name='隔离购买会员', discount_rate=90)
        self.account.save(update_fields=['boss_type'])
        self.service = make_service(price=10000)
        self.wallet = get_wallet(account=self.account, user=user)
        self.wallet.balance = 20000; self.wallet.save(update_fields=['balance'])
        PointFundingLot.objects.create(account=self.account, source_key='discount-payment-principal',
                                       kind='PREPAID', remaining=20000)
        config()
        from console.grab_config import save_grab_window
        save_grab_window(30, 0, lambda *_: None)
        self.coupon = Coupon.objects.create(name='优惠减300', discount_type='DIRECT', amount=3000)
        self.owned = UserCoupon.objects.create(account=self.account, coupon=self.coupon)
        self.promo = make_promotion(title='八五折活动', discount_rate=85)
        self.client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])

    def options(self):
        result = self.client.post('/api/orders/discount-options-preview/', {'service_id': self.service.pk}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        return result.data['data']['options']

    def submit(self, option, request_id='discount-buy-001'):
        return self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            'discount_selection_token': option['selection_token'], 'expected_amount': option['amount'],
            'client_request_id': request_id}, format='json')

    def test_recommended_combination_is_charged_and_consumes_exactly_one_coupon(self):
        option = self.options()[0]
        self.assertEqual(option['amount'], 5500)
        quote = self.client.post('/api/orders/quote/', {'service_id': self.service.pk,
            'discount_selection_token': option['selection_token']}, format='json')
        self.assertEqual(quote.data['data']['amount'], 5500)
        response = self.submit(option)
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        self.assertEqual((order.amount, order.boss_discount, order.promo_discount, order.coupon_discount),
                         (5500, 0, 1500, 3000))
        self.assertEqual(order.promotion_id, self.promo.pk)
        self.owned.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((self.owned.status, self.owned.order_id, self.wallet.balance), ('USED', order.pk, 14500))
        self.assertEqual(order.funding_allocations.get(kind='PREPAID').amount, 5500)

    def test_customer_can_choose_vip_instead_and_coupon_is_not_consumed(self):
        selected = next(item for item in self.options() if item['key'] == 'vip')
        response = self.submit(selected)
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        self.assertEqual((order.amount, order.boss_discount, order.promo_discount, order.coupon_discount),
                         (9000, 1000, 0, 0))
        self.owned.refresh_from_db(); self.assertEqual(self.owned.status, 'UNUSED')

    def test_changed_coupon_is_rejected_inside_payment_transaction(self):
        selected = self.options()[0]
        from orders.discount_selection import lock_selected_discount
        def change_then_validate(*args):
            Coupon.objects.filter(pk=self.coupon.pk).update(amount=2000)
            return lock_selected_discount(*args)
        with patch('orders.discount_selection.lock_selected_discount', side_effect=change_then_validate):
            response = self.submit(selected)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Order.objects.exists())
        self.wallet.refresh_from_db(); self.assertEqual(self.wallet.balance, 20000)
        self.owned.refresh_from_db(); self.assertEqual(self.owned.status, 'UNUSED')
        self.coupon.refresh_from_db(); self.assertEqual(self.coupon.amount, 3000)

    def test_missing_or_tampered_token_cannot_fall_back_to_old_stacking(self):
        response = self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            'client_request_id': 'no-choice'}, format='json')
        self.assertNotEqual(response.data['code'], 0)
        selected = self.options()[0]; selected['selection_token'] += 'bad'
        response = self.submit(selected)
        self.assertNotEqual(response.data.get('code', 400), 0)
        self.assertFalse(Order.objects.exists())
