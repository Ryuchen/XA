from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import Order
from orders.state_machine import transition
from orders.tests.factories import make_order, make_user, make_wallet
from wallet.models import Transaction


class TimeoutManagementCommandTests(TestCase):
    def setUp(self):
        self.boss = make_user()
        self.account = get_or_create_account_for_legacy_user(self.boss)
        self.wallet = make_wallet(self.boss, balance=10000)
        self.order = make_order(self.boss, customer_account=self.account,
                                payment_status='PAID',
                                auto_cancel_at=timezone.now() - timedelta(minutes=1))

    def test_refreshed_pending_deadline_survives_old_scan(self):
        fresh_deadline = timezone.now() + timedelta(minutes=30)

        def rejection_between_scan_and_lock(*args, **kwargs):
            # Emulate an order grabbed and rejected after the expired queryset
            # was read, but before the command enters transition's row lock.
            Order.objects.filter(pk=self.order.pk).update(status=Order.Status.GRABBED)
            Order.objects.filter(pk=self.order.pk).update(
                status=Order.Status.PENDING, auto_cancel_at=fresh_deadline,
            )
            return transition(*args, **kwargs)

        output = StringIO()
        with patch('orders.management.commands.check_order_timeout.transition', side_effect=rejection_between_scan_and_lock):
            call_command('check_order_timeout', stdout=output)

        self.order.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)
        self.assertEqual(self.order.auto_cancel_at, fresh_deadline)
        self.assertEqual(self.wallet.balance, 10000)
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='REFUND').exists())
        self.assertIn('共 1 笔，已取消 0 笔', output.getvalue())

    def test_expired_order_is_refunded_once(self):
        output = StringIO()
        with patch('orders.management.commands.check_order_timeout.notify_order_update'):
            call_command('check_order_timeout', stdout=output)
            call_command('check_order_timeout', stdout=StringIO())
        self.order.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELLED)
        self.assertEqual(self.wallet.balance, 10000 + self.order.amount)
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='REFUND').count(), 1)
        self.assertIn('共 1 笔，已取消 1 笔', output.getvalue())
