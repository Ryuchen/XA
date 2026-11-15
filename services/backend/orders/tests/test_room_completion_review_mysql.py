"""Only disposable MySQL can verify that room submissions wait on order locks."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep
from unittest import skipUnless

from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from common.mysql_test_support import mysql_locking_statements, require_disposable_mysql
from orders.models import Order, OrderProvider, OrderRoomObjection, OrderRoomCompletionRequest
from orders.state_machine import transition
from orders.tests.factories import make_order, make_provider, make_service, make_user


@skipUnless(connection.vendor == 'mysql', 'Requires isolated MySQL row locks')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], ORIGINAL_ATTRIBUTION_ENABLED=False,
                   ROOM_COMPLETION_REVIEW_ENABLED=True)
class RoomCompletionReviewMySQLTest(TransactionTestCase):
    def test_submission_waits_for_order_lock_and_observes_committed_objection(self):
        require_disposable_mysql(connection)
        boss, provider = make_user(), make_provider(deposit_mode='EARNINGS')
        boss_account, player_account = [get_or_create_account_for_legacy_user(u) for u in (boss, provider)]
        order = make_order(boss, service=make_service(room_enabled=True), customer_account=boss_account, payment_status='PAID')
        def assign(row):
            row.provider, row.provider_account = provider, player_account
            OrderProvider.objects.create(order=row, provider=provider, provider_account=player_account)
        transition(order.pk, Order.Status.GRABBED, side_effect=assign, update_fields=['provider', 'provider_account'])
        transition(order.pk, Order.Status.IN_SERVICE)
        token = issue_account_tokens(player_account)[0]
        started, finished = Event(), Event()
        def submit():
            close_old_connections()
            try:
                client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer '+token)
                started.set()
                return client.post(f'/api/orders/orders/{order.pk}/room/completion-requests/',
                    {'description': '已达成', 'client_request_id': 'mysql-race'}, format='json').status_code
            finally:
                finished.set()
                close_old_connections()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                locked = Order.objects.select_for_update().get(pk=order.pk)
                future = pool.submit(submit)
                self.assertTrue(started.wait(5))
                deadline, waiting = monotonic()+5, 0
                while monotonic() < deadline and not waiting and not finished.is_set():
                    waiting = mysql_locking_statements(connection, 'orders_order')
                    if not waiting:
                        sleep(0.03)
                self.assertGreater(waiting, 0, 'submission did not reach the competing MySQL order lock')
                OrderRoomObjection.objects.create(room=locked.room, customer=boss_account,
                                                   request_id='racing-objection', reason='需核对')
            self.assertEqual(future.result(timeout=10), 409)
        self.assertFalse(OrderRoomCompletionRequest.objects.exists())
