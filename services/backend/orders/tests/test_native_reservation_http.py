"""Native boss reservation controllers against an isolated real HTTP server."""
import json
from datetime import timedelta

from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from engagement.models import PointFundingLot
from engagement.services import config
from orders.models import Order, Reservation
from orders.tests.factories import make_provider, make_service, make_user, make_wallet
from users.models import EscortProfile
from wallet.models import SystemConfig, Transaction


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class NativeReservationHTTPTest(LiveServerTestCase):
    def test_boss_creates_provider_confirms_and_boss_converts(self):
        self.run_boss_reservation(False)

    def test_changed_price_requires_new_quote_before_conversion(self):
        self.run_boss_reservation(True)

    def run_boss_reservation(self, price_change):
        boss, provider = make_user(), make_provider(deposit_mode='EARNINGS')
        boss_account = get_or_create_account_for_legacy_user(boss)
        provider_account = get_or_create_account_for_legacy_user(provider)
        link_legacy_relations(boss, boss_account)
        link_legacy_relations(provider, provider_account)
        EscortProfile.objects.filter(user=provider).update(account=provider_account)
        wallet = make_wallet(boss, balance=50000)
        wallet.account = boss_account
        wallet.save(update_fields=['account'])
        PointFundingLot.objects.create(account=boss_account, source_key='native-reservation-http', kind='PREPAID', remaining=50000)
        make_wallet(provider, balance=0)
        service = make_service(price=10000)
        token, refresh = issue_account_tokens(boss_account)
        provider_token, _ = issue_account_tokens(provider_account)
        SystemConfig.objects.update_or_create(key='reservation_deadlines', defaults={'value': json.dumps(
            {'confirmation_minutes': 120, 'payment_minutes': 120, 'version': 1,
             'slot_minutes': 15, 'min_duration_minutes': 15, 'max_duration_minutes': 720, 'max_advance_days': 30})})
        config()
        owner = APIClient(); owner.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        player = APIClient(); player.credentials(HTTP_AUTHORIZATION='Bearer ' + provider_token)
        day = (timezone.now() + timedelta(days=1)).replace(minute=0, second=0, microsecond=0)
        def book(hour):
            start = day.replace(hour=hour)
            created = owner.post('/api/orders/reservations/', {'service_id': service.pk, 'provider_account_id': provider_account.pk,
                'start_time': start.isoformat(), 'end_time': (start + timedelta(minutes=15)).isoformat(),
                'game_rounds': 1, 'game_region': '本地区服', 'game_nickname': '预约老板'}, format='json')
            self.assertEqual(created.data['code'], 0, created.data)
            return created.data['data']['id']
        reservation_id = book(10)
        cancelled_id = book(12)
        self.assertEqual(owner.post(f'/api/orders/reservations/{cancelled_id}/cancel/', {}, format='json').data['code'], 0)
        pending_id = book(14)
        self.assertEqual(player.post(f'/api/orders/reservations/{pending_id}/confirm/', {}, format='json').data['code'], 0)
        self.assertEqual(player.post(f'/api/orders/reservations/{reservation_id}/confirm/', {}, format='json').data['code'], 0)
        quote = owner.get(f'/api/orders/reservations/{reservation_id}/quote/').data
        self.assertEqual(quote['code'], 0, quote)
        if price_change:
            stale_amount = quote['data']['amount']
            service.price = 12000
            service.save(update_fields=['price'])
            stale = owner.post(f'/api/orders/reservations/{reservation_id}/convert/',
                {'client_request_id': 'native-reservation-stale', 'expected_amount': stale_amount,
                 'expected_service': quote['data']['service']}, format='json')
            self.assertNotEqual(stale.data['code'], 0, stale.data)
            quote = owner.get(f'/api/orders/reservations/{reservation_id}/quote/').data
            self.assertEqual(quote['code'], 0, quote)
        converted = owner.post(f'/api/orders/reservations/{reservation_id}/convert/',
            {'client_request_id': 'native-reservation-convert', 'expected_amount': quote['data']['amount'],
             'expected_service': quote['data']['service']}, format='json')
        self.assertEqual(converted.data['code'], 0, converted.data)
        reservation = Reservation.objects.get(customer_account=boss_account, status='CONVERTED')
        self.assertEqual(reservation.status, 'CONVERTED')
        self.assertEqual((reservation.order.amount, reservation.order.provider_account_id), (12000 if price_change else 10000, provider_account.pk))
        self.assertEqual(Reservation.objects.filter(customer_account=boss_account, status='CANCELLED').count(), 1)
        self.assertEqual(Reservation.objects.filter(customer_account=boss_account, status='CONFIRMED').count(), 1)
        self.assertEqual(Reservation.objects.filter(customer_account=boss_account).count(), 3)
        self.assertEqual(Order.objects.filter(customer_account=boss_account).count(), 1)
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 38000 if price_change else 40000)
        self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='PAY').count(), 1)