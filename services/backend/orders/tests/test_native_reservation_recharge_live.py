"""Native page controller and real backend: recharge while a quote is insufficient."""
import json
from datetime import timedelta
from tempfile import TemporaryDirectory

from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from engagement.services import config
from orders.models import Order, Reservation
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from orders.tests.test_release_flow import screenshot
from wallet.models import SystemConfig, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, REQUIRE_RECHARGE_PROOF=True)
class NativeReservationRechargeTest(LiveServerTestCase):
    host = '127.0.0.1'

    def test_recharge_refreshes_quote_and_converts(self):
        self.run_journey('unchanged')

    def test_recharge_refreshes_changed_price_before_conversion(self):
        self.run_journey('price_changed')

    def test_cancelled_reservation_does_not_show_old_quote_after_return(self):
        self.run_journey('cancelled')

    def run_journey(self, scenario):
        with TemporaryDirectory(prefix='xa-reservation-recharge-') as media, override_settings(MEDIA_ROOT=media):
            boss, provider = make_user(), make_provider(deposit_mode='EARNINGS')
            staff = make_console_user(['wallet:recharge', 'service:edit', 'order:view', 'dashboard:view'])
            accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, staff)]
            for user, account in zip((boss, provider, staff), accounts):
                link_legacy_relations(user, account)
            provider.escort_profile.account = accounts[1]
            provider.escort_profile.save(update_fields=['account'])
            staff.admin_membership.account = accounts[2]
            staff.admin_membership.save(update_fields=['account'])
            service = make_service(price=10000)
            tokens = [issue_account_tokens(account)[0] for account in accounts]
            SystemConfig.objects.update_or_create(key='reservation_deadlines', defaults={'value': json.dumps(
                {'confirmation_minutes': 120, 'payment_minutes': 120, 'version': 1,
                 'slot_minutes': 15, 'min_duration_minutes': 15, 'max_duration_minutes': 720, 'max_advance_days': 30})})
            config()
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
            start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
            result = client.post('/api/orders/reservations/', {'provider_account_id': accounts[1].pk,
                'service_id': service.pk, 'start_time': start.isoformat(),
                'end_time': (start + timedelta(hours=1)).isoformat()}, format='json').data
            self.assertEqual(result['code'], 0, result)
            booking_id = result['data']['id']
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[1])
            result = client.post(f'/api/orders/reservations/{booking_id}/confirm/', {}, format='json').data
            self.assertEqual(result['code'], 0, result)
            amount = 12000 if scenario == 'price_changed' else 10000
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[2])
            recharge = client.post('/api/admin/wallets/recharge/', {'account_id': accounts[0].pk,
                'amount': amount, 'gift_amount': 0, 'trade_no': 'native-reservation-local-recharge',
                'client_request_id': 'native-reservation-local-recharge', 'proof_image': screenshot()}, format='multipart')
            self.assertEqual(recharge.data['code'], 0, recharge.data)
            if scenario == 'price_changed':
                service.price = 12000
                service.save(update_fields=['price'])
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
            if scenario == 'cancelled':
                cancelled = client.post(f'/api/orders/reservations/{booking_id}/cancel/', {'reason': '隔离预约取消'}, format='json')
                self.assertEqual(cancelled.data['code'], 0, cancelled.data)
            else:
                quote = client.get(f'/api/orders/reservations/{booking_id}/quote/').data
                self.assertEqual(quote['code'], 0, quote)
                self.assertEqual(quote['data']['amount'], amount)
                converted = client.post(f'/api/orders/reservations/{booking_id}/convert/',
                    {'client_request_id': 'native-reservation-recharge-convert', 'expected_amount': quote['data']['amount'],
                     'expected_service': quote['data']['service']}, format='json')
                self.assertEqual(converted.data['code'], 0, converted.data)
            booking = Reservation.objects.get(pk=booking_id)
            if scenario == 'cancelled':
                self.assertEqual(booking.status, 'CANCELLED')
                self.assertIsNone(booking.order_id)
                self.assertEqual(Wallet.objects.get(account=accounts[0]).balance, 10000)
                return
            self.assertEqual(booking.status, 'CONVERTED')
            amount = 12000 if scenario == 'price_changed' else 10000
            order = Order.objects.get(pk=booking.order_id)
            self.assertEqual((order.amount, order.provider_account_id), (amount, accounts[1].pk))
            self.assertEqual(Wallet.objects.get(account=accounts[0]).balance, 0)
            for token in (tokens[0], tokens[1]):
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                response = client.get(f'/api/orders/orders/{order.pk}/').data
                self.assertEqual(response['code'], 0, response)
                detail = response['data']
                self.assertEqual((detail['amount'], detail['status'], detail['reservation_origin']['id']),
                                 (amount, 'GRABBED', booking_id))
                self.assertEqual(timezone.datetime.fromisoformat(detail['reservation_origin']['start_time']), start)
            notices = client.get('/api/messages/').data['data']['messages']
            self.assertTrue(any(row['title'] == '预约已转为订单' and row['related_order_id'] == order.pk
                                and '原预约时段' in row['preview'] for row in notices))
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[2])
            response = client.get(f'/api/admin/orders/{order.pk}/').data
            self.assertEqual(response['code'], 0, response)
            detail = response['data']
            self.assertEqual((detail['amount'], detail['reservation_origin']['id']), (amount, booking_id))
            summary = client.get('/api/admin/dashboard/stats').data
            self.assertEqual(summary['code'], 0, summary)
            self.assertEqual((summary['data']['paid_order_total'], summary['data']['paid_amount_total']), (1, amount))