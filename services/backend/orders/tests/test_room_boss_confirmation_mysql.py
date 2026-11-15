"""Disposable MySQL test: confirmation must observe a concurrent objection."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from time import monotonic, sleep
from unittest import skipUnless

from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from common.mysql_test_support import mysql_locking_statements, require_disposable_mysql
from orders.models import Order, OrderProvider, OrderRoomCompletionRequest, OrderRoomConfirmation, OrderRoomObjection
from orders.state_machine import transition
from orders.tests.factories import make_order, make_provider, make_service, make_user


@skipUnless(connection.vendor == 'mysql', 'Requires isolated MySQL row locks')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], ORIGINAL_ATTRIBUTION_ENABLED=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', ROOM_COMPLETION_REVIEW_ENABLED=True,
                   ROOM_BOSS_CONFIRMATION_ENABLED=True)
class RoomBossConfirmationMySQLTest(TransactionTestCase):
    def test_confirmation_waits_for_order_lock_and_observes_objection(self):
        require_disposable_mysql(connection)
        boss, provider = make_user(), make_provider(deposit_mode='EARNINGS')
        boss_account, provider_account = [get_or_create_account_for_legacy_user(u) for u in (boss, provider)]
        order = make_order(boss, service=make_service(room_enabled=True), customer_account=boss_account,
                           payment_status='PAID', settlement_mode='REPORT_APPROVAL')
        def assign(current):
            current.provider, current.provider_account = provider, provider_account
            OrderProvider.objects.create(order=current, provider=provider, provider_account=provider_account)
        transition(order.pk, Order.Status.GRABBED, side_effect=assign, update_fields=['provider', 'provider_account'])
        transition(order.pk, Order.Status.IN_SERVICE)
        application = OrderRoomCompletionRequest.objects.create(room=order.room, submitted_by=provider_account,
            request_id='request', description='已完成', participants=[provider_account.pk], status='VERIFIED',
            reviewed_by=provider_account, reviewed_at=timezone.now(), review_note='已核实', review_request_id='review')
        wait = OrderRoomConfirmation.objects.create(request=application, config_minutes=30, config_version=1,
            started_at=timezone.now(), deadline=timezone.now()+timedelta(minutes=30))
        token, (started, finished) = issue_account_tokens(boss_account)[0], (Event(), Event())
        def confirm():
            close_old_connections()
            try:
                client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer '+token)
                started.set()
                return client.post(f'/api/orders/orders/{order.pk}/room/completion-requests/{application.pk}/confirm/',
                                   {'client_request_id': 'confirm'}, format='json').status_code
            finally:
                finished.set()
                close_old_connections()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                Order.objects.select_for_update().get(pk=order.pk)
                future = pool.submit(confirm)
                self.assertTrue(started.wait(5))
                deadline, waiting = monotonic()+5, 0
                while monotonic() < deadline and not waiting and not finished.is_set():
                    waiting = mysql_locking_statements(connection, 'orders_order')
                    if not waiting:
                        sleep(0.03)
                self.assertGreater(waiting, 0, 'confirmation did not wait on the order row')
                wait.status, wait.paused_at, wait.deadline = 'PAUSED', timezone.now(), None
                wait.remaining_microseconds = 10*60*1000000
                wait.save(update_fields=['status', 'paused_at', 'deadline', 'remaining_microseconds'])
                OrderRoomObjection.objects.create(room=order.room, customer=boss_account, request_id='objection',
                    reason='待核对', confirmation=wait, remaining_microseconds=wait.remaining_microseconds)
            self.assertEqual(future.result(timeout=10), 409)
        order.refresh_from_db(); wait.refresh_from_db()
        self.assertEqual((order.status, wait.status), ('IN_SERVICE', 'PAUSED'))

    @override_settings(ROOM_AUTO_CONFIRMATION_ENABLED=True)
    def test_due_auto_task_waits_for_late_objection_transaction(self):
        require_disposable_mysql(connection)
        boss, provider = make_user(), make_provider(deposit_mode='EARNINGS')
        boss_account, provider_account = [get_or_create_account_for_legacy_user(u) for u in (boss, provider)]
        order = make_order(boss, service=make_service(room_enabled=True), customer_account=boss_account,
                           payment_status='PAID', settlement_mode='REPORT_APPROVAL')
        def assign(current):
            current.provider, current.provider_account = provider, provider_account
            OrderProvider.objects.create(order=current, provider=provider, provider_account=provider_account)
        transition(order.pk, Order.Status.GRABBED, side_effect=assign, update_fields=['provider', 'provider_account'])
        transition(order.pk, Order.Status.IN_SERVICE)
        application = OrderRoomCompletionRequest.objects.create(room=order.room, submitted_by=provider_account,
            request_id='request', description='已完成', participants=[provider_account.pk], status='VERIFIED',
            reviewed_by=provider_account, reviewed_at=timezone.now(), review_note='已核实', review_request_id='review')
        wait = OrderRoomConfirmation.objects.create(request=application, config_minutes=30, config_version=1,
            started_at=timezone.now()-timedelta(minutes=31), deadline=timezone.now()-timedelta(minutes=1))
        started, finished = Event(), Event()
        def run_auto():
            close_old_connections()
            try:
                from orders.room_auto_confirmation import process_one
                started.set()
                return process_one(wait.pk)
            finally:
                finished.set()
                close_old_connections()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                Order.objects.select_for_update().get(pk=order.pk)
                future = pool.submit(run_auto)
                self.assertTrue(started.wait(5))
                limit, waiting = monotonic()+5, 0
                while monotonic() < limit and not waiting and not finished.is_set():
                    waiting = mysql_locking_statements(connection, 'orders_order')
                    if not waiting:
                        sleep(0.03)
                self.assertGreater(waiting, 0, 'auto task did not wait on the order row')
                wait.status, wait.paused_at, wait.deadline = 'PAUSED', timezone.now(), None
                wait.remaining_microseconds = 0
                wait.save(update_fields=['status', 'paused_at', 'deadline', 'remaining_microseconds'])
                OrderRoomObjection.objects.create(room=order.room, customer=boss_account, request_id='late-objection',
                    reason='待核对', confirmation=wait, remaining_microseconds=0)
            self.assertEqual(future.result(timeout=10), 'skip')
        order.refresh_from_db(); wait.refresh_from_db()
        self.assertEqual((order.status, wait.status), ('IN_SERVICE', 'PAUSED'))
