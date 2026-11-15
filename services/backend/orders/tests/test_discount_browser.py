"""Boss chooses the recommended combination on a real isolated checkout."""
import json
import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from coupons.models import Coupon, UserCoupon
from engagement.models import PointFundingLot
from engagement.services import config
from orders.models import Order
from orders.tests.factories import make_boss_type, make_promotion, make_service, make_user
from wallet.services import get_wallet
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_DISCOUNT_BROWSER') == '1', 'Requires isolated ASGI and boss Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
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
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/discount-checkout-browser.cjs')], cwd=root,
                input=json.dumps({'api': api, 'service': service.pk, 'token': issue_account_tokens(account)[0],
                                  'account': account.pk}), text=True, encoding='utf-8', capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        order = Order.objects.get(customer_account=account)
        self.assertEqual((order.amount, order.promo_discount, order.coupon_discount, order.promotion_id),
                         (5500, 1500, 3000, promo.pk))
        owned.refresh_from_db(); self.assertEqual((owned.status, owned.order_id), ('USED', order.pk))
