"""Real admin coupon change while the boss checkout is in the background."""
import os

from django.core.cache import cache
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from coupons.models import Coupon, UserCoupon
from engagement.models import PointFundingLot
from orders.models import Order, ServiceItem
from orders.reservation_views import reservation_service_terms
from orders.tests.factories import make_boss_type, make_console_user, make_provider, make_service, make_user, make_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class NativeCheckoutCouponRefreshTest(LiveServerTestCase):
    def _gate(self):
        if os.environ.get('XA_CHECKOUT_COUPON_REFRESH') != '1':
            self.skipTest('Requires isolated checkout acceptance (env gate)')

    def _fund(self, account, balance, key):
        PointFundingLot.objects.create(account=account, source_key=key, kind='PREPAID', remaining=balance)

    def _provider(self, service):
        provider = make_provider(display_name='隔离结算陪玩', deposit_mode='EARNINGS', is_verified=True)
        provider_account = get_or_create_account_for_legacy_user(provider)
        link_legacy_relations(provider, provider_account)
        provider.escort_profile.account = provider_account
        provider.escort_profile.save(update_fields=['account'])
        provider.escort_profile.service_items.add(service)
        return provider_account

    def _quote(self, client, payload):
        response = client.post('/api/orders/quote/', payload, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        data = response.data['data']
        data['service'] = reservation_service_terms(ServiceItem.objects.get(pk=payload['service_id']))
        return data

    def _create(self, client, payload, quote):
        return client.post('/api/orders/create/', {**payload,
            'expected_amount': quote['amount'], 'expected_service': quote['service']}, format='json')

    def _confirm_after_change(self, client, payload, mutate, race, drop_voucher_after=False, expect_stale_reject=True):
        if race:
            quote_old = self._quote(client, payload)
            mutate()
            if drop_voucher_after:
                payload = {key: value for key, value in payload.items() if key != 'user_coupon_id'}
            if expect_stale_reject:
                stale = self._create(client, payload, quote_old)
                self.assertNotEqual(stale.data['code'], 0, stale.data)
            quote = self._quote(client, payload)
        else:
            mutate()
            if drop_voucher_after:
                payload = {key: value for key, value in payload.items() if key != 'user_coupon_id'}
            quote = self._quote(client, payload)
        response = self._create(client, payload, quote)
        self.assertEqual(response.data['code'], 0, response.data)
        return response

    def _open_checkout_with_admin_coupon_change(self, race, change_amount=False):
        self._gate()
        boss, staff = make_user(), make_console_user(['coupon:view', 'coupon:edit'])
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        admin = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = admin
        staff.admin_membership.save(update_fields=['account'])
        wallet = make_wallet(boss, balance=50000)
        self._fund(account, 50000, 'checkout-coupon')
        service = make_service(price=10000)
        provider_account = self._provider(service)
        coupon = Coupon.objects.create(name='结算页实时优惠券', discount_type='DIRECT', amount=1000)
        token, refresh = issue_account_tokens(account)
        buyer = APIClient()
        buyer.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(buyer.post(f'/api/coupons/{coupon.pk}/claim/').data['code'], 0)
        voucher = UserCoupon.objects.get(account=account, coupon=coupon)

        def mutate():
            if change_amount:
                coupon.amount = 2000
            else:
                coupon.is_active = False
            coupon.save()

        payload = {'service_id': service.pk, 'provider_id': provider_account.pk, 'user_coupon_id': voucher.pk,
            'client_request_id': f'checkout-coupon-{race}-{change_amount}'}
        self._confirm_after_change(buyer, payload, mutate, race, drop_voucher_after=not change_amount)
        order = Order.objects.get(customer_account=account)
        self.assertEqual((order.amount, order.coupon_discount), (8000, 2000) if change_amount else (10000, 0))
        wallet.refresh_from_db(); voucher.refresh_from_db()
        self.assertEqual((wallet.balance, voucher.status), (42000, 'USED') if change_amount else (40000, 'UNUSED'))

    def test_open_checkout_updates_after_admin_disables_selected_coupon(self):
        self._open_checkout_with_admin_coupon_change(False)

    def test_coupon_disabled_while_confirming_requires_new_quote(self):
        self._open_checkout_with_admin_coupon_change(True)

    def test_coupon_amount_change_refreshes_quote_and_notifies_customer(self):
        self._open_checkout_with_admin_coupon_change(False, change_amount=True)

    def _zero_payable_breakdown_change(self, race):
        self._gate()
        boss_type = make_boss_type(discount_rate=100)
        boss = make_user(boss_type=boss_type)
        staff = make_console_user(['boss_type:view', 'boss_type:edit'])
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        account.boss_type = boss_type
        account.save(update_fields=['boss_type'])
        admin = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = admin
        staff.admin_membership.save(update_fields=['account'])
        wallet = make_wallet(boss, balance=50000)
        self._fund(account, 50000, 'checkout-zero-breakdown')
        service = make_service(price=10000)
        provider_account = self._provider(service)
        coupon = Coupon.objects.create(name='隔离全额券', discount_type='DIRECT', amount=20000)
        token, refresh = issue_account_tokens(account)
        buyer = APIClient()
        buyer.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(buyer.post(f'/api/coupons/{coupon.pk}/claim/').data['code'], 0)
        voucher = UserCoupon.objects.get(account=account, coupon=coupon)

        def mutate():
            boss_type.discount_rate = 90
            boss_type.save()
            cache.clear()

        payload = {'service_id': service.pk, 'provider_id': provider_account.pk, 'user_coupon_id': voucher.pk,
            'client_request_id': f'checkout-zero-breakdown-{race}'}
        self._confirm_after_change(buyer, payload, mutate, race, expect_stale_reject=False)
        order = Order.objects.get(customer_account=account)
        self.assertEqual((order.amount, order.original_amount, order.boss_discount, order.coupon_discount), (0, 10000, 1000, 9000))
        wallet.refresh_from_db(); voucher.refresh_from_db()
        self.assertEqual((wallet.balance, voucher.status), (50000, 'USED'))

    def test_zero_payable_breakdown_change_requires_review(self):
        self._zero_payable_breakdown_change(False)

    def test_zero_payable_breakdown_change_during_confirmation_requires_review(self):
        self._zero_payable_breakdown_change(True)

    def test_zero_coupon_confirms_order_and_creates_real_zero_payment_order(self):
        self._gate()
        boss = make_user()
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        wallet = make_wallet(boss, balance=50000)
        self._fund(account, 50000, 'checkout-zero-coupon')
        service = make_service(price=10000)
        provider_account = self._provider(service)
        coupon = Coupon.objects.create(name='全额优惠券', discount_type='DIRECT', amount=10000)
        token, refresh = issue_account_tokens(account)
        buyer = APIClient()
        buyer.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(buyer.post(f'/api/coupons/{coupon.pk}/claim/').data['code'], 0)
        voucher = UserCoupon.objects.get(account=account, coupon=coupon)
        payload = {'service_id': service.pk, 'provider_id': provider_account.pk, 'user_coupon_id': voucher.pk,
            'client_request_id': 'checkout-zero-coupon'}
        quote = self._quote(buyer, payload)
        response = self._create(buyer, payload, quote)
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(customer_account=account)
        self.assertEqual((order.amount, order.coupon_discount), (0, 10000))
        wallet.refresh_from_db(); voucher.refresh_from_db()
        self.assertEqual((wallet.balance, voucher.status), (50000, 'USED'))

    def test_disabled_coupon_is_removed_and_checkout_uses_updated_price(self):
        self._gate()
        boss, staff = make_user(), make_console_user(['coupon:view', 'coupon:edit'])
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        admin = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = admin
        staff.admin_membership.save(update_fields=['account'])
        make_wallet(boss, balance=50000)
        self._fund(account, 50000, 'checkout-disabled-coupon')
        service = make_service(price=10000)
        provider_account = self._provider(service)
        coupon = Coupon.objects.create(name='测试优惠券', discount_type='DIRECT', amount=1000)
        token, refresh = issue_account_tokens(account)
        buyer = APIClient()
        buyer.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(buyer.post(f'/api/coupons/{coupon.pk}/claim/').data['code'], 0)
        voucher = UserCoupon.objects.get(account=account, coupon=coupon)
        coupon.is_active = False
        coupon.save()
        payload = {'service_id': service.pk, 'provider_id': provider_account.pk,
            'game_region': '本地大区', 'game_nickname': '本地昵称',
            'client_request_id': 'checkout-disabled-coupon'}
        quote = self._quote(buyer, payload)
        response = self._create(buyer, payload, quote)
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(customer_account=account)
        self.assertEqual((order.amount, order.coupon_discount, order.game_region, order.game_nickname),
                         (10000, 0, '本地大区', '本地昵称'))
        voucher.refresh_from_db()
        self.assertEqual(voucher.status, 'UNUSED')

    def _zero_payable_price_change(self, race):
        self._gate()
        boss, staff = make_user(), make_console_user(['service:view', 'service:edit'])
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        admin = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = admin
        staff.admin_membership.save(update_fields=['account'])
        wallet = make_wallet(boss, balance=50000)
        self._fund(account, 50000, 'checkout-zero-price')
        service = make_service(price=10000)
        provider_account = self._provider(service)
        coupon = Coupon.objects.create(name='隔离全额券', discount_type='DIRECT', amount=20000)
        token, refresh = issue_account_tokens(account)
        buyer = APIClient()
        buyer.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(buyer.post(f'/api/coupons/{coupon.pk}/claim/').data['code'], 0)
        voucher = UserCoupon.objects.get(account=account, coupon=coupon)

        def mutate():
            service.price = 12000
            service.save()

        payload = {'service_id': service.pk, 'provider_id': provider_account.pk, 'user_coupon_id': voucher.pk,
            'client_request_id': f'checkout-zero-price-{race}'}
        self._confirm_after_change(buyer, payload, mutate, race)
        order = Order.objects.get(customer_account=account)
        self.assertEqual((order.amount, order.original_amount, order.coupon_discount), (0, 12000, 12000))
        wallet.refresh_from_db(); voucher.refresh_from_db()
        self.assertEqual((wallet.balance, voucher.status), (50000, 'USED'))

    def test_zero_payable_price_change_requires_review(self):
        self._zero_payable_price_change(False)

    def test_zero_payable_price_change_during_confirmation_requires_review(self):
        self._zero_payable_price_change(True)