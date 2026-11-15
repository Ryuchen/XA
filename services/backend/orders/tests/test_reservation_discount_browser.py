"""Both reservation interfaces use the isolated ASGI and temporary database."""
import json
import os
from datetime import timedelta
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from coupons.models import Coupon, UserCoupon
from engagement.models import PointFundingLot
from engagement.services import config
from orders.models import Order
from orders.tests.factories import make_boss_type, make_console_user, make_promotion, make_provider, make_service, make_user
from wallet.services import get_wallet
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_DISCOUNT_BROWSER') == '1', 'Requires isolated ASGI and Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ReservationDiscountBrowserTest(LiveServerTestCase):
    def setup_reservation(self):
        user = make_user(); account = get_or_create_account_for_legacy_user(user)
        account.boss_type = make_boss_type(name='隔离预约九折', discount_rate=90); account.save(update_fields=['boss_type'])
        wallet = get_wallet(account=account, user=user); wallet.balance = 20000; wallet.save(update_fields=['balance'])
        PointFundingLot.objects.create(account=account, source_key='browser-reservation-discount', kind='PREPAID', remaining=20000)
        config()
        coupon = Coupon.objects.create(name='隔离预约减300', discount_type='DIRECT', amount=3000)
        owned = UserCoupon.objects.create(account=account, coupon=coupon)
        promo = make_promotion(title='隔离预约八五折', discount_rate=85)
        provider = make_provider(deposit_mode='CASH', deposit_paid=40000)
        recipient = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = recipient; provider.escort_profile.save(update_fields=['account'])
        service = make_service(name='隔离预约优惠服务', price=10000)
        boss = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        player = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(recipient)[0])
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        created = boss.post('/api/orders/reservations/', {'service_id': service.pk, 'provider_account_id': recipient.pk,
            'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=1)).isoformat(), 'game_rounds': 1}, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        reservation = created.data['data']
        self.assertEqual(player.post(f"/api/orders/reservations/{reservation['id']}/confirm/", {}, format='json').data['code'], 0)
        return account, owned, promo, reservation

    def run_browser(self, name, data):
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / f'scripts/{name}.cjs')], cwd=root,
                input=json.dumps({'api': api, **data}), text=True, encoding='utf-8', capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_boss_switches_to_vip(self):
        account, owned, _, reservation = self.setup_reservation()
        self.run_browser('reservation-discount-browser', {'reservation': reservation['id'],
            'token': issue_account_tokens(account)[0], 'account': account.pk})
        order = Order.objects.get(customer_account=account)
        self.assertEqual((order.amount, order.boss_discount, order.coupon_discount), (9000, 1000, 0))
        owned.refresh_from_db(); self.assertEqual(owned.status, 'UNUSED')

    def test_staff_uses_recommended_combination(self):
        account, owned, promo, reservation = self.setup_reservation()
        permissions = ['order:view', 'order:dispatch']
        staff = make_console_user(permissions); staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        self.run_browser('admin-reservation-discount-browser', {'reservation_no': reservation['reservation_no'],
            'staff': {'id': staff_account.pk, 'token': issue_account_tokens(staff_account)[0], 'permissions': permissions}})
        order = Order.objects.get(customer_account=account)
        self.assertEqual((order.amount, order.promo_discount, order.coupon_discount, order.promotion_id), (5500, 1500, 3000, promo.pk))
        self.assertEqual(order.funding_confirmation['actor_account_id'], staff_account.pk)
        owned.refresh_from_db(); self.assertEqual((owned.status, owned.order_id), ('USED', order.pk))
