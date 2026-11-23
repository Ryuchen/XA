"""Observe the real profile lock shared by a shift edit, transfer, and booking."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Empty, Queue
from threading import Event
from time import monotonic, sleep
from unittest import skipUnless

from django.conf import settings
from django.db import connection, connections, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.models import LicenseQuotaLock
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from common.mysql_test_support import mysql_locking_statements, require_disposable_mysql
from console.points_redemption_config import save_points_terms
from engagement.models import MemberProfile, PointLedger, RetirementAudit
from orders.models import Order, Reservation
from orders.reservation_services import find_conflicting_reservations
from orders.tests import test_assigned_order_reservation_guard as booking_flow
from orders.tests import test_release_flow as release_flow
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from site_messages.models import Message, ServiceTransferAssignmentNotice
from stores.context import resolve_store, store_context
from stores.models import Store
from users.models import ProviderPassPurchase
from wallet.models import IdempotencyKey, Transaction, Wallet, get_platform_wallet
from wallet.services import get_wallet

SCHEDULE = '/api/users/escorts/schedule/'
PASS = '/api/users/provider-pass/'
RESERVATIONS = '/api/orders/reservations/'
PROFILE_TABLE = 'users_escortprofile'
ACCOUNT_TABLE = 'club_accounts_clubaccount'
LIFECYCLE_TABLE = 'club_accounts_providerlifecyclelock'
TRANSFER_NOTICE_TITLES = ('已为你更换大神', '订单已转出', '客服转单给你')


@skipUnless(connection.vendor == 'mysql', 'Requires real MySQL profile row locks')
@override_settings(**booking_flow.AssignedOrderReservationGuardTest._overridden_settings,
                   ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   SEGMENT_STANDARD_TRANSFER_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
                   PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=False)
class ReservationTransferScheduleMySQLTest(TransactionTestCase):
    routes = (
        (('shenglang', 'sl.esports.club', 'store_a'), ('xinan', 'xa.esports.club', 'store_b'))
        if settings.ISOLATED_CONTROL_PLANE else (('shenglang', 'sl.esports.club', 'default'),)
    )
    databases = {'default', *(alias for _, _, alias in routes)}

    login = booking_flow.AssignedOrderReservationGuardTest.login
    ok = booking_flow.AssignedOrderReservationGuardTest.ok
    recharge = booking_flow.AssignedOrderReservationGuardTest.recharge
    book = booking_flow.AssignedOrderReservationGuardTest.book

    def prepare(self):
        booking_flow.AssignedOrderReservationGuardTest.setUp(self)
        replacement = make_provider(deposit_mode='EARNINGS')
        self.replacement = get_or_create_account_for_legacy_user(replacement)
        replacement.escort_profile.account = self.replacement
        replacement.escort_profile.save(update_fields=['account'])
        self.original = self.accounts[self.provider.pk]
        self.buyer = self.accounts[self.boss.pk]
        self.staff = self.accounts[self.admin.pk]
        self.ok(self.recharge())
        now = timezone.localtime()
        self.start = (now + timedelta(days=2)).replace(hour=23, minute=30, second=0, microsecond=0)
        self.end = self.start + timedelta(hours=1)
        self.reservation_id, self.order_id = self.book(self.provider, self.start, 'schedule-transfer-payment')
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{self.order_id}/start/',
                                 {'entry_image': release_flow.screenshot()}, format='multipart'))
        self.transfer_path = f'/api/admin/orders/{self.order_id}/transfer/'
        self.transfer_payload = {'reason': '协调履约中的原预约', 'transfers': [{
            'old_provider_id': self.original.pk, 'new_provider_id': self.replacement.pk,
            'original_share': {'retained': 4000, 'transferred': 6000, 'agreement': '双方同意原价份额'},
        }]}
        self.today_slot = {'weekday': now.weekday(), 'start_minute': 0, 'end_minute': 1440}
        self.original_slot = {'weekday': self.start.weekday(), 'start_minute': 1410, 'end_minute': 30}
        self.funding_before = list(Order.objects.get(pk=self.order_id).transactions.values_list('pk', 'tx_type', 'amount'))
        self.balance_before = Wallet.objects.get(account=self.buyer).balance

    def api(self, account):
        token = issue_account_tokens(account)[0]
        return APIClient(HTTP_HOST=self.store_host, HTTP_AUTHORIZATION='Bearer ' + token)

    def run_in_stores(self, exercise):
        for alias in self.databases:
            require_disposable_mysql(connections[alias])
        for slug, host, alias in self.routes:
            with self.subTest(store=slug):
                Store.objects.update_or_create(slug=slug, defaults={
                    'name': slug, 'database_alias': alias, 'status': Store.Status.ACTIVE,
                    'initialized_at': timezone.now(),
                })
                self.store = resolve_store(slug)
                self.store_host = host
                with store_context(self.store):
                    self.client = APIClient(HTTP_HOST=host)
                    self.prepare()
                    exercise()

    @staticmethod
    def observe_lock_attempt(attempts, table, mode, *, account_id=None):
        def observe_lock(execute, sql, params, many, context):
            normalized = sql.lower()
            if (f'`{table}`' in normalized and mode in normalized
                    and (account_id is None or account_id in (params or ()))):
                attempts.put(context['connection'].connection.thread_id())
            return execute(sql, params, many, context)
        return observe_lock

    def observe_profile_lock(self, attempts):
        return self.observe_lock_attempt(attempts, PROFILE_TABLE, 'for update')

    def independent_request(self, request, wrapper):
        try:
            with store_context(self.store), connections[self.store.database_alias].execute_wrapper(wrapper):
                return request()
        finally:
            connections.close_all()

    def wait_for_lock(self, competing, attempts, *, table=PROFILE_TABLE, include_shared=False):
        observer = connections[self.store.database_alias].copy()
        try:
            entered = set()
            deadline = monotonic() + 4
            while not competing.done() and monotonic() < deadline:
                try:
                    entered.add(attempts.get_nowait())
                except Empty:
                    pass
                for session in entered:
                    if mysql_locking_statements(observer, table, session_id=session, include_shared=include_shared):
                        self.assertFalse(competing.done())
                        return session
                sleep(.03)
            detail = competing.result().data if competing.done() else '请求仍未完成'
            self.fail(f'未观察到实际锁等待: {entered!r}, {detail}')
        finally:
            observer.close()

    def report_wait(self, session, result, *, table=PROFILE_TABLE):
        print(f'{self.store.slug} reservation={self.reservation_id} order={self.order_id} '
              f'waiting_session={session} lock={table} competing_code={result.data["code"]}', flush=True)

    def while_profile_locked(self, first_request, competing_request):
        attempts = Queue()
        with ThreadPoolExecutor(max_workers=1) as worker:
            with transaction.atomic(using=self.store.database_alias):
                self.ok(first_request())
                competing = worker.submit(self.independent_request, competing_request,
                                          self.observe_profile_lock(attempts))
                waiting = self.wait_for_lock(competing, attempts)
            result = competing.result(timeout=10)
        self.report_wait(waiting, result)
        return result

    def assert_occupancy(self, holder, other, transfer_count):
        booking = Reservation.objects.get(pk=self.reservation_id)
        order = Order.objects.get(pk=self.order_id)
        self.assertEqual((booking.status, booking.order_id, booking.provider_account_id),
                         ('CONVERTED', order.pk, self.original.pk))
        self.assertEqual((order.status, order.provider_account_id), ('IN_SERVICE', holder.pk))
        self.assertEqual(list(order.providers.filter(released_at__isnull=True).values_list('provider_account_id', flat=True)),
                         [holder.pk])
        self.assertTrue(find_conflicting_reservations(holder, self.start, self.end).filter(pk=booking.pk).exists())
        self.assertFalse(find_conflicting_reservations(other, self.start, self.end).exists())
        self.assertEqual(order.status_logs.filter(action='TRANSFER').count(), transfer_count)
        self.assertEqual(list(order.transactions.values_list('pk', 'tx_type', 'amount')), self.funding_before)
        self.assertEqual(Wallet.objects.get(account=self.buyer).balance, self.balance_before)

    def test_transfer_waits_for_shorter_schedule_and_keeps_original_assignment(self):
        self.run_in_stores(self.exercise_schedule_edit)

    def exercise_schedule_edit(self):
        escort, staff = self.api(self.replacement), self.api(self.staff)
        full = [self.today_slot, self.original_slot]
        shorter = [self.today_slot, {**self.original_slot, 'end_minute': 15}]
        self.ok(escort.put(SCHEDULE, {'slots': full}, format='json'))
        result = self.while_profile_locked(lambda: escort.put(SCHEDULE, {'slots': shorter}, format='json'),
                                           lambda: staff.post(self.transfer_path, self.transfer_payload, format='json'))
        self.assertEqual(result.data['code'], 400, result.data)
        self.assertIn('未覆盖原预约时段', result.data['msg'])
        self.assertEqual(self.ok(escort.get(SCHEDULE)), sorted(shorter, key=lambda slot: slot['weekday']))
        self.assert_occupancy(self.original, self.replacement, 0)
        self.ok(escort.put(SCHEDULE, {'slots': full}, format='json'))
        self.ok(staff.post(self.transfer_path, self.transfer_payload, format='json'))
        self.assert_occupancy(self.replacement, self.original, 1)

    def test_overlapping_reservation_waits_for_transfer_and_sees_shift_occupied(self):
        self.run_in_stores(self.exercise_new_reservation)

    def exercise_new_reservation(self):
        other_buyer = get_or_create_account_for_legacy_user(make_user())
        other_service = make_service()
        staff, buyer = self.api(self.staff), self.api(other_buyer)
        payload = {'provider_account_id': self.replacement.pk, 'service_id': other_service.pk,
                   'start_time': self.start.isoformat(), 'end_time': self.end.isoformat()}
        result = self.while_profile_locked(lambda: staff.post(self.transfer_path, self.transfer_payload, format='json'),
                                           lambda: buyer.post(RESERVATIONS, payload, format='json'))
        self.assertEqual(result.data['code'], 409, result.data)
        self.assertFalse(Reservation.objects.filter(customer_account=other_buyer).exists())
        self.assert_occupancy(self.replacement, self.original, 1)

    def test_edit_and_transfer_use_compatible_account_and_profile_lock_order(self):
        self.run_in_stores(self.exercise_transfer_lock_order)

    def exercise_transfer_lock_order(self):
        escort, staff = self.api(self.replacement), self.api(self.staff)
        held = Event()
        resume = Event()
        attempts = Queue()
        observe_profile_lock = self.observe_profile_lock(attempts)

        def pause_after_account_lock(execute, sql, params, many, context):
            result = observe_profile_lock(execute, sql, params, many, context)
            normalized = sql.lower()
            if (not held.is_set() and f'`{ACCOUNT_TABLE}`' in normalized and 'lock in share mode' in normalized
                    and self.replacement.pk in (params or ())):
                held.set()
                if not resume.wait(timeout=6):
                    raise AssertionError('未等到档期编辑加锁查询')
            return result

        with ThreadPoolExecutor(max_workers=1) as pool:
            transfer = pool.submit(self.independent_request,
                lambda: staff.post(self.transfer_path, self.transfer_payload, format='json'), pause_after_account_lock)
            try:
                self.assertTrue(held.wait(timeout=3), '转单未取得接替陪玩共享账号锁')
                with transaction.atomic(using=self.store.database_alias):
                    self.ok(escort.put(SCHEDULE, {'slots': [self.today_slot, self.original_slot]}, format='json'))
                    resume.set()
                    waiting = self.wait_for_lock(transfer, attempts)
            finally:
                resume.set()
            result = transfer.result(timeout=10)
        self.ok(result)
        self.report_wait(waiting, result)
        self.assert_occupancy(self.replacement, self.original, 1)

    @override_settings(PROVIDER_PASS_POINTS_PAYMENT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True)
    def test_points_pass_and_transfer_commit_with_only_one_debit(self):
        self.run_in_stores(self.exercise_points_pass)

    @override_settings(PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True, PROVIDER_PASS_POINTS_PAYMENT_ENABLED=True,
                       POINTS_USAGE_SWITCHES_ENABLED=True, SERVICE_TRANSFER_NOTICE_RECOVERY_ENABLED=True)
    def test_points_pass_and_transfer_commit_with_lifecycle_barrier_enabled(self):
        self.run_in_stores(self.exercise_points_pass)

    def prepare_points_pass(self, *, balance):
        wallet = get_wallet(account=self.replacement)
        wallet.balance = balance
        wallet.save(update_fields=['balance'])
        points = MemberProfile.objects.create(account=self.replacement, points_balance=30000)
        save_points_terms(group_point_hundredths=10000, group_coin_units=10,
                          max_ratio_basis_points=5000, version=0, audit=lambda *_: None)
        escort = self.api(self.replacement)
        quote = self.ok(escort.post(PASS + 'points-quote/',
            {'tier': 'BRONZE', 'selected_point_hundredths': 20000}, format='json'))
        self.assertEqual((quote['daily_price'], quote['discount_units'], quote['payable_units']), (200, 20, 180))
        payload = {'tier': 'BRONZE', 'days': 1, 'expected_daily_price': 200,
                   'points_quote_token': quote['quote_token'], 'client_request_id': 'transfer-points-pass'}
        return wallet, points, escort, payload

    def exercise_points_pass(self):
        wallet, points, escort, payload = self.prepare_points_pass(balance=1000)
        staff = self.api(self.staff)
        store_wallet = get_platform_wallet()
        store_balance = store_wallet.balance
        transfer_guard_table = (LIFECYCLE_TABLE if settings.PROVIDER_LIFECYCLE_INTEGRATION_ENABLED else ACCOUNT_TABLE)

        account_locked, profile_locked, purchase_guard_held = Event(), Event(), Event()
        resume_transfer, resume_purchase = Event(), Event()
        attempts = Queue()
        observe_profile_lock = self.observe_profile_lock(attempts)

        def pause_transfer(execute, sql, params, many, context):
            result = observe_profile_lock(execute, sql, params, many, context)
            query = sql.lower()
            if (not account_locked.is_set() and f'`{transfer_guard_table}`' in query
                    and 'lock in share mode' in query and self.replacement.pk in (params or ())):
                account_locked.set()
                self.assertTrue(resume_transfer.wait(6), '购卡未获得档案锁')
            return result

        def pause_purchase(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            query = sql.lower()
            if (f'`{LIFECYCLE_TABLE}`' in query and 'lock in share mode' in query
                    and self.replacement.pk in (params or ())):
                purchase_guard_held.set()
            if not profile_locked.is_set() and f'`{PROFILE_TABLE}`' in query and 'for update' in query:
                profile_locked.set()
                self.assertTrue(resume_purchase.wait(6), '转单未到达档案锁')
            return result

        with ThreadPoolExecutor(max_workers=2) as pool:
            transfer = pool.submit(self.independent_request,
                lambda: staff.post(self.transfer_path, self.transfer_payload, format='json'), pause_transfer)
            try:
                self.assertTrue(account_locked.wait(3), '转单未读取接替陪玩账号')
                purchase = pool.submit(self.independent_request,
                    lambda: escort.post(PASS, payload, format='json'), pause_purchase)
                self.assertTrue(profile_locked.wait(3), purchase.result().data if purchase.done() else '购卡未加锁档案')
                if settings.PROVIDER_LIFECYCLE_INTEGRATION_ENABLED:
                    self.assertTrue(purchase_guard_held.is_set(), '购卡未先持接替陪玩生命周期共享锁')
                resume_transfer.set()
                waiting = self.wait_for_lock(transfer, attempts)
            finally:
                resume_transfer.set()
                resume_purchase.set()
            purchase_response = purchase.result(timeout=10)
            transfer_response = transfer.result(timeout=10)
        self.ok(purchase_response)
        self.ok(transfer_response)
        self.report_wait(waiting, transfer_response)

        replay = escort.post(PASS, payload, format='json')
        self.assertEqual(replay.data['errorCode'], 'FUND_ALREADY_PROCESSED', replay.data)
        self.assertEqual(replay.data['fundResult']['msg'], purchase_response.data['msg'])
        bought = ProviderPassPurchase.objects.get(account=self.replacement)
        self.assertEqual((bought.business_cycle, bought.paid_amount, bought.point_discount_amount),
                         (self.replacement.business_cycle, 180, 20))
        redemption = bought.point_redemption
        self.assertEqual((redemption.points_hundredths, redemption.ledger.amount), (20000, -20000))
        self.assertEqual(PointLedger.objects.filter(account=self.replacement, kind='PASS_POINTS').count(), 1)
        self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='PASS_PURCHASE').get().pk,
                         bought.transaction_id)
        self.assertEqual(Transaction.objects.get(wallet=store_wallet, external_tx_id=bought.transaction.tx_no,
                                                 tx_type='PASS_INCOME').amount, 180)
        wallet.refresh_from_db()
        points.refresh_from_db()
        store_wallet.refresh_from_db()
        self.assertEqual((wallet.balance, points.points_balance, store_wallet.balance), (820, 10000, store_balance + 180))
        self.assert_occupancy(self.replacement, self.original, 1)
        if settings.SERVICE_TRANSFER_NOTICE_RECOVERY_ENABLED:
            notices = Message.objects.filter(related_order_id=self.order_id, title__in=TRANSFER_NOTICE_TITLES)
            self.assertCountEqual(list(notices.values_list('recipient_account_id', 'recipient_business_cycle',
                                                           'title', 'assignment_alert')), [
                (self.buyer.pk, self.buyer.business_cycle, TRANSFER_NOTICE_TITLES[0], False),
                (self.original.pk, self.original.business_cycle, TRANSFER_NOTICE_TITLES[1], False),
                (self.replacement.pk, self.replacement.business_cycle, TRANSFER_NOTICE_TITLES[2], True),
            ])

    @override_settings(PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True, PROVIDER_RETIREMENT_ENABLED=True,
                       PROVIDER_PASS_POINTS_PAYMENT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True,
                       SERVICE_TRANSFER_NOTICE_RECOVERY_ENABLED=True)
    def test_retirement_wins_against_waiting_points_pass_and_reservation_transfer(self):
        self.run_in_stores(self.exercise_retirement_first)

    def exercise_retirement_first(self):
        wallet, points, escort, purchase_payload = self.prepare_points_pass(balance=0)
        reviewer = make_console_user(['escort:retire'])
        reviewer_account = get_or_create_account_for_legacy_user(reviewer)
        reviewer.admin_membership.account = reviewer_account
        reviewer.admin_membership.save(update_fields=['account'])
        retirement_staff, transfer_staff = self.api(reviewer_account), self.api(self.staff)
        retirement_url = f'/api/admin/providers/{self.replacement.pk}/retirement/'
        preview = self.ok(retirement_staff.get(retirement_url))
        self.assertTrue(preview['can_retire'], preview['facts']['blocking_reasons'])
        retirement_payload = {'confirm': True, 'reason': '陪玩本人已完成结清',
                              'confirmation_token': preview['confirmation_token'],
                              'client_request_id': 'transfer-pass-retirement'}
        lifecycle_held, resume_retirement = Event(), Event()
        count_before = LicenseQuotaLock.objects.get(pk='PROVIDER').active_count

        def pause_retirement(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            normalized = sql.lower()
            if (not lifecycle_held.is_set() and f'`{LIFECYCLE_TABLE}`' in normalized
                    and 'for update' in normalized and self.replacement.pk in (params or ())):
                lifecycle_held.set()
                self.assertTrue(resume_retirement.wait(8), '未观测到等待注销的请求')
            return result

        with ThreadPoolExecutor(max_workers=3) as pool:
            retirement = pool.submit(self.independent_request,
                lambda: retirement_staff.post(retirement_url, retirement_payload, format='json'), pause_retirement)
            try:
                self.assertTrue(lifecycle_held.wait(3), '注销未取得陪玩排它锁')
                purchase_attempts, transfer_attempts = Queue(), Queue()
                purchase = pool.submit(self.independent_request, lambda: escort.post(PASS, purchase_payload, format='json'),
                    self.observe_lock_attempt(purchase_attempts, LIFECYCLE_TABLE, 'lock in share mode', account_id=self.replacement.pk))
                transfer = pool.submit(self.independent_request,
                    lambda: transfer_staff.post(self.transfer_path, self.transfer_payload, format='json'),
                    self.observe_lock_attempt(transfer_attempts, LIFECYCLE_TABLE, 'lock in share mode', account_id=self.replacement.pk))
                purchase_wait = self.wait_for_lock(purchase, purchase_attempts, table=LIFECYCLE_TABLE, include_shared=True)
                transfer_wait = self.wait_for_lock(transfer, transfer_attempts, table=LIFECYCLE_TABLE, include_shared=True)
                self.assertNotEqual(purchase_wait, transfer_wait)
            finally:
                resume_retirement.set()
            receipt = self.ok(retirement.result(timeout=10))
            purchase_result, transfer_result = purchase.result(timeout=10), transfer.result(timeout=10)

        cycle = self.replacement.business_cycle
        self.replacement.refresh_from_db()
        self.assertEqual((self.replacement.license_membership_active, self.replacement.business_cycle), (False, cycle + 1))
        self.assertEqual((receipt['retired_cycle'], receipt['business_cycle']), (cycle, cycle + 1))
        replay = self.ok(retirement_staff.post(retirement_url, retirement_payload, format='json'))
        self.assertTrue(replay['replayed'])
        self.assertEqual((replay['retired_cycle'], replay['business_cycle']), (cycle, cycle + 1))
        self.assertEqual(RetirementAudit.objects.filter(account=self.replacement, business_cycle=cycle).count(), 1)
        self.assertEqual(LicenseQuotaLock.objects.get(pk='PROVIDER').active_count, count_before - 1)
        self.assertEqual((purchase_result.status_code, purchase_result.data['code']), (409, 409), purchase_result.data)
        self.assertIn('账号状态已变化', purchase_result.data['msg'])
        self.assertEqual(transfer_result.data['code'], 400, transfer_result.data)
        self.assertIn('转单陪玩账号周期已变化', transfer_result.data['msg'])
        for session, result in ((purchase_wait, purchase_result), (transfer_wait, transfer_result)):
            self.report_wait(session, result, table=LIFECYCLE_TABLE)
        self.assertEqual(escort.post(PASS, purchase_payload, format='json').status_code, 401)
        wallet.refresh_from_db()
        points.refresh_from_db()
        self.assertEqual((wallet.balance, points.points_balance), (0, 30000))
        self.assertFalse(ProviderPassPurchase.objects.filter(account=self.replacement).exists())
        self.assertFalse(Transaction.objects.filter(wallet=wallet, tx_type='PASS_PURCHASE').exists())
        self.assertFalse(PointLedger.objects.filter(account=self.replacement, kind='PASS_POINTS').exists())
        self.assertFalse(IdempotencyKey.objects.filter(account=self.replacement, request_id=purchase_payload['client_request_id']).exists())
        self.assertFalse(ServiceTransferAssignmentNotice.objects.filter(status_log__order_id=self.order_id).exists())
        self.assertFalse(Message.objects.filter(related_order_id=self.order_id, title__in=TRANSFER_NOTICE_TITLES).exists())
        self.assert_occupancy(self.original, self.replacement, 0)
