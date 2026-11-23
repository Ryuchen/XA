"""Same-wallet reservation payment and an actual negotiated deposit-backed refund."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Empty, Queue
from threading import Event
from time import monotonic, sleep
from unittest import skipUnless

from django.conf import settings
from django.db import OperationalError, connection, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from common.mysql_test_support import mysql_locking_statements, require_disposable_mysql
from console.reservation_deadlines import save_deadlines
from engagement.models import PointFundingLot
from orders.models import Order, Reservation
from orders.reservation_deadlines import current_rules
from orders.tests import test_ordinary_full_refund_deposit as deposit_flow
from orders.tests import test_release_flow as flow
from stores.context import resolve_store, store_context
from stores.models import Store
from wallet.models import RefundDepositDeficit, RefundDepositRecoveryPermit, Transaction
from wallet.services import get_wallet


@skipUnless(connection.vendor == 'mysql', 'requires isolated MySQL row locks')
@override_settings(**{**deposit_flow.OrdinaryFullRefundDepositTest._overridden_settings,
                      'REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED': False})
class ReservationRefundSerializationMySQLTest(TransactionTestCase):
    store_routes = (
        (('shenglang', 'sl.esports.club', 'store_a'), ('xinan', 'xa.esports.club', 'store_b'))
        if settings.ISOLATED_CONTROL_PLANE else (('shenglang', 'sl.esports.club', 'default'),)
    )
    databases = {'default', *(alias for _, _, alias in store_routes)}

    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge

    def test_reservation_and_negotiated_refund_serialize_on_same_buyer_wallet(self):
        for alias in self.databases:
            require_disposable_mysql(connections[alias])
        for slug, host, alias in self.store_routes:
            with self.subTest(store=slug):
                Store.objects.update_or_create(slug=slug, defaults={
                    'name': slug, 'database_alias': alias, 'status': Store.Status.ACTIVE,
                    'initialized_at': timezone.now(),
                })
                store = resolve_store(slug)
                with store_context(store):
                    self.client = APIClient(HTTP_HOST=host)
                    flow.ReleaseFlowTest.setUp(self)
                    self.exercise(store, host, refund_first=slug == 'shenglang')

    def prepare_refund(self):
        player = self.accounts[self.provider.pk]
        profile = self.provider.escort_profile
        profile.deposit_mode, profile.deposit_paid = 'CASH', 0
        profile.save(update_fields=['deposit_mode', 'deposit_paid'])
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'support:edit', 'escort:deposit_topup', 'deposit_rule:edit']
        role.save(update_fields=['permissions'])
        self.login(self.admin)
        self.ok(self.client.put('/api/admin/config/deposit-targets', {
            'cash_target': 2000, 'earnings_target': 2500, 'expected_version': 0,
        }, format='json'))
        self.ok(self.recharge())
        self.ok(self.client.post(f'/api/admin/escorts/{profile.pk}/deposit-topups/', {
            'amount': 2000, 'reason': '隔离押金登记', 'reference': 'reservation-race-deposit',
            'client_request_id': 'reservation-race-deposit',
        }, format='multipart'))
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': player.pk,
            'support_contact_id': self.card.pk, 'client_request_id': 'reservation-race-old-order',
        }, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {
            'entry_image': flow.screenshot(),
        }, format='multipart'))
        self.login(self.admin)
        case_id = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {
            'order_id': order_id, 'affected_provider_account_ids': [player.pk],
            'reason': '隔离全额退款', 'client_request_id': 'reservation-race-case',
        }, format='json'))['id']
        case_path = f'/api/admin/aftersales/{case_id}/'
        proposal = self.ok(self.client.post(case_path + 'negotiation-proposal/', {
            'request_id': 'reservation-race-proposal', 'expected_version': 0, 'refund_units': 10000,
            'provider_terms': [{'account_id': player.pk, 'retained_units': 0}], 'explanation': '隔离全额退款',
        }, format='json'))
        for user in (self.boss, self.provider):
            self.login(user)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/', {
                'proposal_id': proposal['id'], 'accepted': True,
            }, format='json'))
        return order_id, case_path + 'negotiation-execution/', {
            'proposal_id': proposal['id'], 'client_request_id': 'reservation-race-refund',
        }

    def prepare_reservation(self):
        save_deadlines(30, 60, current_rules()['version'], lambda *_: None)
        start = (timezone.now() + timedelta(days=2)).replace(hour=12, minute=0, second=0, microsecond=0)
        self.login(self.boss)
        reservation_id = self.ok(self.client.post('/api/orders/reservations/', {
            'service_id': self.service.pk, 'provider_account_id': self.accounts[self.provider.pk].pk,
            'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=1)).isoformat(),
        }, format='json'))['id']
        path = f'/api/orders/reservations/{reservation_id}/'
        self.login(self.provider)
        self.ok(self.client.post(path + 'confirm/', {}, format='json'))
        self.login(self.boss)
        quote = self.ok(self.client.get(path + 'quote/'))
        self.assertEqual(quote['amount'], 10000)
        return reservation_id, path + 'convert/', {
            'client_request_id': 'reservation-race-payment',
            'expected_amount': quote['amount'], 'expected_service': quote['service'],
        }

    def exercise(self, store, host, *, refund_first):
        order_id, refund_path, refund_payload = self.prepare_refund()
        reservation_id, payment_path, payment_payload = self.prepare_reservation()
        buyer = self.accounts[self.boss.pk]
        staff = self.accounts[self.admin.pk]
        player = self.accounts[self.provider.pk]
        tokens = [issue_account_tokens(account)[0] for account in (staff, buyer)]
        wallet = get_wallet(user=self.boss, account=buyer)
        funding = PointFundingLot.objects.get(account=buyer, kind=PointFundingLot.Kind.RECHARGE)
        self.assertEqual((wallet.balance, funding.remaining, funding.gift_remaining), (11000, 11000, 1000))
        observer = connections[store.database_alias]
        first_has_lock = Event()
        resume_first = Event()
        attempted = Queue()

        def pause_first(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if refund_first:
                lock_held = ('`wallet_wallet`' in sql.lower()
                             and order_id in (params or ()) and 'PAY' in (params or ()))
            else:
                lock_held = '`users_escortprofile`' in sql.lower() and player.pk in (params or ())
            if lock_held and 'FOR UPDATE' in sql.upper():
                first_has_lock.set()
                if not resume_first.wait(8):
                    raise AssertionError('首个操作等待竞争语句观测超时')
            return result

        def observe_second(execute, sql, params, many, context):
            has_write_lock = 'FOR UPDATE' in sql.upper() or sql.lstrip().upper().startswith('INSERT')
            for table in ('orders_orderfinanciallock', 'users_escortprofile', 'club_accounts_clubaccount', 'wallet_wallet'):
                if has_write_lock and f'`{table}`' in sql.lower():
                    attempted.put((table, context['connection'].connection.thread_id()))
            return execute(sql, params, many, context)

        def post(token, path, payload, wrapper):
            dbs = connections[store.database_alias]
            try:
                with store_context(store), dbs.execute_wrapper(wrapper):
                    client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token)
                    return client.post(path, payload, format='json').data
            except OperationalError as exc:
                return {'database_error': exc.args[0]}
            finally:
                connections.close_all()

        refund_request = (tokens[0], refund_path, refund_payload)
        payment_request = (tokens[1], payment_path, payment_payload)
        first_request, second_request = ((refund_request, payment_request) if refund_first
                                         else (payment_request, refund_request))
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(post, *first_request, pause_first)
            try:
                self.assertTrue(first_has_lock.wait(4), first.result() if first.done() else '未观察到首个操作已持锁')
                second = pool.submit(post, *second_request, observe_second)
                deadline = monotonic() + 4
                candidates = set()
                while monotonic() < deadline:
                    try:
                        candidates.add(attempted.get_nowait())
                    except Empty:
                        pass
                    waiting = next((table for table, session_id in candidates
                        if mysql_locking_statements(observer, table, session_id=session_id)), None)
                    if waiting:
                        break
                    if second.done():
                        self.fail(f'第二个操作未进入锁等待: {second.result()}')
                    sleep(0.04)
                else:
                    self.fail(f'未观察到实际锁等待: {candidates}')
                self.assertFalse(second.done())
                wallet.refresh_from_db()
                self.assertEqual(wallet.balance, 11000)
                self.assertFalse(Order.objects.get(pk=order_id).refund_records.exists())
                self.assertIsNone(Reservation.objects.get(pk=reservation_id).order_id)
            finally:
                resume_first.set()
            first_result = first.result(timeout=15)
            second_result = second.result(timeout=15)
            result_refund, result_payment = ((first_result, second_result) if refund_first
                                             else (second_result, first_result))

        print(f'{store.slug} old_order={order_id} reservation={reservation_id} refund_first={refund_first} wait={waiting} '
              f'refund={result_refund.get("database_error", result_refund.get("code"))} '
              f'payment={result_payment.get("database_error", result_payment.get("code"))}', flush=True)
        self.assertEqual(waiting, 'orders_orderfinanciallock')
        self.assertEqual(result_refund.get('code'), 0, result_refund)
        self.assertEqual(result_payment.get('code'), 0, result_payment)
        sale = Order.objects.get(pk=order_id)
        reservation = Reservation.objects.get(pk=reservation_id)
        self.assertEqual((sale.payment_status, reservation.status),
                         (Order.PaymentStatus.REFUNDED, Reservation.Status.CONVERTED))
        gap = RefundDepositDeficit.objects.get(refund__order=sale, account=player)
        self.assertEqual((gap.source_kind, gap.amount, gap.remaining), ('DIRECT', 2000, 2000))
        share = gap.refund.negotiation_execution.shares.get(account=player)
        self.assertEqual((share.pending_deduction, share.wallet_deduction, share.deposit_deduction), (8000, 0, 2000))
        self.assertEqual((gap.source_store_transaction.amount, gap.source_store_transaction.order_id), (-2000, order_id))
        self.assertGreater(gap.created_at, reservation.confirmed_at)
        self.assertEqual(gap.created_at < reservation.order.created_at, refund_first)
        self.assertFalse(RefundDepositRecoveryPermit.objects.filter(account=player).exists())
        self.assertEqual(reservation.order_id, result_payment['data']['order']['id'])
        refund_row = (order_id, 'REFUND', 10000)
        payment_row = (reservation.order_id, 'PAY', -10000)
        expected_rows = [refund_row, payment_row] if refund_first else [payment_row, refund_row]
        self.assertEqual(list(Transaction.objects.filter(
            order_id__in=(order_id, reservation.order_id), wallet=wallet,
        ).order_by('pk').values_list('order_id', 'tx_type', 'amount')),
            [(order_id, 'PAY', -10000), *expected_rows])
        wallet.refresh_from_db()
        funding.refresh_from_db()
        self.assertEqual((wallet.balance, funding.remaining, funding.gift_remaining), (11000, 11000, 1000))

        self.login(self.admin)
        self.ok(self.client.post(refund_path, refund_payload, format='json'))
        self.login(self.boss)
        replay = self.client.post(payment_path, payment_payload, format='json').data
        self.assertEqual((replay['code'], replay['errorCode'], replay['fundResult']),
                         (409, 'FUND_ALREADY_PROCESSED', result_payment))
        self.assertEqual(sale.refund_records.count(), 1)
        self.assertEqual(Transaction.objects.filter(wallet=wallet, order_id=reservation.order_id, tx_type='PAY').count(), 1)
