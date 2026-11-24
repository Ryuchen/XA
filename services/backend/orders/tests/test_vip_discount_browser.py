"""Checkout renders effective store rules and pays using real isolated APIs."""
import os
from datetime import timedelta
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from coupons.benefits import grant_periodic
from coupons.models import UserCoupon, VipMonthlyCutover
from engagement.models import PointFundingLot, VipTier
from engagement.services import config
from orders.models import Order
from orders.tests.factories import make_boss_type, make_service, make_user
from wallet.services import get_wallet


@skipUnless(os.environ.get('XA_VIP_TERMS_BROWSER') == '1', 'Requires isolated ASGI and boss Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   VIP_MONTHLY_DECISIONS_ENABLED=True, DISCOUNT_OPTIONS_PREVIEW_ENABLED=True,
                   DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True)
class VipDiscountBrowserTest(LiveServerTestCase):
    def test_updated_safe_box_terms_are_visible_and_paid(self):
        user = make_user(); account = get_or_create_account_for_legacy_user(user)
        wallet = get_wallet(account=account, user=user); wallet.balance = 20000; wallet.save(update_fields=['balance'])
        PointFundingLot.objects.create(account=account, source_key='vip-browser-principal', kind='PREPAID', remaining=20000)
        old = make_service(name='旧安全箱隔离服务', price=10000, benefit_kind='SAFEBOX_33', platform='PC')
        new = make_service(name='新安全箱隔离服务', price=10000, benefit_kind='SAFEBOX_33', platform='PC')
        tier = VipTier.objects.create(level=6, boss_type=make_boss_type(discount_rate=100), recharge_threshold=1,
            monthly_coupon_count=0, monthly_discount=90, season_discount=0, safe_box_service_ids=[old.pk])
        account.boss_type = tier.boss_type; account.save(update_fields=['boss_type']); config()
        VipMonthlyCutover.objects.create(activated_at=timezone.now()-timedelta(minutes=1), platform_actor_id='test', operation_id='vip-browser')
        grant_periodic(account)
        voucher = UserCoupon.objects.get(account=account, coupon__scope='SAFE_BOX')
        tier.season_discount = 50; tier.safe_box_service_ids = [new.pk]
        tier.save(update_fields=['season_discount', 'safe_box_service_ids'])
        client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        preview = client.post('/api/orders/discount-options-preview/', {'service_id': new.pk}, format='json')
        self.assertEqual(preview.data['code'], 0, preview.data)
        safe = next(row for row in preview.data['data']['options'] if row['coupon_id'] == voucher.pk)
        self.assertEqual(safe['amount'], 5000)
        created = client.post('/api/orders/create/', {'service_id': new.pk,
            'discount_selection_token': safe['selection_token'], 'expected_amount': safe['amount'],
            'game_region': '隔离大区', 'game_nickname': '隔离老板',
            'client_request_id': 'vip-browser-payment'}, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order = Order.objects.get(customer_account=account)
        voucher.refresh_from_db(); voucher.coupon.refresh_from_db()
        self.assertEqual((order.amount, voucher.status, voucher.coupon.discount_rate, voucher.coupon.service_ids),
                         (5000, 'USED', 50, [new.pk]))