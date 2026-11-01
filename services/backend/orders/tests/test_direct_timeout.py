from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.direct_payment import VerifiedPayment, confirm_verified_payment, prepare_direct_order
from orders.direct_refund_models import DirectPaymentRefund
from orders.models import Order
from orders.state_machine import IllegalTransitionError, transition
from orders.tasks import auto_cancel_pending_order
from orders.tests.factories import make_order, make_provider, make_service, make_user, make_wallet
from orders.timeout import reserve_expired_direct_refund
from wallet.models import Transaction, Wallet


class DirectTimeoutFixture:
    def make_paid_direct_order(self):
        user = make_user()
        account = get_or_create_account_for_legacy_user(user)
        account.openid, account.wechat_appid, account.is_openid_bound = 'test-openid', 'wx-test', True
        account.save(update_fields=['openid', 'wechat_appid', 'is_openid_bound'])
        wallet = make_wallet(user, balance=50000)
        service = make_service(price=10000)
        request = SimpleNamespace(account=account, legacy_user=user, data={
            'service_id': service.pk, 'expected_amount': 10000,
            'client_request_id': 'timeout-payment-1', 'dispatch_mode': 'AUTO_GRAB',
        })
        intent = prepare_direct_order(request, appid='wx-test', mchid='mch-test')
        evidence = VerifiedPayment(intent.merchant_order_no, 'wx-timeout-transaction', intent.appid,
            intent.mchid, intent.openid, intent.amount_fen, 'CNY', timezone.now(),
            payer_total_fen=intent.amount_fen, payer_currency='CNY')
        confirm_verified_payment(intent.order_id, evidence)
        Order.objects.filter(pk=intent.order_id).update(auto_cancel_at=timezone.now()-timedelta(seconds=1))
        return intent, wallet


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True)
class DirectTimeoutTests(DirectTimeoutFixture, TestCase):
    def setUp(self):
        self.intent, self.wallet = self.make_paid_direct_order()

    def test_task_reserves_once_and_does_not_claim_refund_arrived(self):
        with patch('orders.notifier.notify_order_update'):
            self.assertEqual(auto_cancel_pending_order.run(self.intent.order_id), 'refund_reserved')
            self.assertNotEqual(auto_cancel_pending_order.run(self.intent.order_id), 'refund_reserved')
        order = Order.objects.get(pk=self.intent.order_id)
        refund = DirectPaymentRefund.objects.get(intent=self.intent)
        self.assertEqual((order.status, order.payment_status, refund.status), ('CANCELLED', 'PAID', 'RESERVED'))
        self.assertEqual(refund.request_id, f'timeout-{order.pk}')
        self.assertIsNone(order.refunded_at)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 50000)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='REFUND').exists())

    def test_locked_deadline_and_assignment_are_rechecked(self):
        order = Order.objects.get(pk=self.intent.order_id)
        order.auto_cancel_at = timezone.now() + timedelta(minutes=30)
        order.save(update_fields=['auto_cancel_at'])
        self.assertIsNone(reserve_expired_direct_refund(order.pk))
        provider = make_provider()
        Order.objects.filter(pk=order.pk).update(provider=provider, auto_cancel_at=timezone.now()-timedelta(seconds=1))
        self.assertIsNone(reserve_expired_direct_refund(order.pk))
        Order.objects.filter(pk=order.pk).update(status='GRABBED', auto_cancel_at=timezone.now()-timedelta(seconds=1))
        self.assertIsNone(reserve_expired_direct_refund(order.pk))
        self.assertFalse(DirectPaymentRefund.objects.filter(intent=self.intent).exists())

    def test_account_cycle_change_does_not_cancel_order(self):
        self.intent.account.business_cycle += 1
        self.intent.account.save(update_fields=['business_cycle'])
        with self.assertLogs('orders.timeout', level='ERROR') as logs:
            self.assertIsNone(reserve_expired_direct_refund(self.intent.order_id))
        self.assertIn('账号周期已变化', '\n'.join(logs.output))
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).status, 'PENDING')
        self.assertFalse(DirectPaymentRefund.objects.filter(intent=self.intent).exists())

    def test_missing_wallet_is_reported_without_creating_one(self):
        self.wallet.delete()
        with self.assertLogs('orders.timeout', level='ERROR') as logs:
            self.assertIsNone(reserve_expired_direct_refund(self.intent.order_id))
        self.assertIn('钱包身份不完整', '\n'.join(logs.output))
        self.assertFalse(Wallet.objects.filter(account=self.intent.account).exists())
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).status, 'PENDING')
        self.assertFalse(DirectPaymentRefund.objects.filter(intent=self.intent).exists())

    @override_settings(WECHAT_DIRECT_DOMAIN_TESTING=False)
    def test_production_gate_keeps_direct_order_unchanged(self):
        self.assertEqual(auto_cancel_pending_order.run(self.intent.order_id), 'skip')
        order = Order.objects.get(pk=self.intent.order_id)
        self.assertEqual((order.status, order.payment_status), ('PENDING', 'PAID'))
        self.assertFalse(DirectPaymentRefund.objects.filter(intent=self.intent).exists())

    def test_command_isolates_failed_direct_order_and_processes_legacy_order(self):
        legacy_user = make_user()
        legacy_account = get_or_create_account_for_legacy_user(legacy_user)
        legacy_wallet = make_wallet(legacy_user, balance=10000)
        legacy = make_order(legacy_user, customer_account=legacy_account, payment_status='PAID',
                            auto_cancel_at=timezone.now()-timedelta(minutes=1))
        # Force the failing direct order to be encountered before the legacy
        # order under Order's newest-first default ordering.
        Order.objects.filter(pk=self.intent.order_id).update(created_at=timezone.now()+timedelta(seconds=1))
        out = StringIO()
        with patch('orders.timeout.reserve_expired_direct_refund', side_effect=RuntimeError('test failure')), \
             patch('orders.management.commands.check_order_timeout.notify_order_update'):
            call_command('check_order_timeout', stdout=out)
        legacy.refresh_from_db()
        legacy_wallet.refresh_from_db()
        self.assertEqual(legacy.status, 'CANCELLED')
        self.assertEqual(legacy_wallet.balance, 10000 + legacy.amount)
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).status, 'PENDING')
        self.assertIn('共 2 笔，已取消 1 笔', out.getvalue())

    @override_settings(WECHAT_DIRECT_DOMAIN_TESTING=False)
    def test_disabled_gate_is_logged_and_does_not_stop_legacy_scanning(self):
        legacy_user = make_user()
        legacy_account = get_or_create_account_for_legacy_user(legacy_user)
        legacy_wallet = make_wallet(legacy_user, balance=10000)
        legacy = make_order(legacy_user, customer_account=legacy_account, payment_status='PAID',
                            auto_cancel_at=timezone.now()-timedelta(minutes=1))
        Order.objects.filter(pk=self.intent.order_id).update(created_at=timezone.now()+timedelta(seconds=1))
        with self.assertLogs('orders.management.commands.check_order_timeout', level='ERROR') as logs, \
             patch('orders.management.commands.check_order_timeout.notify_order_update'):
            call_command('check_order_timeout', stdout=StringIO())
        self.assertIn('退款预留失败', '\n'.join(logs.output))
        legacy.refresh_from_db()
        legacy_wallet.refresh_from_db()
        self.assertEqual((legacy.status, legacy_wallet.balance), ('CANCELLED', 10000 + legacy.amount))
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).status, 'PENDING')
        self.assertFalse(DirectPaymentRefund.objects.filter(intent=self.intent).exists())

    def test_command_reserves_direct_once(self):
        with patch('orders.management.commands.check_order_timeout.notify_order_update'):
            call_command('check_order_timeout', stdout=StringIO())
            call_command('check_order_timeout', stdout=StringIO())
        self.assertEqual(DirectPaymentRefund.objects.filter(intent=self.intent, status='RESERVED').count(), 1)
        self.assertEqual(Order.objects.get(pk=self.intent.order_id).payment_status, 'PAID')


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True)
@skipUnlessDBFeature('has_select_for_update')
class DirectTimeoutConcurrencyTests(DirectTimeoutFixture, TransactionTestCase):
    def setUp(self):
        self.intent, _ = self.make_paid_direct_order()

    def test_concurrent_scans_reserve_only_one_refund(self):
        barrier = Barrier(2)
        def scan(_):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return reserve_expired_direct_refund(self.intent.order_id) is not None
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(scan, (1, 2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(DirectPaymentRefund.objects.filter(intent=self.intent, status='RESERVED').count(), 1)

    def test_transition_and_timeout_are_serialized_by_order_lock(self):
        barrier = Barrier(2)
        def run(which):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                if which == 'grab':
                    try:
                        transition(self.intent.order_id, Order.Status.GRABBED)
                        return 'grabbed'
                    except IllegalTransitionError:
                        return 'skip'
                return 'reserved' if reserve_expired_direct_refund(self.intent.order_id) else 'skip'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(run, ('grab', 'timeout')))
        order = Order.objects.get(pk=self.intent.order_id)
        if order.status == 'GRABBED':
            self.assertIn('grabbed', results)
            self.assertEqual(DirectPaymentRefund.objects.filter(intent=self.intent).count(), 0)
        else:
            self.assertEqual(order.status, 'CANCELLED')
            self.assertIn('reserved', results)
            self.assertEqual(DirectPaymentRefund.objects.filter(intent=self.intent, status='RESERVED').count(), 1)
