"""Exercise the test-only paid opening marker through actual order APIs."""
from unittest import skipUnless
from unittest.mock import patch

from django.db import connection
from django.test import override_settings
from rest_framework.test import APITransactionTestCase

from orders.grab_opening_intent import observe_committed_payment
from orders.models import Order, OrderGrabPoolOpeningIntent
from orders.tests import test_game_commission_payment as fixture
from orders.tests import test_grab_single_flow as grab_fixture
from orders.tests.factories import make_console_user
from orders.tests import test_staff_points_dispatch as points_fixture
from wallet.models import Transaction


@override_settings(GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True, GRAB_SINGLE_WINDOW_ENABLED=True,
                   GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
                   REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED=True, GAME_COMMISSION_PAYMENT_ENABLED=False,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class GrabOpeningPaymentFlowTest(APITransactionTestCase):
    setUp = fixture.GameCommissionPaymentTest.setUp
    link = fixture.GameCommissionPaymentTest.link
    auth = fixture.GameCommissionPaymentTest.auth
    create = fixture.GameCommissionPaymentTest.create
    prepare = grab_fixture.GrabSingleFlowTest.prepare

    def staff(self):
        user = make_console_user(['order:view', 'order:dispatch'])
        account = self.link(user)
        user.admin_membership.account = account
        user.admin_membership.save(update_fields=['account'])
        return account

    def assert_observed_once(self, order):
        intent = OrderGrabPoolOpeningIntent.objects.get(order=order)
        self.assertEqual(intent.state, 'OBSERVED')
        self.assertEqual(intent.payment_id, Transaction.objects.get(order=order, tx_type='PAY').pk)
        self.assertEqual(intent.paid_amount, order.amount)
        self.assertIsNotNone(intent.commit_observed_at)
        return intent

    def test_boss_payment_replay_keeps_single_committed_observation(self):
        self.prepare(); self.auth(self.boss_account)
        response = self.create(dispatch_mode='AUTO_GRAB')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        intent = self.assert_observed_once(order)
        replay = self.create(dispatch_mode='AUTO_GRAB')
        self.assertEqual(replay.data['code'], 409, replay.data)
        self.assertEqual(replay.data['fundResult']['data']['id'], order.pk)
        self.assertEqual(self.assert_observed_once(order).commit_observed_at, intent.commit_observed_at)
        self.assertEqual(Order.objects.count(), 1)

    def test_staff_payment_replay_keeps_single_committed_observation(self):
        self.prepare(); self.auth(self.staff())
        payload = {'customer_id': self.boss_account.pk, 'service_id': self.service.pk,
                   'client_request_id': 'staff-opening-payment'}
        response = self.client.post('/api/admin/orders/dispatch/', payload, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        intent = self.assert_observed_once(order)
        replay = self.client.post('/api/admin/orders/dispatch/', payload, format='json')
        self.assertEqual(replay.data['code'], 409, replay.data)
        self.assertEqual(self.assert_observed_once(order).commit_observed_at, intent.commit_observed_at)
        self.assertEqual(Order.objects.count(), 1)

    def test_marker_write_failure_rolls_back_actual_payment_and_order(self):
        self.prepare(); self.auth(self.boss_account)
        with patch('orders.grab_opening_intent.record_in_payment_transaction', side_effect=RuntimeError('marker failure')):
            with self.assertRaisesMessage(RuntimeError, 'marker failure'):
                self.create(dispatch_mode='AUTO_GRAB')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 100000)
        self.assertFalse(Order.objects.exists())
        self.assertFalse(OrderGrabPoolOpeningIntent.objects.exists())
        self.assertFalse(Transaction.objects.filter(tx_type='PAY').exists())

    def test_failed_postcommit_observation_is_recovered_without_repayment(self):
        self.prepare(); self.auth(self.boss_account)
        with patch('orders.grab_opening_intent._clock_observation', side_effect=ValueError('observation unavailable')):
            response = self.create(dispatch_mode='AUTO_GRAB')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        self.assertEqual(OrderGrabPoolOpeningIntent.objects.get(order=order).state, 'PENDING')
        replay = self.create(dispatch_mode='AUTO_GRAB')
        self.assertEqual(replay.data['code'], 409, replay.data)
        self.assertEqual(replay.data['fundResult']['data']['id'], order.pk)
        self.assertEqual(observe_committed_payment(order.pk).state, 'OBSERVED')
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)


@skipUnless(connection.vendor == 'sqlite', '此用例使用 SQLite 专用积分试验入口')
@override_settings(POINTS_REDEMPTION_PREVIEW_ENABLED=True, DISCOUNT_OPTIONS_PREVIEW_ENABLED=True,
    DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
    POINTS_USAGE_SWITCHES_ENABLED=True, SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True,
    BOSS_FUNDING_SELECTION_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=True,
    ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    SERVICE_POINTS_ASSIGN_TESTING=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED=True, GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True,
    GRAB_TEAM_WINDOW_ENABLED=True, GRAB_SINGLE_WINDOW_ENABLED=True,
    GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True, CELERY_TASK_ALWAYS_EAGER=False,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    ALLOWED_HOSTS=['*'])
class GrabOpeningPointsPaymentTest(APITransactionTestCase):
    setUp = points_fixture.StaffPointsDispatchTest.setUp
    prepare = points_fixture.StaffPointsDispatchTest.prepare
    create = points_fixture.StaffPointsDispatchTest.create

    def test_staff_points_and_mixed_sources_record_actual_coin_payment(self):
        from orders.models import OrderPointsRedemption
        self.prepare()
        order = self.create()
        intent = OrderGrabPoolOpeningIntent.objects.get(order=order)
        payment = Transaction.objects.get(order=order, tx_type='PAY')
        redemption = OrderPointsRedemption.objects.get(order=order)
        self.assertEqual((intent.state, intent.paid_amount, intent.payment_id),
                         ('OBSERVED', redemption.paid_units, payment.pk))
        allocations = {row.kind: row.amount for row in order.funding_allocations.all()}
        self.assertEqual(allocations['GIFT'], 600)
        self.assertEqual(allocations['COMPENSATION'], 400)
        self.assertEqual(sum(allocations.values()), order.amount)
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual(self.wallet.balance, 25000 - order.amount)
        self.assertEqual(self.profile.points_balance, 15000 - redemption.points_hundredths)

    def test_marker_failure_rolls_back_points_mixed_funding_and_wallet(self):
        from engagement.models import PointFundingLot, PointLedger
        from orders.models import OrderPointsRedemption
        self.prepare()
        with patch('orders.grab_opening_intent.record_in_payment_transaction', side_effect=RuntimeError('marker failure')):
            with self.assertRaisesMessage(RuntimeError, 'marker failure'):
                self.create()
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (25000, 15000))
        remaining = {row.kind: row.remaining for row in PointFundingLot.objects.filter(account=self.account)}
        self.assertEqual(remaining, {'PREPAID': 24000, 'GIFT': 600, 'COMPENSATION': 400})
        self.assertFalse(Order.objects.exists())
        self.assertFalse(OrderPointsRedemption.objects.exists())
        self.assertFalse(OrderGrabPoolOpeningIntent.objects.exists())
        self.assertFalse(PointLedger.objects.filter(kind='ORDER_POINTS_REDEEM').exists())
        self.assertFalse(Transaction.objects.filter(tx_type='PAY').exists())
