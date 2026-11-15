"""Checkout renders effective store rules and pays using real isolated APIs."""
import json
import os
from datetime import timedelta
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from coupons.benefits import grant_periodic
from coupons.models import UserCoupon, VipMonthlyCutover
from engagement.models import PointFundingLot, VipTier
from engagement.services import config
from orders.models import Order
from orders.tests.factories import make_boss_type, make_service, make_user
from wallet.services import get_wallet
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_VIP_TERMS_BROWSER') == '1', 'Requires isolated ASGI and boss Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
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
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/vip-discount-browser.cjs')], cwd=root,
                input=json.dumps({'api':api, 'service':new.pk, 'token':issue_account_tokens(account)[0], 'account':account.pk}),
                text=True, encoding='utf-8', capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        order = Order.objects.get(customer_account=account)
        voucher.refresh_from_db(); voucher.coupon.refresh_from_db()
        self.assertEqual((order.amount, voucher.status, voucher.coupon.discount_rate, voucher.coupon.service_ids),
                         (5000, 'USED', 50, [new.pk]))
