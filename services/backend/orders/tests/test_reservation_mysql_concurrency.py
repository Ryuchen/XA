"""Real MySQL races across booking, state transition and wallet debit."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from time import monotonic, sleep
from unittest import skipUnless

from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, Reservation
from orders.tests.factories import make_provider, make_service, make_user, make_wallet
from users.models import EscortProfile
from wallet.models import Transaction, Wallet

URL = '/api/orders/reservations/'


def competing_lock_waits(observer):
    """Observe real InnoDB waits on the isolated database from another connection."""
    with observer.cursor() as cursor:
        cursor.execute('SHOW ENGINE INNODB STATUS')
        status = cursor.fetchone()[2]
    transactions = status.split('LIST OF TRANSACTIONS FOR EACH SESSION:', 1)[1].split('FILE I/O', 1)[0]
    waiting = [entry for entry in transactions.split('---TRANSACTION') if '\nLOCK WAIT ' in entry]
    wallets = sum('`wallet_wallet`' in entry for entry in waiting)
    providers = sum('`orders_providerdispatchlock`' in entry for entry in waiting)
    return wallets, providers


@skipUnless(connection.vendor == 'mysql', 'Requires real MySQL row locks')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class ReservationMySQLConcurrencyTest(TransactionTestCase):
    def setUp(self):
        self.boss = make_user()
        self.other = make_user()
        self.provider = make_provider()
        self.boss_account = self.bind(self.boss)
        self.other_account = self.bind(self.other)
        self.provider_account = self.bind(self.provider)
        EscortProfile.objects.filter(user=self.provider).update(account=self.provider_account)
        self.wallet = make_wallet(self.boss, balance=100000)
        self.wallet.account = self.boss_account
        self.wallet.save(update_fields=['account'])
        self.service = make_service(price=1000)
        from orders.timeout import current_timeout_config
        current_timeout_config()
        from engagement.services import config
        config()
        self.tokens = {account.pk: issue_account_tokens(account)[0] for account in
                       (self.boss_account, self.other_account, self.provider_account)}
        start = (timezone.now() + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
        self.payload = {'provider_account_id': self.provider_account.pk, 'service_id': self.service.pk,
                        'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=2)).isoformat(),
                        'game_rounds': 2}

    @staticmethod
    def bind(user):
        account = get_or_create_account_for_legacy_user(user)
        link_legacy_relations(user, account)
        return account

    def post(self, account_id, path, data):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + self.tokens[account_id])
        return client.post(path, data, format='json').data

    def race(self, requests):
        barrier = Barrier(len(requests))
        def worker(args):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return self.post(*args)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(requests)) as pool:
            return list(pool.map(worker, requests))

    def create(self):
        response = self.post(self.boss_account.pk, URL, self.payload)
        self.assertEqual(response['code'], 0, response)
        return response['data']['id']

    def test_concurrent_customers_cannot_reserve_same_provider_slot(self):
        results = self.race([(account.pk, URL, self.payload) for account in (self.boss_account, self.other_account)])
        self.assertEqual(sorted(result['code'] for result in results), [0, 409], results)
        self.assertEqual(Reservation.objects.filter(provider_account=self.provider_account, status='PENDING').count(), 1)

    def test_concurrent_conversions_create_one_order_and_debit_once(self):
        reservation_id = self.create()
        self.assertEqual(self.post(self.provider_account.pk, f'{URL}{reservation_id}/confirm/', {})['code'], 0)
        path = f'{URL}{reservation_id}/convert/'
        results = self.race([(self.boss_account.pk, path, {'client_request_id': key}) for key in ('race-one', 'race-two')])
        self.assertEqual(sum(result['code'] == 0 for result in results), 1, results)
        self.assertEqual(Order.objects.count(), 1)
        reservation = Reservation.objects.get(pk=reservation_id)
        self.assertEqual(reservation.status, 'CONVERTED')
        self.assertEqual(reservation.order.amount, 2000)
        self.assertEqual(Wallet.objects.get(pk=self.wallet.pk).balance, 98000)
        self.assertEqual(Transaction.objects.filter(order=reservation.order, tx_type='PAY').count(), 1)

    def test_concurrent_confirmation_and_cancellation_have_one_result(self):
        reservation_id = self.create()
        results = self.race([
            (self.provider_account.pk, f'{URL}{reservation_id}/confirm/', {}),
            (self.boss_account.pk, f'{URL}{reservation_id}/cancel/', {'reason': '时间变更'}),
        ])
        # Cancel may legitimately follow confirmation; either terminal result
        # must be consistent with both successful operations and state logs.
        reservation = Reservation.objects.get(pk=reservation_id)
        self.assertEqual(reservation.status, 'CANCELLED')
        self.assertTrue(any(result['code'] == 0 for result in results), results)
        if all(result['code'] == 0 for result in results):
            self.assertTrue(reservation.confirmed_at)
        self.assertTrue(reservation.cancelled_at)
        self.assertIsNone(reservation.order_id)
        self.assertEqual(Wallet.objects.get(pk=self.wallet.pk).balance, 100000)

    def test_confirmation_error_uses_the_locked_current_state_after_cancel(self):
        reservation_id = self.create()
        def confirm():
            close_old_connections()
            try:
                return self.post(self.provider_account.pk, f'{URL}{reservation_id}/confirm/', {})
            finally:
                close_old_connections()

        observer = connection.copy()
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            with transaction.atomic():
                Reservation.objects.select_for_update().get(pk=reservation_id)
                future = pool.submit(confirm)
                deadline = monotonic() + 4
                waiting = False
                while monotonic() < deadline:
                    with observer.cursor() as cursor:
                        cursor.execute('SHOW ENGINE INNODB STATUS')
                        status = cursor.fetchone()[2]
                    entries = status.split('LIST OF TRANSACTIONS FOR EACH SESSION:', 1)[1].split('FILE I/O', 1)[0]
                    waiting = any('\nLOCK WAIT ' in entry and '`orders_reservation`' in entry
                                  for entry in entries.split('---TRANSACTION'))
                    if waiting or future.done():
                        break
                    sleep(.05)
                self.assertTrue(waiting, 'Confirmation must wait on the real reservation row lock')
                cancelled = self.post(self.boss_account.pk, f'{URL}{reservation_id}/cancel/', {'reason': '时间变更'})
                self.assertEqual(cancelled['code'], 0, cancelled)
            rejected = future.result(timeout=20)
        finally:
            observer.close()
            pool.shutdown(wait=True)
        self.assertEqual(rejected['code'], 409, rejected)
        self.assertIn('已取消', rejected['msg'])
        self.assertNotIn('待确认', rejected['msg'])
        self.assertEqual(Reservation.objects.get(pk=reservation_id).status, Reservation.Status.CANCELLED)

    def test_two_confirmed_reservations_compete_for_same_provider(self):
        second_wallet = make_wallet(self.other, balance=100000)
        second_wallet.account = self.other_account
        second_wallet.save(update_fields=['account'])
        other_service = make_service(name='另一服务', price=1200)
        first = self.create()
        later = dict(self.payload, service_id=other_service.pk)
        start = timezone.datetime.fromisoformat(later['start_time']) + timedelta(days=1)
        later.update(start_time=start.isoformat(), end_time=(start + timedelta(hours=2)).isoformat())
        response = self.post(self.other_account.pk, URL, later)
        self.assertEqual(response['code'], 0, response)
        second = response['data']['id']
        for reservation_id in (first, second):
            self.assertEqual(self.post(self.provider_account.pk, f'{URL}{reservation_id}/confirm/', {})['code'], 0)

        # Holding separate real buyer wallet locks makes both requests wait
        # after their eligibility checks instead of relying on thread timing.
        barrier = Barrier(2)
        def worker(account_id, reservation_id):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return self.post(account_id, f'{URL}{reservation_id}/convert/', {'client_request_id': f'cap-{reservation_id}'})
            finally:
                close_old_connections()
        pool = ThreadPoolExecutor(max_workers=2)
        observer = connection.copy()
        try:
            with transaction.atomic():
                list(Wallet.objects.filter(pk__in=[self.wallet.pk, second_wallet.pk]).order_by('pk').select_for_update())
                futures = [pool.submit(worker, self.boss_account.pk, first), pool.submit(worker, self.other_account.pk, second)]
                # One request can wait for the provider lock while the other
                # waits on its buyer wallet. Observe both waits, not just
                # wallet queries, and release before InnoDB's 5s test timeout.
                deadline = monotonic() + 4
                waits = (0, 0)
                while monotonic() < deadline:
                    waits = competing_lock_waits(observer)
                    if (waits[0] >= 1 and sum(waits) >= 2) or any(future.done() for future in futures):
                        break
                    sleep(.05)
                self.assertGreaterEqual(waits[0], 1, f'Expected a wallet lock wait: {waits!r}')
                self.assertGreaterEqual(sum(waits), 2, f'Both requests must reach a real lock wait: {waits!r}')
            results = [future.result(timeout=20) for future in futures]
        finally:
            observer.close()
            pool.shutdown(wait=True)
        self.assertLessEqual(sum(result['code'] == 0 for result in results), 1, (results, waits))
        self.assertEqual(Order.objects.filter(provider_account=self.provider_account, status='GRABBED').count(), 1)
        self.assertEqual(Transaction.objects.filter(tx_type='PAY').count(), 1)

    def test_reservation_and_direct_order_share_provider_capacity(self):
        other_wallet = make_wallet(self.other, balance=100000)
        other_wallet.account = self.other_account
        other_wallet.save(update_fields=['account'])
        other_service = make_service(name='普通下单服务', price=1200)
        reservation_id = self.create()
        self.assertEqual(self.post(self.provider_account.pk, f'{URL}{reservation_id}/confirm/', {})['code'], 0)
        barrier = Barrier(2)
        def worker(account_id, path, payload):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return self.post(account_id, path, payload)
            finally:
                close_old_connections()
        pool = ThreadPoolExecutor(max_workers=2)
        observer = connection.copy()
        try:
            with transaction.atomic():
                list(Wallet.objects.filter(pk__in=[self.wallet.pk, other_wallet.pk]).order_by('pk').select_for_update())
                futures = [
                    pool.submit(worker, self.boss_account.pk, f'{URL}{reservation_id}/convert/', {'client_request_id': 'reserve-competition'}),
                    pool.submit(worker, self.other_account.pk, '/api/orders/create/', {'service_id': other_service.pk,
                        'provider_ids': [self.provider_account.pk], 'client_request_id': 'direct-competition'}),
                ]
                # Requests serialize on the shared provider as well as the
                # independently locked buyer wallets.
                deadline = monotonic() + 4
                waits = (0, 0)
                while monotonic() < deadline:
                    waits = competing_lock_waits(observer)
                    if (waits[0] >= 1 and sum(waits) >= 2) or any(future.done() for future in futures):
                        break
                    sleep(.05)
                self.assertGreaterEqual(waits[0], 1, f'Expected a wallet lock wait: {waits!r}')
                self.assertGreaterEqual(sum(waits), 2, f'Both requests must reach a real lock wait: {waits!r}')
            results = [future.result(timeout=20) for future in futures]
        finally:
            observer.close()
            pool.shutdown(wait=True)
        self.assertEqual(sum(result['code'] == 0 for result in results), 1, (results, waits))
        self.assertEqual(Order.objects.filter(provider_account=self.provider_account, status='GRABBED').count(), 1)
        self.assertEqual(Transaction.objects.filter(tx_type='PAY').count(), 1)
