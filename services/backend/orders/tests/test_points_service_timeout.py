"""Real points/wallet writes on isolated SQLite; checkout stays disabled."""
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from coupons.models import Coupon, UserCoupon
from engagement.models import PointFundingLot, PointLedger
from engagement.services import config
from orders.models import Order, OrderPointsRefund, OrderRefund
from orders.tasks import auto_cancel_pending_order
from orders.tests import test_points_service_refund as refund_fixture
from site_messages.models import Message
from wallet.models import Transaction


@override_settings(SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True,
                   SERVICE_ORDER_POINTS_TIMEOUT_TESTING=True)
class PointsServiceTimeoutTests(TestCase):
    def setUp(self):
        refund_fixture.ServicePointsRefundTests.setUp(self)
        self.order.refresh_from_db()
        self.order.auto_cancel_at = timezone.now() - timedelta(seconds=1)
        self.order.save(update_fields=['auto_cancel_at'])

    def assert_unmodified(self):
        self.order.refresh_from_db(); self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status), ('PENDING', 'PAID'))
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (1030, 5000))
        self.assertFalse(OrderRefund.objects.filter(order=self.order).exists())
        self.assertFalse(OrderPointsRefund.objects.filter(refund__order=self.order).exists())
        self.assertFalse(Message.objects.filter(related_order_id=self.order.pk).exists())

    def assert_refunded_once(self):
        self.order.refresh_from_db(); self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status), ('CANCELLED', 'REFUNDED'))
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (2000, 15000))
        self.assertEqual(list(PointFundingLot.objects.order_by('kind').values_list('remaining', flat=True)), [100, 100, 1800])
        receipt = OrderPointsRefund.objects.get(refund__order=self.order)
        self.assertEqual((receipt.coin_units, receipt.points_hundredths, receipt.is_final), (970, 10000, True))
        self.assertEqual(OrderRefund.objects.filter(order=self.order).count(), 1)
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='REFUND').count(), 1)
        self.assertEqual(PointLedger.objects.filter(order=self.order, kind='ORDER_POINTS_RETURN').count(), 1)
        message = Message.objects.get(related_order_id=self.order.pk)
        self.assertIn('抵扣积分已退回', message.preview)

    def test_task_refunds_assets_and_coupon_once_even_when_points_disabled(self):
        coupon = Coupon.objects.create(name='测试金额券', amount=10)
        user_coupon = UserCoupon.objects.create(user=self.user, account=self.account, coupon=coupon,
            order=self.order, status=UserCoupon.Status.USED, restore_on_refund=True)
        operation = config(); operation.points_enabled = False; operation.save(update_fields=['points_enabled'])
        self.assertEqual(auto_cancel_pending_order.run(self.order.pk), 'cancelled')
        self.assertNotEqual(auto_cancel_pending_order.run(self.order.pk), 'cancelled')
        call_command('check_order_timeout', stdout=StringIO())
        self.assert_refunded_once()
        user_coupon.refresh_from_db()
        self.assertEqual((user_coupon.status, user_coupon.order_id), ('UNUSED', None))
        self.assertEqual(OrderRefund.objects.get(order=self.order).benefit_effects['order_coupon_results'][0]['outcome'], 'RETURNED')

    def test_management_command_executes_same_atomic_path(self):
        call_command('check_order_timeout', stdout=StringIO())
        call_command('check_order_timeout', stdout=StringIO())
        self.assertNotEqual(auto_cancel_pending_order.run(self.order.pk), 'cancelled')
        self.assert_refunded_once()

    @override_settings(SERVICE_ORDER_POINTS_TIMEOUT_TESTING=False)
    def test_default_disabled_never_changes_order_or_money(self):
        self.assertEqual(auto_cancel_pending_order.run(self.order.pk), 'skip')
        call_command('check_order_timeout', stdout=StringIO())
        self.assert_unmodified()

    def test_tampered_redemption_is_not_refunded(self):
        ledger = self.order.points_redemption.ledger
        ledger.event_key = 'invalid'; ledger.save(update_fields=['event_key'])
        self.assertEqual(auto_cancel_pending_order.run(self.order.pk), 'skip')
        self.assert_unmodified()

    def test_missing_marker_cannot_fall_back_to_coin_only_refund(self):
        terms = dict(self.order.funding_terms)
        terms.pop('points_redemption_v1')
        Order.objects.filter(pk=self.order.pk).update(funding_terms=terms)
        self.assertEqual(auto_cancel_pending_order.run(self.order.pk), 'skip')
        call_command('check_order_timeout', stdout=StringIO())
        self.assert_unmodified()

    def test_receipt_alone_still_blocks_fallback_if_marker_and_ledger_kind_are_damaged(self):
        terms = dict(self.order.funding_terms)
        terms.pop('points_redemption_v1')
        Order.objects.filter(pk=self.order.pk).update(funding_terms=terms)
        PointLedger.objects.filter(pk=self.order.points_redemption.ledger_id).update(kind='CORRUPT')
        self.assertEqual(auto_cancel_pending_order.run(self.order.pk), 'skip')
        self.assert_unmodified()

    def test_absent_receipt_table_still_allows_ordinary_legacy_order(self):
        from django.db import DatabaseError
        from django.db.migrations.recorder import MigrationRecorder
        from stores.db import transaction
        from orders.points_service_payment import needs_integrated_processing
        other = Order.objects.create(customer=self.user, customer_account=self.account, service=self.service,
            amount=100, original_amount=100, business_cycle=self.account.business_cycle)
        connection = transaction.get_connection()
        # A genuinely older store has a migration history but has not yet
        # applied the receipt migration; a missing migration table is not
        # sufficient evidence that it is safe to assume no receipts exist.
        with transaction.atomic():
            MigrationRecorder.Migration.objects.using(connection.alias).filter(
                app='orders', name='0117_orderpointsredemption').delete()
            with patch.object(connection.introspection, 'table_names', return_value=['django_migrations']):
                self.assertFalse(needs_integrated_processing(other))
            with patch.object(connection.introspection, 'table_names', return_value=[]):
                with self.assertRaises(DatabaseError):
                    needs_integrated_processing(other)
            transaction.set_rollback(True)

    def test_test_gate_checks_active_tenant_connection(self):
        from orders.points_service_timeout import _require_isolated_test
        from orders.settlement import SettlementError
        unsafe = SimpleNamespace(settings_dict={'ENGINE': 'django.db.backends.mysql', 'NAME': 'store_b'})
        with patch('orders.points_service_timeout.transaction.get_connection', return_value=unsafe):
            with self.assertRaises(SettlementError):
                _require_isolated_test()

    def test_failure_after_credit_rolls_back_both_assets_and_order(self):
        with patch.object(OrderPointsRefund.objects, 'create', side_effect=RuntimeError('injected failure')):
            with self.assertRaises(RuntimeError):
                auto_cancel_pending_order.run(self.order.pk)
        self.assert_unmodified()
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='REFUND').exists())
        self.assertFalse(PointLedger.objects.filter(order=self.order, kind='ORDER_POINTS_RETURN').exists())

    def test_assigned_order_is_not_cancelled(self):
        self.order.provider = self.user
        self.order.save(update_fields=['provider'])
        self.assertEqual(auto_cancel_pending_order.run(self.order.pk), 'skip')
        self.assert_unmodified()

    def test_active_grab_pool_is_not_mistaken_for_no_candidate(self):
        from orders.grab_pool_models import OrderGrabPoolSnapshot
        now = timezone.now()
        OrderGrabPoolSnapshot.objects.create(order=self.order, opened_at=now - timedelta(minutes=2),
            original_total_deadline=now - timedelta(seconds=1), first_deadline=now - timedelta(seconds=1),
            total_deadline=now - timedelta(seconds=1), window_seconds=30, config_version=1,
            visibility_delays={}, required_provider_count=1)
        self.assertEqual(auto_cancel_pending_order.run(self.order.pk), 'skip')
        self.assert_unmodified()

    def test_conflicting_direct_and_points_evidence_blocks_both_timeout_paths(self):
        from orders.direct_payment_models import DirectPaymentIntent
        intent = DirectPaymentIntent.objects.create(order=self.order, account=self.account,
            client_request_id='test-conflict', request_fingerprint='test', merchant_order_no='test-conflict',
            appid='test', mchid='test', openid='test', amount_fen=97)
        self.assertEqual(auto_cancel_pending_order.run(self.order.pk), 'skip')
        call_command('check_order_timeout', stdout=StringIO())
        intent.refresh_from_db()
        self.assertEqual(intent.status, DirectPaymentIntent.Status.AWAITING)
        self.assert_unmodified()
