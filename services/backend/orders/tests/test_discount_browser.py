"""Boss chooses the recommended combination through the real isolated checkout API."""
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
from orders.tests.factories import make_boss_type, make_promotion, make_service, make_user
from wallet.services import get_wallet


@skipUnless(os.environ.get('XA_DISCOUNT_BROWSER') == '1', 'Requires isolated ASGI and boss Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   DISCOUNT_OPTIONS_PREVIEW_ENABLED=True, DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True)
class DiscountBrowserTest(LiveServerTestCase):
    def test_recommendation_can_be_switched_and_paid(self):
        user = make_user(); account = get_or_create_account_for_legacy_user(user)
        account.boss_type = make_boss_type(name='隔离会员九折', discount_rate=90); account.save(update_fields=['boss_type'])
        service = make_service(name='隔离优惠组合服务', price=10000)
        wallet = get_wallet(account=account, user=user); wallet.balance = 20000; wallet.save(update_fields=['balance'])
        PointFundingLot.objects.create(account=account, source_key='browser-discount-principal', kind='PREPAID', remaining=20000)
        config()
        coupon = Coupon.objects.create(name='隔离减300券', discount_type='DIRECT', amount=3000)
        owned = UserCoupon.objects.create(account=account, coupon=coupon)
        promo = make_promotion(title='隔离八五折活动', discount_rate=85)
        client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        preview = client.post('/api/orders/discount-options-preview/', {'service_id': service.pk}, format='json')
        self.assertEqual(preview.data['code'], 0, preview.data)
        options = preview.data['data']['options']
        recommended = next(row for row in options if row['key'] == preview.data['data']['recommended_key'])
        self.assertEqual((recommended['promotion_id'], recommended['coupon_id'], recommended['amount']),
                         (promo.pk, owned.pk, 5500))
        created = client.post('/api/orders/create/', {'service_id': service.pk,
            'discount_selection_token': recommended['selection_token'], 'expected_amount': recommended['amount'],
            'game_region': '隔离大区', 'game_nickname': '隔离老板',
            'client_request_id': 'discount-browser-payment'}, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order = Order.objects.get(customer_account=account)
        self.assertEqual((order.amount, order.promo_discount, order.coupon_discount, order.promotion_id),
                         (5500, 1500, 3000, promo.pk))
        owned.refresh_from_db(); self.assertEqual((owned.status, owned.order_id), ('USED', order.pk))