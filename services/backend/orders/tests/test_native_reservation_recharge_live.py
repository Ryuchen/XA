"""Native page controller and real backend: recharge while a quote is insufficient."""
import json
import base64
import subprocess
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import LiveServerTestCase, override_settings
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, Reservation
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from orders.tests.test_release_flow import screenshot
from wallet.models import Wallet


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
            boss, provider = make_user(), make_provider()
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
            repo = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(repo / 'scripts/native-reservation-recharge-live.cjs')], cwd=repo,
                input=json.dumps({'api': self.live_server_url, 'bookingId': booking_id, 'bossId': accounts[0].pk,
                                  'bossToken': tokens[0], 'staffToken': tokens[2], 'scenario': scenario,
                                  'serviceId': service.pk,
                                  'proof': base64.b64encode(screenshot().read()).decode('ascii')}),
                text=True, encoding='utf-8', capture_output=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
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
