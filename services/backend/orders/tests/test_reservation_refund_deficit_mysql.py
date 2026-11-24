"""Real MySQL serialization of a verified refund-deposit deficit and reservation payment."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue
from time import monotonic, sleep
from unittest import skipUnless

from django.conf import settings
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from common.mysql_test_support import mysql_locking_statements, require_disposable_mysql
from console.reservation_deadlines import save_deadlines
from orders.models import Order, Reservation, ReservationSeat
from orders.reservation_deadlines import current_rules
from orders.tests.factories import make_completed_order, make_provider, make_service, make_user
from orders.tests.test_reservation_api import prepare_reservation_payment
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.models import Store
from users.models import EscortProfile
from wallet.models import IdempotencyKey, RefundDepositRecoveryPermit, Transaction, get_platform_wallet
from wallet.receipts import fingerprint_for
from wallet.services import get_wallet


@skipUnless(connection.vendor == 'mysql', 'requires an isolated MySQL server')
@override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=False,
                   LEGACY_FORCE_AUTH_COMPAT=False, PROVIDER_WECHAT_GATE_ENABLED=False,
                   ALLOWED_HOSTS=['sl.esports.club', 'xa.esports.club'],
                   CELERY_TASK_ALWAYS_EAGER=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ReservationRefundDeficitMySQLTest(TransactionTestCase):
    store_routes = (
        (('shenglang', 'sl.esports.club', 'store_a'), ('xinan', 'xa.esports.club', 'store_b'))
        if settings.ISOLATED_CONTROL_PLANE else (('shenglang', 'sl.esports.club', 'default'),)
    )
    databases = {'default', *(alias for _, _, alias in store_routes)}

    def test_refund_deficit_commit_serializes_before_reservation_payment(self):
        for alias in self.databases:
            require_disposable_mysql(connections[alias])
        for slug, host, alias in self.store_routes:
            Store.objects.update_or_create(slug=slug, defaults={
                'name': slug, 'database_alias': alias, 'status': Store.Status.ACTIVE,
                'initialized_at': timezone.now(),
            })
            store = resolve_store(slug)
            with store_context(store):
                save_deadlines(30, 60, current_rules()['version'], lambda *_: None)
            for provider_count in (1, 2):
                with self.subTest(store=slug, provider_count=provider_count), store_context(store):
                    self.assert_payment_serializes(store, host, provider_count)

    def assert_payment_serializes(self, store, host, provider_count):
        from wallet.test_refund_deposit_deficit import RefundDepositDeficitTest

        self.boss = make_user()
        boss = get_or_create_account_for_legacy_user(self.boss)
        link_legacy_relations(self.boss, boss)
        buyer_wallet = get_wallet(user=self.boss, account=boss)
        buyer_wallet.balance = 10000
        buyer_wallet.save(update_fields=['balance'])
        principal = prepare_reservation_payment(boss)

        providers = []
        for _ in range(provider_count):
            user = make_provider(deposit_mode='EARNINGS', deposit_paid=10000)
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            EscortProfile.objects.filter(user=user).update(account=account)
            providers.append((user, account))
        # Exercise the second seat in double-provider bookings.
        self.provider, self.account = providers[-1]
        self.profile = EscortProfile.objects.get(account=self.account)
        self.wallet = get_wallet(user=self.provider, account=self.account)
        self.platform = get_platform_wallet()
        self.platform.balance = 10000
        self.platform.save(update_fields=['balance'])
        self.order = make_completed_order(self.boss, self.provider, amount=10000,
            provider_account=self.account, payment_status='PAID')

        service = make_service(price=1000, billing_provider_count=1,
                               fulfillment_provider_count=provider_count, price_unit='份')
        token = issue_account_tokens(boss)[0]
        owner = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token)
        start = (timezone.now() + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
        response = owner.post('/api/orders/reservations/', {
            'service_id': service.pk, 'provider_account_ids': [account.pk for _, account in providers],
            'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=1)).isoformat(),
        }, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        rid = response.data['data']['id']
        reservation_path = f'/api/orders/reservations/{rid}/'
        for _, account in providers:
            worker = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            response = worker.post(reservation_path + 'confirm/', {}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
        reservation = Reservation.objects.get(pk=rid)
        confirmed_at = (ReservationSeat.objects.get(reservation=reservation, provider_account=self.account).confirmed_at
                        if provider_count == 2 else reservation.confirmed_at)
        response = owner.get(reservation_path + 'quote/')
        self.assertEqual(response.data['code'], 0, response.data)
        quote = response.data['data']
        self.assertEqual(quote['amount'], 1000)
        convert_path = reservation_path + 'convert/'
        payload = {'client_request_id': f'mysql-d200-{rid}', 'expected_amount': quote['amount'],
                   'expected_service': quote['service']}
        observer = connections[store.database_alias]
        waiting_sessions = Queue()

        def observe_account_lock(execute, sql, params, many, context):
            if ('`club_accounts_clubaccount`' in sql.lower() and 'FOR UPDATE' in sql.upper()
                    and self.account.pk in (params or ())):
                waiting_sessions.put(context['connection'].connection.thread_id())
            return execute(sql, params, many, context)

        def pay():
            close_old_connections()
            try:
                connection_for_payment = connections[store.database_alias]
                with store_context(store), connection_for_payment.execute_wrapper(observe_account_lock):
                    client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token)
                    return client.post(convert_path, payload, format='json').data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                EscortProfile.objects.select_for_update().get(pk=self.profile.pk)
                # InnoDB also retains the account's foreign-key lock after recording the gap.
                gap = RefundDepositDeficitTest.deficit(self, 200)
                self.assertGreater(gap.created_at, confirmed_at)
                future = pool.submit(pay)
                deadline = monotonic() + 6
                while monotonic() < deadline:
                    if not waiting_sessions.empty():
                        payment_session = waiting_sessions.get()
                        break
                    if future.done():
                        self.fail(f'付款未到达陪玩账号锁: {future.result()}')
                    sleep(0.05)
                else:
                    self.fail('付款未请求目标陪玩账号锁')
                while monotonic() < deadline:
                    if mysql_locking_statements(observer, 'club_accounts_clubaccount', session_id=payment_session):
                        break
                    if future.done():
                        self.fail(f'付款未等待陪玩账号锁: {future.result()}')
                    sleep(0.05)
                else:
                    self.fail('未观察到付款连接等待目标账号锁')
                self.assertFalse(future.done())
                buyer_wallet.refresh_from_db()
                principal.refresh_from_db()
                self.assertEqual((buyer_wallet.balance, principal.remaining), (10000, 10000))
                self.assertFalse(IdempotencyKey.objects.filter(account=boss, request_id=payload['client_request_id']).exists())
            result = future.result(timeout=15)

        self.assertEqual(result['code'], 0, result)
        oid = result['data']['order']['id']
        replay = owner.post(convert_path, payload, format='json').data
        self.assertEqual((replay['code'], replay['errorCode']), (409, 'FUND_ALREADY_PROCESSED'))
        self.assertEqual(replay['fundResult'], result)
        conflict = owner.post(convert_path, {**payload, 'expected_amount': 1010}, format='json').data
        self.assertEqual((conflict['code'], conflict['errorCode']), (409, 'REQUEST_ID_CONFLICT'))
        another = owner.post(convert_path, {**payload, 'client_request_id': f'mysql-d200-new-{rid}'}, format='json').data
        self.assertEqual(another['code'], 409, another)

        reservation.refresh_from_db()
        buyer_wallet.refresh_from_db()
        principal.refresh_from_db()
        gap.refresh_from_db()
        self.assertEqual((reservation.status, reservation.order_id), (Reservation.Status.CONVERTED, oid))
        self.assertEqual(Order.objects.filter(customer_account=boss).count(), 1)
        self.assertEqual((buyer_wallet.balance, principal.remaining, gap.remaining), (9000, 9000, 200))
        self.assertEqual(list(Transaction.objects.filter(
            wallet=buyer_wallet, tx_type='PAY',
        ).values_list('order_id', 'amount', 'balance_before', 'balance_after', 'status')),
            [(oid, -1000, 10000, 9000, 'SUCCESS')])
        self.assertFalse(RefundDepositRecoveryPermit.objects.filter(account=self.account).exists())
        receipt = IdempotencyKey.objects.get(account=boss)
        self.assertEqual((receipt.scope, receipt.request_id, receipt.response),
                         ('reservation.convert', payload['client_request_id'], result))
        self.assertEqual(receipt.request_fingerprint, fingerprint_for(convert_path, payload))
        print(f'D200 {store.slug} seats={provider_count} reservation={rid} paid_order={oid} '
              f'refund_order={self.order.pk} gap={gap.pk} waited_for_account=1 '
              f'pay=-1000 principal=9000 deficit=200 replay=409', flush=True)
