from unittest.mock import patch

from django.test import override_settings
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from coupons.models import Coupon, UserCoupon
from engagement.services import config
from orders.models import Order
from orders.tests.factories import make_boss_type, make_promotion, make_user
from orders.tests.test_reservation_api import LIST_URL, ReservationApiTestBase
from club_accounts.services import get_or_create_account_for_legacy_user
from wallet.models import Wallet


@override_settings(DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=False,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class ReservationDiscountApiTest(ReservationApiTestBase):
    def setUp(self):
        super().setUp()
        self.boss_account.boss_type = make_boss_type(name='预约九折', discount_rate=90)
        self.boss_account.save(update_fields=['boss_type'])
        config()
        self.promotion = make_promotion(title='预约八五折', discount_rate=85)
        self.coupon = Coupon.objects.create(name='预约减50', discount_type='DIRECT', amount=500)
        self.owned = UserCoupon.objects.create(account=self.boss_account, coupon=self.coupon)
        self.boss_client = self.login(self.boss_account)
        self.staff_client = self.login(self.staff_account)
        self.provider_client = self.login(self.provider_account)

    def login(self, account):
        return APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])

    def confirmed(self):
        created = self.boss_client.post(LIST_URL, self.create_payload(), format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        path = f"{LIST_URL}{created.data['data']['id']}/"
        confirmed = self.provider_client.post(path + 'confirm/', {}, format='json')
        self.assertEqual(confirmed.data['code'], 0, confirmed.data)
        return path

    def test_boss_switches_from_recommended_to_vip_and_keeps_coupon(self):
        path = self.confirmed()
        quote = self.boss_client.get(path + 'quote/').data['data']
        self.assertEqual((quote['amount'], quote['discount_option_key']), (1200, f'promotion:{self.promotion.pk}|coupon:{self.owned.pk}'))
        vip = next(item for item in quote['discount_options'] if item['key'] == 'vip')
        quote = self.boss_client.get(path + 'quote/', {'discount_selection_token': vip['selection_token']}).data['data']
        self.assertEqual((quote['amount'], quote['discount_option_key']), (1800, 'vip'))
        self.assertTrue(any(item['selection_token'] == quote['discount_selection_token'] for item in quote['discount_options']))
        result = self.boss_client.post(path + 'convert/', {'discount_selection_token': quote['discount_selection_token'],
            'expected_amount': 1800, 'expected_service': quote['service'], 'client_request_id': 'reservation-vip'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get(pk=result.data['data']['order']['id'])
        self.assertEqual((order.amount, order.boss_discount, order.coupon_discount), (1800, 200, 0))
        self.owned.refresh_from_db(); self.assertEqual(self.owned.status, 'UNUSED')

    def test_staff_needs_confirmation_and_consumes_boss_coupon(self):
        path = self.confirmed()
        quote = self.staff_client.get(path + 'quote/').data['data']
        payload = {'discount_selection_token': quote['discount_selection_token'], 'expected_amount': quote['amount'],
                   'expected_service': quote['service'], 'client_request_id': 'reservation-discount-staff'}
        denied = self.staff_client.post(path + 'convert/', payload, format='json')
        self.assertEqual(denied.data['code'], 400)
        self.assertFalse(Order.objects.exists())
        result = self.staff_client.post(path + 'convert/', {**payload, 'boss_confirmation': '老板确认活动加金额券'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get(pk=result.data['data']['order']['id'])
        self.assertEqual((order.amount, order.promo_discount, order.coupon_discount), (1200, 300, 500))
        self.assertEqual(order.funding_confirmation['actor_account_id'], self.staff_account.pk)
        self.owned.refresh_from_db(); self.assertEqual((self.owned.status, self.owned.order_id), ('USED', order.pk))

    def test_coupon_changes_inside_transaction_do_not_debit(self):
        path = self.confirmed()
        quote = self.boss_client.get(path + 'quote/').data['data']
        from orders.discount_selection import lock_selected_discount
        def change_then_validate(*args):
            Coupon.objects.filter(pk=self.coupon.pk).update(amount=400)
            return lock_selected_discount(*args)
        with patch('orders.discount_selection.lock_selected_discount', side_effect=change_then_validate):
            result = self.boss_client.post(path + 'convert/', {'discount_selection_token': quote['discount_selection_token'],
                'expected_amount': quote['amount'], 'client_request_id': 'reservation-discount-changed'}, format='json')
        self.assertEqual(result.status_code, 400)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(Wallet.objects.get(account=self.boss_account).balance, 100000)
        self.coupon.refresh_from_db(); self.assertEqual(self.coupon.amount, 500)
        self.owned.refresh_from_db(); self.assertEqual(self.owned.status, 'UNUSED')

    def test_other_boss_cannot_view_or_reuse_selection(self):
        path = self.confirmed()
        quote = self.boss_client.get(path + 'quote/').data['data']
        other = self.login(get_or_create_account_for_legacy_user(make_user()))
        self.assertNotEqual(other.get(path + 'quote/').data['code'], 0)
        self.assertNotEqual(other.post(path + 'convert/', {'discount_selection_token': quote['discount_selection_token']}, format='json').data['code'], 0)
        self.assertFalse(Order.objects.exists())

    def test_reservation_uses_changed_monthly_coupon_rule(self):
        from django.utils import timezone
        from coupons.monthly import issue_monthly_coupons
        from engagement.models import VipTier
        tier = VipTier.objects.create(boss_type=self.boss_account.boss_type, level=2, recharge_threshold=1,
                                      monthly_coupon_count=1, monthly_discount=80)
        issue_monthly_coupons(self.boss_account, tier.pk, now=timezone.now(), allow_new_period=True)
        monthly = UserCoupon.objects.get(account=self.boss_account, coupon__grant_key__startswith='vip-month:')
        tier.monthly_discount = 40; tier.save(update_fields=['monthly_discount'])
        path = self.confirmed()
        quote = self.boss_client.get(path + 'quote/').data['data']
        self.assertEqual((quote['amount'], quote['discount_option_key']), (800, f'coupon:{monthly.pk}'))
        result = self.boss_client.post(path + 'convert/', {'discount_selection_token': quote['discount_selection_token'],
            'expected_amount': 800, 'client_request_id': 'reservation-updated-monthly'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        monthly.refresh_from_db(); monthly.coupon.refresh_from_db()
        self.assertEqual((monthly.status, monthly.coupon.discount_rate), ('USED', 40))
