"""Console refunds/cancellations publish their committed state exactly once."""
from unittest.mock import AsyncMock, patch

from django.db import connection, transaction
from django.test import override_settings
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.notifier import notify_order_update
from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user
from wallet.models import Transaction
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class ConsoleRefundNotificationTest(APITransactionTestCase):
    def setUp(self):
        self.boss = make_user()
        self.provider = make_provider()
        self.admin = make_console_user(['order:view', 'order:refund', 'order:cancel'])
        self.accounts = {}
        for user in (self.boss, self.provider, self.admin):
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            self.accounts[user.pk] = account
        membership = self.admin.admin_membership
        membership.account = self.accounts[self.admin.pk]
        membership.save(update_fields=['account'])
        token, _ = issue_account_tokens(self.accounts[self.admin.pk])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.wallet = get_wallet(account=self.accounts[self.boss.pk], user=self.boss)
        self.wallet.balance = 90000
        self.wallet.save(update_fields=['balance'])
        self.service = make_service(price=1000)

    def order(self, status=Order.Status.GRABBED):
        return make_order(
            self.boss, self.service,
            self.provider if status == Order.Status.GRABBED else None,
            status=status, customer_account=self.accounts[self.boss.pk],
            provider_account=self.accounts[self.provider.pk] if status == Order.Status.GRABBED else None,
            payment_status=Order.PaymentStatus.PAID,
        )

    def post(self, order, action):
        return self.client.post(
            f'/api/admin/orders/{order.pk}/{action}/',
            {'reason': '本地回归验证'}, format='json',
        )

    def assert_committed_notification(self, action, status):
        order = self.order(status)
        before = self.wallet.balance

        def publish(committed_order):
            self.assertFalse(connection.in_atomic_block)
            saved = Order.objects.get(pk=committed_order.pk)
            self.assertEqual((saved.status, saved.payment_status), ('CANCELLED', 'REFUNDED'))
            self.wallet.refresh_from_db()
            self.assertEqual(self.wallet.balance, before + order.amount)
            self.assertEqual(Transaction.objects.filter(order=order, tx_type='REFUND').count(), 1)
            notify_order_update(committed_order)

        with patch('orders.notifier.get_channel_layer') as get_layer:
            get_layer.return_value.group_send = AsyncMock()
            with patch('console.views.notify_order_update', side_effect=publish) as notify:
                response = self.post(order, action)
            self.assertEqual(response.data['code'], 0, response.data)
            notify.assert_called_once()
            own_group = f'account_{self.accounts[self.boss.pk].pk}'
            updates = [
                call.args[1]['data']
                for call in get_layer.return_value.group_send.call_args_list
                if call.args[0] == own_group
            ]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]['id'], order.pk)
        self.assertEqual((updates[0]['status'], updates[0]['payment_status']), ('CANCELLED', 'REFUNDED'))

    def test_refund_broadcasts_committed_refunded_state_to_boss(self):
        self.assert_committed_notification('refund', Order.Status.GRABBED)

    def test_cancel_broadcasts_committed_refunded_state_to_boss(self):
        self.assert_committed_notification('cancel', Order.Status.PENDING)

    def test_repeated_refund_and_cancel_do_not_credit_or_notify_twice(self):
        for action, status in (('refund', Order.Status.GRABBED), ('cancel', Order.Status.PENDING)):
            with self.subTest(action=action):
                order = self.order(status)
                self.wallet.refresh_from_db()
                before = self.wallet.balance
                with patch('console.views.notify_order_update') as notify:
                    self.assertEqual(self.post(order, action).data['code'], 0)
                    self.assertNotEqual(self.post(order, action).data['code'], 0)
                notify.assert_called_once()
                self.wallet.refresh_from_db()
                self.assertEqual(self.wallet.balance, before + order.amount)
                self.assertEqual(Transaction.objects.filter(order=order, tx_type='REFUND').count(), 1)

    def test_invalid_cancel_does_not_publish_or_refund(self):
        order = self.order()
        with patch('console.views.notify_order_update') as notify:
            self.assertNotEqual(self.post(order, 'cancel').data['code'], 0)
        notify.assert_not_called()
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.GRABBED)
        self.assertFalse(Transaction.objects.filter(order=order).exists())

    def test_ledger_failure_rolls_back_without_notification(self):
        order = self.order()
        with patch('console.views.notify_order_update') as notify:
            with patch('console.views.Transaction.objects.create', side_effect=RuntimeError('simulated ledger failure')):
                with self.assertRaisesRegex(RuntimeError, 'simulated ledger failure'):
                    self.post(order, 'refund')
        notify.assert_not_called()
        self.wallet.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(self.wallet.balance, 90000)
        self.assertEqual((order.status, order.payment_status), ('GRABBED', 'PAID'))
        self.assertFalse(Transaction.objects.filter(order=order).exists())

    def test_enclosing_commit_defers_notification_until_transaction_finishes(self):
        order = self.order()
        with patch('console.views.notify_order_update') as notify:
            with transaction.atomic():
                self.assertEqual(self.post(order, 'refund').data['code'], 0)
                notify.assert_not_called()
            notify.assert_called_once()

    def test_enclosing_rollback_discards_notification_and_refund(self):
        order = self.order()
        with patch('console.views.notify_order_update') as notify:
            with self.assertRaisesRegex(RuntimeError, 'rollback outer transaction'):
                with transaction.atomic():
                    self.assertEqual(self.post(order, 'refund').data['code'], 0)
                    notify.assert_not_called()
                    raise RuntimeError('rollback outer transaction')
        notify.assert_not_called()
        self.wallet.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(self.wallet.balance, 90000)
        self.assertEqual((order.status, order.payment_status), ('GRABBED', 'PAID'))
        self.assertFalse(Transaction.objects.filter(order=order).exists())

    def test_broken_notification_channel_cannot_fail_a_committed_refund(self):
        order = self.order()
        with patch('orders.notifier.get_channel_layer', side_effect=RuntimeError('channel unavailable')):
            with self.assertLogs('orders.notifier', level='WARNING'):
                response = self.post(order, 'refund')
        self.assertEqual(response.data['code'], 0, response.data)
        order.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ('CANCELLED', 'REFUNDED'))
        self.assertEqual(self.wallet.balance, 91000)
