"""Real reservation API and wallet behaviour when the booked window ends."""
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Reservation, ReservationStatusLog
from orders.tests.factories import make_console_user
from orders.tests.test_reservation_api import LIST_URL, ReservationApiTestBase
from wallet.models import Transaction, Wallet, IdempotencyKey


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ReservationExpirationTest(ReservationApiTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=None)
        self.boss_client = self.account_client(self.boss_account)
        self.provider_client = self.account_client(self.provider_account)
        self.staff_client = self.account_client(self.staff_account)

    def account_client(self, account):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        return client

    def create(self):
        result = self.boss_client.post(LIST_URL, self.create_payload(), format='json').data
        self.assertEqual(result['code'], 0, result)
        return result['data']['id']

    def end_window(self, reservation_id):
        now = timezone.now()
        Reservation.objects.filter(pk=reservation_id).update(start_time=now - timedelta(hours=2), end_time=now - timedelta(hours=1))

    def test_pending_expires_for_both_roles_and_cannot_be_confirmed(self):
        reservation_id = self.create()
        self.end_window(reservation_id)
        result = self.provider_client.post(f'{LIST_URL}{reservation_id}/confirm/', {}, format='json').data
        self.assertEqual(result['code'], 409, result)
        for client in (self.boss_client, self.provider_client, self.staff_client):
            detail = client.get(f'{LIST_URL}{reservation_id}/').data
            self.assertEqual(detail['data']['status'], 'EXPIRED')
            self.assertNotIn('confirm', detail['data']['allowed_actions'])
        self.assertEqual(ReservationStatusLog.objects.filter(reservation_id=reservation_id, action='EXPIRE').count(), 1)

    def test_confirmed_expiration_cannot_quote_convert_or_debit(self):
        reservation_id = self.create()
        self.assertEqual(self.provider_client.post(f'{LIST_URL}{reservation_id}/confirm/', {}, format='json').data['code'], 0)
        self.end_window(reservation_id)
        before = Wallet.objects.get(user=self.boss).balance
        self.assertEqual(self.boss_client.get(f'{LIST_URL}{reservation_id}/quote/').data['code'], 409)
        result = self.boss_client.post(f'{LIST_URL}{reservation_id}/convert/',
                                       {'client_request_id': 'expired-reservation-test'}, format='json').data
        self.assertEqual(result['code'], 409, result)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, before)
        self.assertFalse(Transaction.objects.filter(wallet__user=self.boss, tx_type='PAY').exists())
        self.assertFalse(IdempotencyKey.objects.filter(account=self.boss_account, request_id='expired-reservation-test').exists())
        self.assertIsNone(Reservation.objects.get(pk=reservation_id).order_id)

    def test_scan_and_workbench_expire_both_active_states_once(self):
        pending_id = self.create()
        from orders.tests.test_reservation_api import slot
        confirmed = self.boss_client.post(LIST_URL, self.create_payload(
            start_time=slot(hour=13).isoformat(), end_time=slot(hour=15).isoformat()), format='json').data
        self.assertEqual(confirmed['code'], 0, confirmed)
        confirmed_id = confirmed['data']['id']
        self.assertEqual(self.provider_client.post(f'{LIST_URL}{confirmed_id}/confirm/', {}, format='json').data['code'], 0)
        self.end_window(pending_id)
        self.end_window(confirmed_id)
        staff = make_console_user(['dashboard:view', 'order:view'])
        account = get_or_create_account_for_legacy_user(staff)
        link_legacy_relations(staff, account)
        staff.admin_membership.account = account
        staff.admin_membership.save(update_fields=['account'])
        stats = self.account_client(account).get('/api/admin/dashboard/stats').data['data']
        self.assertEqual((stats['pending_reservation_total'], stats['confirmed_reservation_total']), (0, 0))
        self.assertEqual(self.boss_client.get(LIST_URL, {'status': 'expired'}).data['data']['total'], 2)
        call_command('expire_reservations', verbosity=0)
        self.assertEqual(ReservationStatusLog.objects.filter(reservation_id__in=[pending_id, confirmed_id], action='EXPIRE').count(), 2)

    def test_exact_end_is_expired_but_one_microsecond_before_is_not(self):
        from orders.reservation_expiration import expire_reservation
        reservation_id = self.create()
        deadline = timezone.now() + timedelta(hours=1)
        Reservation.objects.filter(pk=reservation_id).update(start_time=deadline - timedelta(hours=1), end_time=deadline)
        with patch('django.utils.timezone.now', return_value=deadline - timedelta(microseconds=1)):
            self.assertFalse(expire_reservation(reservation_id))
        with patch('django.utils.timezone.now', return_value=deadline):
            self.assertTrue(expire_reservation(reservation_id))
        self.assertEqual(Reservation.objects.get(pk=reservation_id).status, 'EXPIRED')

    def test_scheduled_scan_records_one_notice_for_each_participant(self):
        from orders.tasks import expire_reservations
        from site_messages.models import Message
        reservation_id = self.create()
        self.end_window(reservation_id)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(expire_reservations.run(), 1)
        self.assertEqual(expire_reservations.run(), 0)
        self.assertEqual(Reservation.objects.get(pk=reservation_id).status, 'EXPIRED')
        notices = Message.objects.filter(title='预约已过期')
        self.assertEqual(set(notices.values_list('recipient_account_id', flat=True)),
                         {self.boss_account.pk, self.provider_account.pk})
        self.assertEqual(notices.count(), 2)
