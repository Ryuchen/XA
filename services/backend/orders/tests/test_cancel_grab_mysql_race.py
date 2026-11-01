"""A real grab can win after staff read a pending order but before cancellation locks it."""
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
from orders.models import Order, OrderStatusLog
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from wallet.models import Transaction, Wallet


def waiting_on_order(observer):
    with observer.cursor() as cursor:
        cursor.execute('SHOW ENGINE INNODB STATUS')
        status = cursor.fetchone()[2]
    transactions = status.split('LIST OF TRANSACTIONS FOR EACH SESSION:', 1)[1].split('FILE I/O', 1)[0]
    return sum('\nLOCK WAIT ' in entry and '`orders_order`' in entry
               for entry in transactions.split('---TRANSACTION'))


@skipUnless(connection.vendor == 'mysql', 'Requires real MySQL row locks')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class CancelGrabMySQLRaceTest(TransactionTestCase):
    def test_staff_cancel_must_not_cancel_an_order_grabbed_after_initial_read(self):
        boss, provider = make_user(), make_provider(deposit_mode='EARNINGS')
        staff = make_console_user(['order:view', 'order:cancel'])
        accounts = []
        for user in (boss, provider, staff):
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            accounts.append(account)
        provider.escort_profile.account = accounts[1]
        provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = accounts[2]
        staff.admin_membership.save(update_fields=['account'])
        wallet = make_wallet(boss, balance=50000)
        wallet.account = accounts[0]
        wallet.save(update_fields=['account'])
        service = make_service(price=10000, billing_provider_count=1)
        tokens = [issue_account_tokens(account)[0] for account in accounts]

        def post(token, url, body):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                return client.post(url, body, format='json').data
            finally:
                close_old_connections()

        created = post(tokens[0], '/api/orders/create/', {'service_id': service.pk,
            'dispatch_route': 'PUBLIC', 'dispatch_mode': 'AUTO_GRAB',
            'client_request_id': 'cancel-grab-race-order'})
        self.assertEqual(created['code'], 0, created)
        order_id = created['data']['id']
        Order.objects.filter(pk=order_id).update(pool_entered_at=timezone.now() - timedelta(hours=1))
        observer = connections['default'].copy()
        pool = ThreadPoolExecutor(max_workers=2)
        try:
            with transaction.atomic():
                Order.objects.select_for_update().get(pk=order_id)
                grabbed = pool.submit(post, tokens[1], f'/api/orders/orders/{order_id}/grab/', {})
                deadline = monotonic() + 15
                while waiting_on_order(observer) < 1 and monotonic() < deadline:
                    sleep(.05)
                self.assertGreaterEqual(waiting_on_order(observer), 1, 'grab did not reach order lock')
                cancelled = pool.submit(post, tokens[2], f'/api/admin/orders/{order_id}/cancel/', {'reason': '隔离测试'})
                deadline = monotonic() + 15
                while waiting_on_order(observer) < 2 and monotonic() < deadline:
                    sleep(.05)
                self.assertGreaterEqual(waiting_on_order(observer), 2, 'staff cancellation did not reach order lock')
            grab_result = grabbed.result(timeout=20)
            cancel_result = cancelled.result(timeout=20)
        finally:
            observer.close()
            pool.shutdown(wait=True)
        self.assertEqual(grab_result['code'], 0, grab_result)
        self.assertNotEqual(cancel_result['code'], 0, cancel_result)
        self.assertIn('仅待接单订单', cancel_result['msg'])
        order = Order.objects.get(pk=order_id)
        self.assertEqual((order.status, order.provider_account_id), ('GRABBED', accounts[1].pk))
        self.assertEqual(Wallet.objects.get(pk=wallet.pk).balance, 40000)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='REFUND').count(), 0)
        self.assertFalse(order.status_logs.filter(action=OrderStatusLog.Action.CANCEL).exists())
        for token, url in ((tokens[0], f'/api/orders/orders/{order_id}/'),
                           (tokens[1], f'/api/orders/orders/{order_id}/'),
                           (tokens[2], f'/api/admin/orders/{order_id}/')):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
            detail = client.get(url).data
            self.assertEqual(detail['code'], 0, detail)
            self.assertEqual((detail['data']['status'], detail['data']['payment_status']), ('GRABBED', 'PAID'))
