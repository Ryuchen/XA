from datetime import timedelta

from django.test import override_settings
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from coupons.models import Coupon, UserCoupon
from orders.models import Order, Reservation
from orders.tests.factories import make_boss_type, make_promotion
from orders.tests.test_reservation_api import LIST_URL, ReservationApiTestBase, iso, prepare_reservation_payment, slot
from wallet.models import Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True,
                   BOSS_FUNDING_SELECTION_ENABLED=False, KOOK_ENABLED=False,
                   WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ReservationHourlyPricingTest(ReservationApiTestBase):
    def setUp(self):
        super().setUp()
        self.service.price_unit = '小时'
        self.service.price = 10100  # 1010 store coins per hour
        self.service.save(update_fields=['price_unit', 'price'])
        prepare_reservation_payment(self.boss_account)
        self.boss_client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.boss_account)[0])
        self.provider_client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.provider_account)[0])

    def create_and_confirm(self, minutes, rounds=1):
        start = slot(hour=10)
        result = self.boss_client.post(LIST_URL, self.create_payload(
            start_time=iso(start), end_time=iso(start + timedelta(minutes=minutes)), game_rounds=rounds), format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        path = f"{LIST_URL}{result.data['data']['id']}/"
        confirmed = self.provider_client.post(path + 'confirm/', {}, format='json')
        self.assertEqual(confirmed.data['code'], 0, confirmed.data)
        return result.data['data'], path

    def test_quarter_hour_rounds_up_to_whole_store_coin_and_pays_quote(self):
        reservation, path = self.create_and_confirm(15, rounds=7)
        self.assertEqual(reservation['estimated_amount'], 2530)
        quote = self.boss_client.get(path + 'quote/').data['data']
        self.assertEqual((quote['original_amount'], quote['amount']), (2530, 2530))
        result = self.boss_client.post(path + 'convert/', {
            'discount_selection_token': quote['discount_selection_token'],
            'expected_amount': quote['amount'], 'client_request_id': 'hourly-quarter'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get(pk=result.data['data']['order']['id'])
        self.assertEqual((order.original_amount, order.amount), (2530, 2530))
        self.assertEqual(Wallet.objects.get(account=self.boss_account).balance, 97470)

    def test_one_and_half_hours_and_changed_duration_reject_stale_token(self):
        reservation, path = self.create_and_confirm(90, rounds=1)
        self.assertEqual(reservation['estimated_amount'], 15150)
        quote = self.boss_client.get(path + 'quote/').data['data']
        self.assertEqual(quote['original_amount'], 15150)
        booking = Reservation.objects.get(pk=reservation['id'])
        Reservation.objects.filter(pk=booking.pk).update(
            duration_minutes=60, end_time=booking.start_time + timedelta(minutes=60))
        result = self.boss_client.post(path + 'convert/', {
            'discount_selection_token': quote['discount_selection_token'],
            'expected_amount': quote['amount'], 'client_request_id': 'hourly-stale'}, format='json')
        self.assertNotEqual(result.data.get('code', result.status_code), 0)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(Wallet.objects.get(account=self.boss_account).balance, 100000)

    def test_inconsistent_stored_duration_cannot_be_quoted_or_paid(self):
        reservation, path = self.create_and_confirm(90)
        quote = self.boss_client.get(path + 'quote/').data['data']
        Reservation.objects.filter(pk=reservation['id']).update(duration_minutes=60)
        response = self.boss_client.get(path + 'quote/')
        self.assertEqual(response.status_code, 400)
        result = self.boss_client.post(path + 'convert/', {
            'discount_selection_token': quote['discount_selection_token'],
            'client_request_id': 'hourly-inconsistent'}, format='json')
        self.assertEqual(result.status_code, 400)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(Wallet.objects.get(account=self.boss_account).balance, 100000)

    def test_discount_and_amount_coupon_are_recalculated_on_hourly_price(self):
        self.boss_account.boss_type = make_boss_type(discount_rate=85)
        self.boss_account.save(update_fields=['boss_type'])
        coupon = Coupon.objects.create(name='满减', discount_type='DIRECT', amount=500)
        owned = UserCoupon.objects.create(account=self.boss_account, coupon=coupon)
        reservation, path = self.create_and_confirm(15)
        quote = self.boss_client.get(path + 'quote/').data['data']
        # 253 store coins * 85% rounds up to 216, then minus 50.
        self.assertEqual((quote['original_amount'], quote['amount']), (2530, 1660))
        result = self.boss_client.post(path + 'convert/', {
            'discount_selection_token': quote['discount_selection_token'],
            'expected_amount': 1660, 'client_request_id': 'hourly-coupon'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get(pk=result.data['data']['order']['id'])
        self.assertEqual((order.original_amount, order.amount), (2530, 1660))
        owned.refresh_from_db()
        self.assertEqual((owned.status, owned.order_id), ('USED', order.pk))

    def test_hour_limited_rate_coupon_does_not_discount_extra_half_hour(self):
        coupon = Coupon.objects.create(name='一小时九折', discount_type='RATE', amount=1,
                                       discount_rate=90, limit_units=1)
        owned = UserCoupon.objects.create(account=self.boss_account, coupon=coupon)
        _, path = self.create_and_confirm(90)
        quote = self.boss_client.get(path + 'quote/').data['data']
        choice = next(item for item in quote['discount_options'] if item['coupon_id'] == owned.pk)
        self.assertEqual((choice['original_amount'], choice['coupon_discount'], choice['amount']),
                         (15150, 1010, 14140))
        result = self.boss_client.post(path + 'convert/', {
            'discount_selection_token': choice['selection_token'],
            'expected_amount': choice['amount'], 'client_request_id': 'hourly-capped-coupon'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        self.assertEqual(Order.objects.get(pk=result.data['data']['order']['id']).amount, 14140)

    @override_settings(DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=False)
    def test_legacy_pricing_still_uses_booking_duration(self):
        reservation, path = self.create_and_confirm(90, rounds=2)
        quote = self.boss_client.get(path + 'quote/').data['data']
        self.assertEqual((reservation['estimated_amount'], quote['amount']), (15150, 15150))
        result = self.boss_client.post(path + 'convert/', {
            'expected_amount': quote['amount'], 'client_request_id': 'hourly-legacy'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        self.assertEqual(Order.objects.get(pk=result.data['data']['order']['id']).amount, 15150)

    @override_settings(DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=False)
    def test_legacy_hourly_checkout_selects_one_rate_and_rounds_up(self):
        self.boss_account.boss_type = make_boss_type(discount_rate=85)
        self.boss_account.save(update_fields=['boss_type'])
        make_promotion(title='95折', discount_rate=95)
        _, path = self.create_and_confirm(15)
        quote = self.boss_client.get(path + 'quote/').data['data']
        self.assertEqual((quote['original_amount'], quote['boss_discount'], quote['promo_discount'], quote['amount']),
                         (2530, 370, 0, 2160))
        result = self.boss_client.post(path + 'convert/', {
            'expected_amount': 2160, 'client_request_id': 'hourly-one-rate'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        self.assertEqual(Order.objects.get(pk=result.data['data']['order']['id']).amount, 2160)
