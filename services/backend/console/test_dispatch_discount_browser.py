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
from orders.tests.factories import make_boss_type, make_console_user, make_promotion, make_service, make_user
from wallet.services import get_wallet
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_DISCOUNT_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
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
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/admin-dispatch-discount-browser.cjs')], cwd=root,
                input=json.dumps({'api': api, 'staff': {'id': operator.pk, 'token': issue_account_tokens(operator)[0],
                                                        'permissions': permissions}}),
                text=True, encoding='utf-8', capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        order = Order.objects.get(customer_account=boss, service=service)
        self.assertEqual((order.amount, order.boss_discount, order.coupon_discount), (9000, 1000, 0))
        self.assertEqual(order.funding_confirmation['actor_account_id'], operator.pk)
        owned.refresh_from_db(); self.assertEqual(owned.status, 'UNUSED')
