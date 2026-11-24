"""Same-wallet reservation payment and an actual negotiated deposit-backed refund."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Empty, Queue
from threading import Event
from time import monotonic, sleep
from unittest import skipUnless

from django.conf import settings
from django.core import signing
from django.db import OperationalError, connection, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from common.mysql_test_support import mysql_locking_statements, require_disposable_mysql
from console.reservation_deadlines import save_deadlines
from engagement.confirmation import refund_within_aftersale_window
from engagement.models import PointFundingLot
from engagement.services import wallet_funding_parts
from orders.models import Order, OrderSegmentSettlementManifest, RefundRecoveryLine, Reservation
from orders.refunds import PLAN_SALT
from orders.reservation_deadlines import current_rules
from orders.tests import test_ordinary_full_refund_deposit as deposit_flow
from orders.tests import test_release_flow as flow
from stores.context import resolve_store, store_context
from stores.models import Store
from wallet.models import RefundDepositDeficit, RefundDepositRecoveryPermit, Transaction, get_platform_wallet
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
    new_order = flow.ReleaseFlowTest.new_order
    finish = flow.ReleaseFlowTest.finish
    settle_after_window = flow.ReleaseFlowTest.settle_after_window

    def test_reservation_and_negotiated_refund_serialize_on_same_buyer_wallet(self):
        self.run_in_stores(self.exercise)

    @override_settings(BOSS_FUNDING_SELECTION_ENABLED=False, ORDINARY_SERVICE_FUNDING_ENABLED=False,
                       ORIGINAL_ATTRIBUTION_ENABLED=False, SEGMENT_ECONOMICS_CAPTURE_ENABLED=False)
    def test_reservation_and_settled_refund_serialize_and_recheck_the_quote(self):
        self.run_in_stores(self.exercise_settled_refund)

    def run_in_stores(self, exercise):
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
                    exercise(store, host, refund_first=slug == 'shenglang')

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

    def compete(self, store, host, order_id, reservation_id, refund, payment, *, refund_first, refund_wallet_locked):
        buyer = self.accounts[self.boss.pk]
        staff = self.accounts[self.admin.pk]
        player = self.accounts[self.provider.pk]
        wallet = get_wallet(user=self.boss, account=buyer)
        balance_before = wallet.balance
        observer = connections[store.database_alias]
        first_has_lock = Event()
        resume_first = Event()
        attempted = Queue()

        def pause_first(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if refund_first:
                lock_held = '`wallet_wallet`' in sql.lower() and refund_wallet_locked(params or ())
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

        refund_request = (issue_account_tokens(staff)[0], *refund)
        payment_request = (issue_account_tokens(buyer)[0], *payment)
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
                self.assertEqual(wallet.balance, balance_before)
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
        return result_refund, result_payment

    def exercise(self, store, host, *, refund_first):
        order_id, refund_path, refund_payload = self.prepare_refund()
        reservation_id, payment_path, payment_payload = self.prepare_reservation()
        buyer = self.accounts[self.boss.pk]
        player = self.accounts[self.provider.pk]
        wallet = get_wallet(user=self.boss, account=buyer)
        funding = PointFundingLot.objects.get(account=buyer, kind=PointFundingLot.Kind.RECHARGE)
        self.assertEqual((wallet.balance, funding.remaining, funding.gift_remaining), (11000, 11000, 1000))
        result_refund, result_payment = self.compete(
            store, host, order_id, reservation_id, (refund_path, refund_payload), (payment_path, payment_payload),
            refund_first=refund_first, refund_wallet_locked=lambda params: order_id in params and 'PAY' in params,
        )
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

    def prepare_settled_order(self):
        profile = self.provider.escort_profile
        profile.deposit_mode, profile.deposit_paid = 'EARNINGS', 0
        profile.save(update_fields=['deposit_mode', 'deposit_paid'])
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'deposit_rule:edit']
        role.save(update_fields=['permissions'])
        self.login(self.admin)
        self.ok(self.client.put('/api/admin/config/deposit-rules', {'deposit_percent': 100}, format='json'))
        order = self.new_order()
        self.assertEqual(order.service_deposit_percent, 100)
        report = self.finish(order)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {
            'kind': 'RESULT', 'image': flow.screenshot(),
        }, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        # Generate actual legacy-shaped settlement rows at the due time. The
        # concurrent refund runs on the ordinary clock, within the refund window.
        self.settle_after_window()
        order.refresh_from_db()
        profile.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        self.assertFalse(refund_within_aftersale_window(order, now=order.settled_at))
        self.assertFalse(OrderSegmentSettlementManifest.objects.filter(order=order).exists())
        self.assertEqual(profile.deposit_paid, 8000)
        provider_wallet = get_wallet(user=self.provider, account=profile.account)
        self.assertEqual(provider_wallet.balance, 0)
        deposit = Transaction.objects.get(order=order, wallet=provider_wallet, tx_type='DEPOSIT', amount=-8000)
        self.assertEqual(deposit.deposit_owner_id, profile.account_id)
        credit = Transaction.objects.get(order=order, tx_type='DEPOSIT_INCOME', deposit_pair=deposit)
        self.assertEqual((deposit.deposit_business_cycle, credit.amount), (profile.account.business_cycle, 8000))
        return order

    def exercise_settled_refund(self, store, host, *, refund_first):
        order = self.prepare_settled_order()
        reservation_id, payment_path, payment_payload = self.prepare_reservation()
        buyer = self.accounts[self.boss.pk]
        player = self.accounts[self.provider.pk]
        wallet = get_wallet(user=self.boss, account=buyer)
        self.assertEqual(wallet.balance, 11000)
        original_entries = list(order.transactions.order_by('pk').values_list('pk', 'amount'))
        preview_path = f'/api/admin/orders/{order.pk}/refund-preview/'
        refund_path = f'/api/admin/orders/{order.pk}/refund/'
        self.login(self.admin)
        preview = self.ok(self.client.get(preview_path))
        self.assertTrue(preview['eligible'])
        refund_payload = {
            'reason': '隔离已结算原单退款', 'refund_amount': 10000,
            'client_request_id': 'settled-reservation-race-refund', 'plan_token': preview['plan_token'],
        }
        start = monotonic()
        result_refund, result_payment = self.compete(
            store, host, order.pk, reservation_id, (refund_path, refund_payload), (payment_path, payment_payload),
            refund_first=refund_first, refund_wallet_locked=lambda params: buyer.pk in params,
        )
        self.assertEqual(result_payment.get('code'), 0, result_payment)
        self.assertLess(monotonic() - start, 60)
        reservation = Reservation.objects.get(pk=reservation_id)
        self.assertEqual((reservation.status, reservation.order_id),
                         (Reservation.Status.CONVERTED, result_payment['data']['order']['id']))
        if not refund_first:
            self.assertEqual((result_refund.get('code'), result_refund.get('errorCode')),
                             (409, 'REFUND_PLAN_CHANGED'), result_refund)
            order.refresh_from_db()
            wallet.refresh_from_db()
            self.assertEqual((order.status, order.payment_status, wallet.balance), ('COMPLETED', 'PAID', 1000))
            self.assertFalse(order.refund_records.exists())
            self.assertFalse(RefundRecoveryLine.objects.filter(refund__order=order).exists())
            self.assertFalse(RefundDepositDeficit.objects.filter(refund__order=order).exists())
            self.assertEqual(list(order.transactions.order_by('pk').values_list('pk', 'amount')), original_entries)
            refreshed = self.ok(self.client.get(preview_path))
            self.assertTrue(refreshed['eligible'])
            previous_plan = signing.loads(preview['plan_token'], salt=PLAN_SALT, max_age=600)
            current_plan = signing.loads(refreshed['plan_token'], salt=PLAN_SALT, max_age=600)
            self.assertNotEqual(previous_plan.pop('fingerprint'), current_plan.pop('fingerprint'))
            self.assertEqual(previous_plan, current_plan)
            refund_payload['plan_token'] = refreshed['plan_token']
            result_refund = self.client.post(refund_path, refund_payload, format='json').data
        self.assertEqual(result_refund.get('code'), 0, result_refund)

        refund = order.refund_records.get(client_request_id=refund_payload['client_request_id'])
        gap = RefundDepositDeficit.objects.get(refund=refund, account=player)
        self.assertEqual((gap.source_kind, gap.remaining, gap.account_business_cycle),
                         ('PAIRED', 8000, player.business_cycle))
        self.assertGreater(gap.created_at, reservation.confirmed_at)
        self.assertEqual(gap.created_at < reservation.order.created_at, refund_first)
        self.assertEqual((gap.source_provider_transaction.amount, gap.source_store_transaction.amount), (8000, -8000))
        self.assertEqual(gap.source_provider_transaction_id, gap.source_store_transaction.deposit_pair_id)
        self.assertFalse(RefundDepositRecoveryPermit.objects.filter(account=player).exists())
        self.ok(self.client.post(refund_path, refund_payload, format='json'))
        self.login(self.boss)
        replay = self.client.post(payment_path, payment_payload, format='json').data
        self.assertEqual((replay['code'], replay['errorCode'], replay['fundResult']),
                         (409, 'FUND_ALREADY_PROCESSED', result_payment))
        order.refresh_from_db()
        wallet.refresh_from_db()
        self.assertEqual((order.status, order.payment_status, wallet.balance), ('CANCELLED', 'REFUNDED', 11000))
        self.assertEqual(wallet_funding_parts(wallet),
                         {'prepaid_balance': 10000, 'gift_balance': 1000, 'compensation_balance': 0})
        self.assertEqual(PointFundingLot.objects.get(account=buyer, kind=PointFundingLot.Kind.RECHARGE).remaining, 11000)
        self.assertEqual(get_platform_wallet().balance, 0)
        self.assertEqual(order.refund_records.count(), 1)
        self.assertEqual(list(Transaction.objects.filter(wallet=wallet, tx_type='REFUND').values_list('order_id', 'amount')),
                         [(order.pk, 10000)])
        self.assertEqual(list(Transaction.objects.filter(wallet=wallet, order=reservation.order, tx_type='PAY')
                              .values_list('amount', flat=True)), [-10000])
        print(f'Legacy {store.slug} refund={refund.pk} paid_order={reservation.order_id} gap={gap.pk} '
              f'paired={gap.remaining} wallet={wallet.balance}', flush=True)
