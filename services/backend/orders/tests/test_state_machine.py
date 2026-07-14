"""订单状态机 transition 的单元测试。"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from orders.models import Order, OrderStatusLog
from orders.state_machine import (
    IllegalTransitionError,
    can_transition,
    transition,
)
from orders.tests.factories import make_order, make_provider, make_service, make_user


class CanTransitionTest(TestCase):
    def test_legal_pairs(self):
        self.assertTrue(can_transition(Order.Status.PENDING, Order.Status.GRABBED))
        self.assertTrue(can_transition(Order.Status.GRABBED, Order.Status.IN_SERVICE))
        self.assertTrue(can_transition(Order.Status.IN_SERVICE, Order.Status.COMPLETED))
        self.assertTrue(can_transition(Order.Status.GRABBED, Order.Status.PENDING))
        self.assertTrue(can_transition(Order.Status.IN_SERVICE, Order.Status.CANCELLED))

    def test_illegal_pairs(self):
        self.assertFalse(can_transition(Order.Status.PENDING, Order.Status.IN_SERVICE))
        self.assertFalse(can_transition(Order.Status.PENDING, Order.Status.COMPLETED))
        self.assertFalse(can_transition(Order.Status.COMPLETED, Order.Status.PENDING))
        self.assertFalse(can_transition(Order.Status.CANCELLED, Order.Status.PENDING))


class TransitionTest(TestCase):
    def setUp(self):
        self.customer = make_user()
        self.provider = make_provider()
        self.service = make_service()

    def _order(self, status=Order.Status.PENDING):
        return make_order(self.customer, service=self.service, provider=self.provider, status=status)

    def test_transition_updates_status_and_timestamp(self):
        order = self._order(Order.Status.PENDING)
        order.auto_cancel_at = timezone.now() + timedelta(minutes=30)
        order.save(update_fields=['auto_cancel_at'])
        result = transition(order.id, Order.Status.GRABBED)
        self.assertEqual(result.status, Order.Status.GRABBED)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.GRABBED)
        self.assertIsNotNone(order.grabbed_at)
        self.assertIsNone(order.auto_cancel_at)

    def test_transition_writes_status_log(self):
        order = self._order(Order.Status.PENDING)
        transition(order.id, Order.Status.GRABBED, operator=self.provider, reason='接单')
        log = OrderStatusLog.objects.get(order=order)
        self.assertEqual(log.action, OrderStatusLog.Action.GRAB)
        self.assertEqual(log.from_status, Order.Status.PENDING)
        self.assertEqual(log.to_status, Order.Status.GRABBED)
        self.assertEqual(log.operator_id, self.provider.id)
        self.assertEqual(log.reason, '接单')

    def test_illegal_transition_raises(self):
        order = self._order(Order.Status.PENDING)
        with self.assertRaises(IllegalTransitionError):
            transition(order.id, Order.Status.COMPLETED)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertFalse(OrderStatusLog.objects.filter(order=order).exists())

    def test_terminal_status_cannot_transition(self):
        order = self._order(Order.Status.COMPLETED)
        with self.assertRaises(IllegalTransitionError):
            transition(order.id, Order.Status.CANCELLED)

    def test_missing_order_raises_does_not_exist(self):
        with self.assertRaises(Order.DoesNotExist):
            transition(999999, Order.Status.GRABBED)

    def test_pre_check_failure_aborts_transition(self):
        order = self._order(Order.Status.PENDING)

        def pre_check(_order):
            raise ValueError('校验失败')

        with self.assertRaises(ValueError):
            transition(order.id, Order.Status.GRABBED, pre_check=pre_check)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertFalse(OrderStatusLog.objects.filter(order=order).exists())

    def test_side_effect_is_invoked(self):
        order = self._order(Order.Status.GRABBED)
        captured = {}

        def side_effect(o):
            captured['status'] = o.status

        transition(order.id, Order.Status.IN_SERVICE, side_effect=side_effect)
        self.assertEqual(captured['status'], Order.Status.IN_SERVICE)

    def test_side_effect_failure_rolls_back(self):
        order = self._order(Order.Status.GRABBED)

        def side_effect(_o):
            raise RuntimeError('副作用失败')

        with self.assertRaises(RuntimeError):
            transition(order.id, Order.Status.IN_SERVICE, side_effect=side_effect)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.GRABBED)
        self.assertFalse(OrderStatusLog.objects.filter(order=order).exists())

    def test_reject_flow_back_to_pending(self):
        order = self._order(Order.Status.GRABBED)
        transition(order.id, Order.Status.PENDING)
        log = OrderStatusLog.objects.get(order=order)
        self.assertEqual(log.action, OrderStatusLog.Action.REJECT)

    def test_full_happy_path(self):
        order = self._order(Order.Status.PENDING)
        transition(order.id, Order.Status.GRABBED)
        transition(order.id, Order.Status.IN_SERVICE)
        transition(order.id, Order.Status.COMPLETED)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.COMPLETED)
        self.assertIsNotNone(order.completed_at)
        self.assertEqual(OrderStatusLog.objects.filter(order=order).count(), 3)
