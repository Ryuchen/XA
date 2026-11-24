import os
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from coupons.models import Coupon, UserCoupon
from engagement.models import PointFundingLot
from engagement.services import config
from orders.models import Order
from orders.tests.factories import make_boss_type, make_console_user, make_promotion, make_service, make_user
from wallet.services import get_wallet


@skipUnless(os.environ.get('XA_DISCOUNT_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=True)
class DispatchDiscountBrowserTest(LiveServerTestCase):
    def test_staff_switches_and_confirms_boss_discount(self):
        user = make_user(nickname='隔离优惠代单老板'); boss = get_or_create_account_for_legacy_user(user)
        boss.boss_type = make_boss_type(name='隔离代单九折', discount_rate=90); boss.save(update_fields=['boss_type'])
        wallet = get_wallet(account=boss, user=user); wallet.balance = 20000; wallet.save(update_fields=['balance'])
        PointFundingLot.objects.create(account=boss, source_key='browser-dispatch-discount', kind='PREPAID', remaining=20000)
        config()
        service = make_service(name='隔离代单优惠服务', price=10000)
        make_promotion(title='隔离代单八五折', discount_rate=85)
        coupon = Coupon.objects.create(name='隔离代单减300', discount_type='DIRECT', amount=3000)
        owned = UserCoupon.objects.create(account=boss, coupon=coupon)
        permissions = ['order:view', 'order:dispatch']
        staff = make_console_user(permissions); operator = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = operator; staff.admin_membership.save(update_fields=['account'])
        admin = APIClient()
        admin.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(operator)[0])
        params = {'customer_id': boss.pk, 'service_id': service.pk, 'game_rounds': 1}
        first = admin.get('/api/admin/orders/dispatch-quote/', params)
        self.assertEqual(first.data['code'], 0, first.data)
        vip = next(item for item in first.data['data']['discount_options'] if item['key'] == 'vip')
        quote = admin.get('/api/admin/orders/dispatch-quote/', {**params, 'discount_selection_token': vip['selection_token']})
        self.assertEqual(quote.data['code'], 0, quote.data)
        self.assertEqual((quote.data['data']['amount'], quote.data['data']['discount_option_key']), (9000, 'vip'))
        response = admin.post('/api/admin/orders/dispatch/', {**params,
            'expected_amount': quote.data['data']['amount'],
            'discount_selection_token': quote.data['data']['discount_selection_token'],
            'boss_confirmation_method': '隔离代单确认', 'boss_confirmation': '老板确认会员九折',
            'client_request_id': 'browser-dispatch-discount'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(customer_account=boss, service=service)
        self.assertEqual((order.amount, order.boss_discount, order.coupon_discount), (9000, 1000, 0))
        self.assertEqual(order.funding_confirmation['actor_account_id'], operator.pk)
        owned.refresh_from_db(); self.assertEqual(owned.status, 'UNUSED')