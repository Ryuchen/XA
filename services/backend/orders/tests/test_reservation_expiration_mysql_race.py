"""A request waiting on the row lock must observe the expired deadline."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from time import monotonic, sleep
from unittest import skipUnless

from django.db import close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Reservation
from orders.tests.factories import make_provider, make_service, make_user, make_wallet
from wallet.models import IdempotencyKey, Transaction, Wallet


@skipUnless(connection.vendor == 'mysql', 'Requires real MySQL row locks')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ReservationExpirationMySQLRaceTest(TransactionTestCase):
    def test_confirm_and_convert_waiting_on_lock_expire_without_debit(self):
        boss, provider = make_user(), make_provider()
        boss_account, provider_account = (get_or_create_account_for_legacy_user(user) for user in (boss, provider))
        for user, account in ((boss, boss_account), (provider, provider_account)):
            link_legacy_relations(user, account)
        provider.escort_profile.account = provider_account
        provider.escort_profile.save(update_fields=['account'])
        wallet = make_wallet(boss, balance=50000)
        wallet.account = boss_account
        wallet.save(update_fields=['account'])
        service = make_service(price=10000)
        clients = []
        tokens = [issue_account_tokens(account)[0] for account in (boss_account, provider_account)]
        for token in tokens:
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
            clients.append(client)
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)

        def make_appointment(hour_offset):
            response = clients[0].post('/api/orders/reservations/', {
                'provider_account_id': provider_account.pk, 'service_id': service.pk,
                'start_time': (start + timedelta(hours=hour_offset)).isoformat(),
                'end_time': (start + timedelta(hours=hour_offset + 1)).isoformat(),
                'game_rounds': 1,
            }, format='json').data
            self.assertEqual(response['code'], 0, response)
            return response['data']['id']

        def post(token, url, data):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                return client.post(url, data, format='json').data
            finally:
                close_old_connections()

        def wait_locked_request(reservation_id, token, path, payload):
            observer = connections['default'].copy()
            with ThreadPoolExecutor(max_workers=1) as pool:
                try:
                    with transaction.atomic():
                        reservation = Reservation.objects.select_for_update().get(pk=reservation_id)
                        deadline = timezone.now() + timedelta(seconds=2)
                        reservation.start_time = deadline - timedelta(hours=1)
                        reservation.end_time = deadline
                        reservation.save(update_fields=['start_time', 'end_time'])
                        # Commit the new time before locking again, so the waiting
                        # request sees the same real deadline as the lock holder.
                    with transaction.atomic():
                        Reservation.objects.select_for_update().get(pk=reservation_id)
                        future = pool.submit(post, token, path, payload)
                        timeout = monotonic() + 10
                        while monotonic() < timeout:
                            with observer.cursor() as cursor:
                                cursor.execute('SHOW ENGINE INNODB STATUS')
                                status = cursor.fetchone()[2]
                            if 'LOCK WAIT' in status and '`orders_reservation`' in status:
                                break
                            sleep(.02)
                        else:
                            self.fail('request did not wait for the reservation lock')
                        while timezone.now() <= deadline:
                            sleep(.02)
                    return future.result(timeout=20)
                finally:
                    observer.close()

        pending_id = make_appointment(0)
        response = wait_locked_request(pending_id, tokens[1], f'/api/orders/reservations/{pending_id}/confirm/', {})
        self.assertEqual(response['code'], 409, response)
        self.assertEqual(Reservation.objects.get(pk=pending_id).status, 'EXPIRED')
        confirmed_id = make_appointment(2)
        self.assertEqual(clients[1].post(f'/api/orders/reservations/{confirmed_id}/confirm/', {}, format='json').data['code'], 0)
        response = wait_locked_request(confirmed_id, tokens[0], f'/api/orders/reservations/{confirmed_id}/convert/',
                                       {'client_request_id': 'expired-while-waiting'})
        self.assertEqual(response['code'], 409, response)
        confirmed = Reservation.objects.get(pk=confirmed_id)
        self.assertEqual(confirmed.status, 'EXPIRED')
        self.assertIsNone(confirmed.order_id)
        self.assertEqual(Wallet.objects.get(pk=wallet.pk).balance, 50000)
        self.assertFalse(Transaction.objects.filter(wallet=wallet, tx_type='PAY').exists())
        self.assertFalse(IdempotencyKey.objects.filter(account=boss_account, request_id='expired-while-waiting').exists())

        # Two readers can both first observe an expired ACTIVE row. The loser
        # of the expiry lock must re-read the winner's committed state.
        read_id = make_appointment(4)
        Reservation.objects.filter(pk=read_id).update(start_time=timezone.now() - timedelta(hours=2),
                                                      end_time=timezone.now() - timedelta(hours=1))
        def read(token):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                return client.get(f'/api/orders/reservations/{read_id}/').data
            finally:
                close_old_connections()
        observer = connections['default'].copy()
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                with transaction.atomic():
                    Reservation.objects.select_for_update().get(pk=read_id)
                    futures = [pool.submit(read, token) for token in tokens]
                    deadline = monotonic() + 10
                    while monotonic() < deadline:
                        with observer.cursor() as cursor:
                            cursor.execute('SHOW ENGINE INNODB STATUS')
                            status = cursor.fetchone()[2]
                        sections = status.split('LIST OF TRANSACTIONS FOR EACH SESSION:', 1)[1].split('FILE I/O', 1)[0]
                        waiting = sum('LOCK WAIT' in part and '`orders_reservation`' in part
                                      for part in sections.split('---TRANSACTION'))
                        if waiting >= 2:
                            break
                        sleep(.02)
                    else:
                        self.fail('both readers did not wait on the reservation lock')
                for future in futures:
                    result = future.result(timeout=20)
                    self.assertEqual(result['data']['status'], 'EXPIRED', result)
        finally:
            observer.close()

        # A request may acquire the reservation before expiry but then wait
        # for the wallet; recheck the deadline immediately before the debit.
        late_id = make_appointment(6)
        self.assertEqual(clients[1].post(f'/api/orders/reservations/{late_id}/confirm/', {}, format='json').data['code'], 0)
        deadline = timezone.now() + timedelta(seconds=2)
        Reservation.objects.filter(pk=late_id).update(start_time=deadline - timedelta(hours=1), end_time=deadline)
        observer = connections['default'].copy()
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                with transaction.atomic():
                    Wallet.objects.select_for_update().get(pk=wallet.pk)
                    future = pool.submit(post, tokens[0], f'/api/orders/reservations/{late_id}/convert/',
                                         {'client_request_id': 'expired-waiting-wallet'})
                    timeout = monotonic() + 10
                    while monotonic() < timeout:
                        with observer.cursor() as cursor:
                            cursor.execute('SHOW ENGINE INNODB STATUS')
                            status = cursor.fetchone()[2]
                        if 'LOCK WAIT' in status and '`wallet_wallet`' in status:
                            break
                        sleep(.02)
                    else:
                        self.fail('conversion did not wait for the wallet lock')
                    while timezone.now() <= deadline:
                        sleep(.02)
                result = future.result(timeout=20)
            self.assertEqual(result['code'], 409, result)
            self.assertEqual(Reservation.objects.get(pk=late_id).status, 'EXPIRED')
            self.assertEqual(Wallet.objects.get(pk=wallet.pk).balance, 50000)
            self.assertFalse(IdempotencyKey.objects.filter(account=boss_account, request_id='expired-waiting-wallet').exists())
        finally:
            observer.close()
