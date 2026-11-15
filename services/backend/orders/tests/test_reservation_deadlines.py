"""Actual booking, confirmation, payment expiry and store configuration."""
from datetime import timedelta
from unittest.mock import patch
from unittest import skipUnless

from django.conf import settings
from django.db import IntegrityError
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.reservation_deadlines import save_deadlines
from orders.models import Order, Reservation
from orders.reservation_expiration import expire_due_reservations
from orders.reservation_services import find_conflicting_reservations
from orders.tests.test_reservation_api import ReservationApiTestBase
from orders.tests.factories import make_provider, make_service, make_user
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.tests_hosts import xinan_store
from wallet.models import Transaction, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, CELERY_TASK_ALWAYS_EAGER=False)
class ReservationDeadlineWorkflowTest(ReservationApiTestBase):
    def setUp(self):
        super().setUp()
        from orders.reservation_deadlines import CONFIG_KEY
        from wallet.models import SystemConfig
        SystemConfig.objects.filter(key=CONFIG_KEY).delete()
        self.client.force_authenticate(user=None)
        self.start_clock = timezone.now().replace(second=0, microsecond=0)
        self.start_clock -= timedelta(minutes=self.start_clock.minute % 15)
        self.start = self.start_clock + timedelta(hours=2)

    def login(self, account):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])

    def create(self, start=None):
        self.login(self.boss_account)
        start = start or self.start
        payload = self.create_payload(start_time=start.isoformat(), end_time=(start+timedelta(hours=1)).isoformat(),
                                      game_rounds=1)
        return self.client.post('/api/orders/reservations/', payload, format='json')

    def configure(self, confirmation=30, payment=45, version=0):
        return save_deadlines(confirmation, payment, version, lambda *_: None)

    def test_unconfigured_store_rejects_new_booking_without_locking_a_slot(self):
        with patch('django.utils.timezone.now', return_value=self.start_clock):
            response = self.create()
        self.assertNotEqual(response.data['code'], 0)
        self.assertIn('尚未配置预约', response.data['msg'])
        self.assertFalse(Reservation.objects.exists())

    def test_confirmation_and_payment_snapshot_their_respective_config_versions(self):
        self.configure(30, 45)
        with patch('django.utils.timezone.now', return_value=self.start_clock):
            result = self.create()
        self.assertEqual(result.data['code'], 0, result.data)
        booking = Reservation.objects.get(pk=result.data['data']['id'])
        self.assertEqual((booking.confirmation_deadline, booking.confirmation_timeout_minutes,
                          booking.confirmation_config_version),
                         (self.start_clock + timedelta(minutes=30), 30, 1))
        self.configure(15, 25, version=1)
        self.login(self.provider_account)
        confirmed_at = self.start_clock + timedelta(minutes=10)
        with patch('django.utils.timezone.now', return_value=confirmed_at):
            response = self.client.post(f'/api/orders/reservations/{booking.pk}/confirm/', {}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        booking.refresh_from_db()
        self.assertEqual((booking.confirmation_deadline, booking.payment_deadline,
                          booking.payment_timeout_minutes, booking.payment_config_version),
                         (self.start_clock+timedelta(minutes=30), confirmed_at+timedelta(minutes=25), 25, 2))
        self.configure(5, 5, version=2)
        self.login(self.boss_account)
        balance = Wallet.objects.get(account=self.boss_account).balance
        with patch('django.utils.timezone.now', return_value=booking.payment_deadline):
            result = self.client.post(f'/api/orders/reservations/{booking.pk}/convert/',
                                      {'client_request_id': 'expired-reservation'}, format='json')
        self.assertNotEqual(result.data['code'], 0)
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'EXPIRED')
        self.assertEqual(Wallet.objects.get(account=self.boss_account).balance, balance)
        self.assertFalse(Order.objects.exists())
        self.assertFalse(Transaction.objects.filter(tx_type='PAY').exists())

    def test_late_confirmation_expires_and_releases_slot(self):
        self.configure()
        with patch('django.utils.timezone.now', return_value=self.start_clock):
            result = self.create()
        booking = Reservation.objects.get(pk=result.data['data']['id'])
        self.login(self.provider_account)
        with patch('django.utils.timezone.now', return_value=booking.confirmation_deadline):
            result = self.client.post(f'/api/orders/reservations/{booking.pk}/confirm/', {}, format='json')
            self.assertNotEqual(result.data['code'], 0)
            self.assertFalse(find_conflicting_reservations(self.provider_account, self.start,
                                                           self.start+timedelta(hours=1)).exists())
            self.assertEqual(expire_due_reservations(), 0)  # detail already expired it once
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'EXPIRED')

    def test_confirmation_crossing_cutoff_before_status_write_does_not_confirm(self):
        self.configure()
        with patch('django.utils.timezone.now', return_value=self.start_clock):
            result = self.create()
        booking = Reservation.objects.get(pk=result.data['data']['id'])
        self.login(self.provider_account)
        from orders.reservation_state_machine import transition_reservation as actual_transition
        with patch('django.utils.timezone.now', return_value=self.start_clock+timedelta(minutes=29)) as clock:
            def transition_after_deadline(*args, **kwargs):
                original_check = kwargs['pre_check']
                def check_and_advance(reservation):
                    original_check(reservation)
                    clock.return_value = booking.confirmation_deadline
                kwargs['pre_check'] = check_and_advance
                return actual_transition(*args, **kwargs)
            with patch('orders.reservation_views.transition_reservation', side_effect=transition_after_deadline):
                response = self.client.post(f'/api/orders/reservations/{booking.pk}/confirm/', {}, format='json')
        self.assertNotEqual(response.data['code'], 0)
        booking.refresh_from_db()
        self.assertEqual((booking.status, booking.confirmed_at, booking.payment_deadline), ('EXPIRED', None, None))

    def test_confirmation_window_and_payment_deadline_cannot_extend_past_start(self):
        self.configure(30, 60)
        with patch('django.utils.timezone.now', return_value=self.start_clock):
            too_close = self.create(start=self.start_clock+timedelta(minutes=15))
            self.assertNotEqual(too_close.data['code'], 0)
            self.assertIn('不足本店确认时限', too_close.data['msg'])
            result = self.create(start=self.start_clock+timedelta(minutes=45))
        booking = Reservation.objects.get(pk=result.data['data']['id'])
        self.login(self.provider_account)
        with patch('django.utils.timezone.now', return_value=self.start_clock+timedelta(minutes=29)):
            response = self.client.post(f'/api/orders/reservations/{booking.pk}/confirm/', {}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        booking.refresh_from_db()
        self.assertEqual(booking.payment_deadline, booking.start_time)

    def test_confirmed_booking_converts_once_before_payment_deadline(self):
        self.configure()
        with patch('django.utils.timezone.now', return_value=self.start_clock):
            result = self.create()
        booking = Reservation.objects.get(pk=result.data['data']['id'])
        self.login(self.provider_account)
        with patch('django.utils.timezone.now', return_value=self.start_clock+timedelta(minutes=10)):
            result = self.client.post(f'/api/orders/reservations/{booking.pk}/confirm/', {}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        self.login(self.boss_account)
        balance = Wallet.objects.get(account=self.boss_account).balance
        with patch('django.utils.timezone.now', return_value=self.start_clock+timedelta(minutes=20)):
            result = self.client.post(f'/api/orders/reservations/{booking.pk}/convert/',
                                      {'client_request_id': 'on-time-reservation'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'CONVERTED')
        self.assertEqual(Wallet.objects.get(account=self.boss_account).balance, balance-1000)
        self.assertEqual(Transaction.objects.filter(order=booking.order, tx_type='PAY').count(), 1)
        self.assertEqual(booking.order.provider_account_id, self.provider_account.pk)

    def test_payment_deadline_during_locked_checkout_rolls_back_idempotency_claim(self):
        self.configure()
        with patch('django.utils.timezone.now', return_value=self.start_clock):
            response = self.create()
        booking = Reservation.objects.get(pk=response.data['data']['id'])
        self.login(self.provider_account)
        with patch('django.utils.timezone.now', return_value=self.start_clock+timedelta(minutes=10)):
            response = self.client.post(f'/api/orders/reservations/{booking.pk}/confirm/', {}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        booking.refresh_from_db()
        self.login(self.boss_account)
        balance = Wallet.objects.get(account=self.boss_account).balance
        from wallet.models import IdempotencyKey
        from wallet.services import claim_fund_write as actual_claim
        with patch('django.utils.timezone.now', return_value=self.start_clock+timedelta(minutes=20)) as clock:
            def claim_then_expire(*args, **kwargs):
                result = actual_claim(*args, **kwargs)
                clock.return_value = booking.payment_deadline
                return result
            with patch('orders.reservation_services.claim_fund_write', side_effect=claim_then_expire):
                result = self.client.post(f'/api/orders/reservations/{booking.pk}/convert/',
                    {'client_request_id': 'expires-during-checkout'}, format='json')
        self.assertNotEqual(result.data['code'], 0)
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'EXPIRED')
        self.assertEqual(Wallet.objects.get(account=self.boss_account).balance, balance)
        self.assertFalse(IdempotencyKey.objects.filter(account=self.boss_account,
                                                       request_id='expires-during-checkout').exists())
        self.assertFalse(Order.objects.exists())
        self.assertFalse(Transaction.objects.filter(tx_type='PAY').exists())

    def test_store_staff_cannot_confirm_a_booking_for_the_provider(self):
        self.configure()
        with patch('django.utils.timezone.now', return_value=self.start_clock):
            result = self.create()
        booking = Reservation.objects.get(pk=result.data['data']['id'])
        self.login(self.staff_account)
        with patch('django.utils.timezone.now', return_value=self.start_clock):
            result = self.client.post(f'/api/orders/reservations/{booking.pk}/confirm/', {}, format='json')
        self.assertEqual(result.status_code, 403)
        booking.refresh_from_db()
        self.assertEqual((booking.status, booking.payment_deadline), ('PENDING', None))

    def test_database_rejects_partial_new_snapshot(self):
        self.configure()
        with patch('django.utils.timezone.now', return_value=self.start_clock):
            response = self.create()
        booking_id = response.data['data']['id']
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Reservation.objects.filter(pk=booking_id).update(confirmation_config_version=None)
        self.assertEqual(Reservation.objects.get(pk=booking_id).confirmation_config_version, 1)


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated stores')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], CELERY_TASK_ALWAYS_EAGER=False)
class ReservationDeadlineStoreTest(TransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    reset_sequences = True

    def test_same_number_bookings_use_only_their_own_store_config_and_expiration(self):
        entries = []
        start = (timezone.now()+timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
        for store, host, minutes in ((resolve_store(), 'sl.esports.club', 30),
                                     (xinan_store(), 'xa.esports.club', 60)):
            with store_context(store):
                save_deadlines(minutes, 90, 0, lambda *_: None)
                boss = make_user()
                boss_account = get_or_create_account_for_legacy_user(boss)
                link_legacy_relations(boss, boss_account)
                provider = make_provider(deposit_mode='EARNINGS')
                provider_account = get_or_create_account_for_legacy_user(provider)
                link_legacy_relations(provider, provider_account)
                provider.escort_profile.account = provider_account
                provider.escort_profile.save(update_fields=['account'])
                service = make_service(price=1000)
                token = issue_account_tokens(boss_account)[0]
                ids = (provider_account.pk, service.pk)
            client = APIClient(HTTP_AUTHORIZATION='Bearer ' + token)
            response = client.post('/api/orders/reservations/', {'provider_account_id': ids[0], 'service_id': ids[1],
                'start_time': start.isoformat(), 'end_time': (start+timedelta(hours=1)).isoformat()},
                format='json', HTTP_HOST=host)
            self.assertEqual(response.data['code'], 0, response.data)
            entries.append((store, host, token, response.data['data']['id'], minutes))
        self.assertEqual(entries[0][3], entries[1][3])
        with store_context(entries[1][0]):
            Reservation.objects.filter(pk=entries[1][3]).update(confirmation_deadline=timezone.now()-timedelta(seconds=1))
            self.assertEqual(expire_due_reservations(), 1)
        for store, host, token, booking_id, minutes in entries:
            client = APIClient(HTTP_AUTHORIZATION='Bearer ' + token)
            other = entries[1 if host == entries[0][1] else 0][1]
            self.assertNotEqual(client.get(f'/api/orders/reservations/{booking_id}/', HTTP_HOST=other).status_code, 200)
            response = client.get(f'/api/orders/reservations/{booking_id}/', HTTP_HOST=host)
            self.assertEqual(response.data['code'], 0, response.data)
            self.assertEqual(response.data['data']['confirmation_timeout_minutes'], minutes)
            self.assertEqual(response.data['data']['status'], 'PENDING' if minutes == 30 else 'EXPIRED')
