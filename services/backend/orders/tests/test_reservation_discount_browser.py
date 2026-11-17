"""Both reservation interfaces use the isolated ASGI and temporary database."""
import json
import json
import os
import urllib.parse
import urllib.request
from datetime import timedelta
from unittest import skipUnless

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
from wallet.models import SystemConfig
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
        SystemConfig.objects.update_or_create(key='reservation_deadlines', defaults={'value': json.dumps(
            {'confirmation_minutes': 120, 'payment_minutes': 120, 'version': 1,
             'slot_minutes': 15, 'min_duration_minutes': 15, 'max_duration_minutes': 720, 'max_advance_days': 30})})
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

    def run_convert(self, api, host, token, reservation_id, query, payload, option_key=None):
        headers = {'Host': host, 'Authorization': f'Bearer {token}'}
        quote_url = api + f'/api/orders/reservations/{reservation_id}/quote/?' + urllib.parse.urlencode(query)
        with urllib.request.urlopen(urllib.request.Request(quote_url, headers=headers), timeout=30) as response:
            quote = json.load(response)
        self.assertEqual(quote['code'], 0, quote)
        if option_key:
            selection = next(item['selection_token'] for item in quote['data']['discount_options'] if item['key'] == option_key)
        else:
            selection = quote['data']['discount_selection_token']
        body = {**payload, 'discount_selection_token': selection,
                'client_request_id': f'resdiscount-{reservation_id}-{quote["data"].get("discount_option_key") or "plain"}'}
        request = urllib.request.Request(api + f'/api/orders/reservations/{reservation_id}/convert/',
            data=json.dumps(body).encode(), headers={**headers, 'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(request, timeout=30) as response:
            converted = json.load(response)
        self.assertEqual(converted['code'], 0, converted)

    def test_boss_switches_to_vip(self):
        account, owned, _, reservation = self.setup_reservation()
        with single_asgi() as (api, _):
            self.run_convert(api, 'sl.esports.club', issue_account_tokens(account)[0], reservation['id'],
                {}, {}, option_key='vip')
        order = Order.objects.get(customer_account=account)
        self.assertEqual((order.amount, order.boss_discount, order.coupon_discount), (9000, 1000, 0))
        owned.refresh_from_db(); self.assertEqual(owned.status, 'UNUSED')

    def test_staff_uses_recommended_combination(self):
        account, owned, promo, reservation = self.setup_reservation()
        permissions = ['order:view', 'order:dispatch']
        staff = make_console_user(permissions); staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        with single_asgi() as (api, _):
            self.run_convert(api, 'admin.esports.club', issue_account_tokens(staff_account)[0], reservation['id'],
                {'user_coupon_id': owned.pk}, {'boss_confirmation': '老板确认推荐优惠组合'})
        order = Order.objects.get(customer_account=account)
        self.assertEqual((order.amount, order.promo_discount, order.coupon_discount, order.promotion_id), (5500, 1500, 3000, promo.pk))
        self.assertEqual(order.funding_confirmation['actor_account_id'], staff_account.pk)
        owned.refresh_from_db(); self.assertEqual((owned.status, owned.order_id), ('USED', order.pk))
