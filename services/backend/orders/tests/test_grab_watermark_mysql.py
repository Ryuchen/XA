"""Cutoff seal tests against real APIs and a disposable two-store MySQL."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, get_ident
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch

from django.db import DatabaseError, close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from console.grab_config import save_grab_window
from orders.models import Order, OrderGrabCutoffWatermark, OrderGrabReceiptProbe, OrderGrabSingleRoundState
from orders.tests import test_game_commission_payment as game_fixture
from orders.tests import test_grab_single_flow as single_fixture
from stores.context import store_context
from stores.tests_hosts import xinan_store
from wallet.test_deposit_lifecycle_mysql import require_isolated


@skipUnless(connection.vendor == 'mysql' and 'store_b' in connections, 'Requires two isolated MySQL stores')
@override_settings(PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True, LICENSE_ACCOUNT_ADMISSION_ENABLED=False,
    GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True, GRAB_SINGLE_WINDOW_ENABLED=True,
    GRAB_CUTOFF_WATERMARK_TESTING=True, GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True,
    PRESTART_SCOPE_SNAPSHOT_ENABLED=True, REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED=True,
    GAME_COMMISSION_PAYMENT_ENABLED=False, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'],
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class GrabWatermarkMySQLTest(TransactionTestCase):
    databases = {'default', 'store_b'}
    reset_sequences = True
    link = game_fixture.GameCommissionPaymentTest.link
    auth = game_fixture.GameCommissionPaymentTest.auth
    create = game_fixture.GameCommissionPaymentTest.create
    prepare = single_fixture.GrabSingleFlowTest.prepare

    def setUp(self):
        require_isolated(self)
        self.store = xinan_store()
        self.client = APIClient(HTTP_HOST='xa.esports.club')
        with store_context(self.store):
            game_fixture.GameCommissionPaymentTest.setUp(self)
            with patch('orders.tests.test_grab_single_flow.save_grab_window',
                       side_effect=lambda seconds, version, audit: save_grab_window(10, version, audit)):
                self.prepare()
            self.auth(self.boss_account)
            result = self.create(dispatch_mode='AUTO_GRAB')
            self.assertEqual(result.data['code'], 0, result.data)
            self.order = Order.objects.get(pk=result.data['data']['id'])
            self.state = OrderGrabSingleRoundState.objects.select_related('round').get(round__pool__order=self.order)
            self.tokens = {a.pk: issue_account_tokens(a)[0] for a in [self.boss_account, *self.accounts]}

    def api(self, account):
        return APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + self.tokens[account.pk])

    def after_deadline(self):
        delay = (self.state.round.deadline - timezone.now()).total_seconds()
        if delay > 0:
            sleep(delay + .2)

    def test_pending_evidence_is_sealed_but_never_assigned(self):
        from orders import grab_receipt_probe
        from orders.grab_single_flow import resolve
        original, calls = grab_receipt_probe._db_clock, [0]
        def lost_ack():
            calls[0] += 1
            if calls[0] == 2:
                raise DatabaseError('test: original commit acknowledgement unavailable')
            return original()
        with patch('orders.grab_receipt_probe._db_clock', side_effect=lost_ack):
            result = self.api(self.accounts[0]).post(f'/api/orders/orders/{self.order.pk}/grab/',
                {'team_size':1, 'client_request_id':'watermark-pending'}, format='json')
        self.assertEqual(result.status_code, 409, result.data)
        self.after_deadline()
        with store_context(self.store):
            self.assertEqual(resolve(self.order.pk), 'WAIT_REVIEW')
            mark = OrderGrabCutoffWatermark.objects.get(round_id=self.state.round_id)
            self.assertEqual(mark.receipt_count, 1)
            self.assertEqual(OrderGrabReceiptProbe.objects.get().state, 'PENDING')
            self.order.refresh_from_db()
            self.assertEqual(self.order.status, Order.Status.PENDING)
            replay = grab_receipt_probe.record_intent(self.state.round_id, self.accounts[0].pk, 'SINGLE',
                'watermark-pending', {}, single_flow=True, actor_snapshot=self.accounts[0])
            self.assertTrue(replay.replayed)
            self.assertEqual(replay.state, 'PENDING')
            with self.assertRaisesMessage(ValueError, '已截止'):
                grab_receipt_probe.record_intent(self.state.round_id, self.accounts[1].pk, 'SINGLE',
                    'watermark-late', {}, single_flow=True, actor_snapshot=self.accounts[1])
            original_digest = mark.receipt_digest
            OrderGrabCutoffWatermark.objects.filter(pk=mark.pk).update(receipt_digest='0' * 64)
            self.assertEqual(resolve(self.order.pk), 'WAIT_WATERMARK_REVIEW')
            OrderGrabCutoffWatermark.objects.filter(pk=mark.pk).update(receipt_digest=original_digest)
            self.assertEqual(resolve(self.order.pk), 'WAIT_REVIEW')

    def test_empty_first_round_parks_then_allows_later_candidate(self):
        from orders.grab_single_flow import resolve
        self.after_deadline()
        with store_context(self.store):
            self.assertEqual(resolve(self.order.pk), 'PARKED')
            self.assertEqual(OrderGrabCutoffWatermark.objects.get().receipt_count, 0)
        result = self.api(self.accounts[0]).post(f'/api/orders/orders/{self.order.pk}/grab/',
            {'team_size':1, 'client_request_id':'watermark-parked'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        with store_context(self.store):
            self.order.refresh_from_db()
            self.assertEqual(self.order.status, Order.Status.GRABBED)
            self.assertEqual(self.order.provider_account_id, self.accounts[0].pk)

    def test_registered_boss_cancel_is_processed_before_allocation(self):
        from orders.grab_receipt_probe import record_intent
        from orders.grab_single_flow import resolve
        with store_context(self.store):
            result = record_intent(self.state.round_id, self.boss_account.pk, 'BOSS_CANCEL',
                'watermark-cancel', {'reason':'客户取消'}, single_flow=True)
            self.assertEqual(result.state, 'BEFORE')
        self.after_deadline()
        with store_context(self.store):
            self.assertEqual(resolve(self.order.pk), 'CANCELLED')
            self.assertFalse(OrderGrabCutoffWatermark.objects.exists())
            self.order.refresh_from_db(); self.wallet.refresh_from_db()
            self.assertEqual((self.order.status, self.wallet.balance), (Order.Status.CANCELLED, 100000))

    def test_seal_waits_for_order_lock_and_includes_committed_receipt(self):
        from orders import grab_receipt_probe
        from orders.grab_cutoff_watermark import seal
        from orders.grab_single_flow import resolve
        entered, release, sealer_started = Event(), Event(), Event()
        clock, request_thread, calls = grab_receipt_probe._db_clock, [None], [0]

        def hold_first_sample():
            result = clock()
            if get_ident() == request_thread[0]:
                calls[0] += 1
                if calls[0] == 1:
                    entered.set()
                    if not release.wait(30):
                        raise AssertionError('registration was not released')
            return result

        def submit():
            close_old_connections()
            request_thread[0] = get_ident()
            try:
                return self.api(self.accounts[0]).post(f'/api/orders/orders/{self.order.pk}/grab/',
                    {'team_size':1, 'client_request_id':'watermark-concurrent'}, format='json')
            finally:
                close_old_connections()

        def seal_round():
            close_old_connections()
            try:
                with store_context(self.store):
                    sealer_started.set()
                    return seal(self.order.pk, team=False)
            finally:
                close_old_connections()

        with patch('orders.grab_receipt_probe._db_clock', side_effect=hold_first_sample):
            with ThreadPoolExecutor(max_workers=2) as pool:
                request = pool.submit(submit)
                try:
                    self.assertTrue(entered.wait(15), 'registration did not lock the order')
                    sealing = pool.submit(seal_round)
                    self.assertTrue(sealer_started.wait(5))
                    deadline, waiting = monotonic() + 8, False
                    while monotonic() < deadline:
                        db = connections['store_b']
                        with db.cursor() as cursor:
                            cursor.execute('SHOW FULL PROCESSLIST')
                            waiting = any(row[3] == db.settings_dict['NAME'] and row[4] == 'Query' and row[7]
                                and 'ORDERS_ORDER' in str(row[7]).upper()
                                and 'FOR UPDATE' in str(row[7]).upper() for row in cursor.fetchall())
                        if waiting:
                            break
                        sleep(.05)
                    self.assertTrue(waiting, 'watermark did not wait for the order lock')
                    self.after_deadline()
                finally:
                    release.set()
                result, seal_result = request.result(timeout=30), sealing.result(timeout=30)
        self.assertEqual(result.status_code, 409, result.data)
        self.assertEqual(seal_result, 'SEALED')
        with store_context(self.store):
            self.assertEqual(OrderGrabCutoffWatermark.objects.get().receipt_count, 1)
            self.assertIn(OrderGrabReceiptProbe.objects.get().state, ('PENDING', 'REVIEW'))
            self.assertEqual(resolve(self.order.pk), 'WAIT_REVIEW')
            self.order.refresh_from_db()
            self.assertEqual(self.order.status, Order.Status.PENDING)

    def test_committed_order_is_serialized_even_when_ack_intervals_overlap(self):
        from orders import grab_receipt_probe
        first_committed, release = Event(), Event()
        clock, first_thread, count = grab_receipt_probe._db_clock, [None], [0]

        def pause_first_ack():
            if get_ident() == first_thread[0]:
                count[0] += 1
                if count[0] == 2:
                    first_committed.set()
                    if not release.wait(10):
                        raise AssertionError('first acknowledgement was not released')
            return clock()

        def submit(account, key, first=False):
            close_old_connections()
            if first:
                first_thread[0] = get_ident()
            try:
                return self.api(account).post(f'/api/orders/orders/{self.order.pk}/grab/',
                    {'team_size': 1, 'client_request_id': key}, format='json')
            finally:
                close_old_connections()

        with patch('orders.grab_receipt_probe._db_clock', side_effect=pause_first_ack):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(submit, self.accounts[0], 'serial-first', True)
                try:
                    self.assertTrue(first_committed.wait(8), 'first registration did not commit')
                    second = pool.submit(submit, self.accounts[1], 'serial-second')
                    self.assertEqual(second.result(timeout=8).status_code, 200)
                finally:
                    release.set()
                self.assertEqual(first.result(timeout=8).status_code, 200)
        with store_context(self.store):
            rows = {row.request_key: row for row in OrderGrabReceiptProbe.objects.filter(round=self.state.round)}
            one, two = rows['serial-first'], rows['serial-second']
            self.assertLess(one.pk, two.pk)
            self.assertLessEqual(one.lower_at, two.lower_at)
            self.assertLessEqual(two.lower_at, one.upper_observed_at)
            self.assertEqual(grab_receipt_probe.compare_candidate_bounds(one.pk, two.pk), 'REVIEW')
