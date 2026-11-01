"""Offline customers can see the result of a genuinely expired order."""
from datetime import timedelta
from io import StringIO
from uuid import uuid4

from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tasks import auto_cancel_pending_order
from orders.tests.factories import make_order, make_service, make_user, make_wallet
from orders.tests.test_direct_timeout import DirectTimeoutFixture
from site_messages.models import Message
from wallet.models import Transaction, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class TimeoutCustomerNoticeTest(APITransactionTestCase):
    def setUp(self):
        self.boss = make_user()
        self.account = get_or_create_account_for_legacy_user(self.boss)
        self.service = make_service(price=10000)
        make_wallet(self.boss, balance=50000)
        token, _ = issue_account_tokens(self.account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def create_paid(self, route):
        response = self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'dispatch_route': route,
            'client_request_id': str(uuid4()),
        }, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order_id = response.data['data']['id']
        # Advance the deadline only; cancellation and refund execute real code.
        Order.objects.filter(pk=order_id).update(auto_cancel_at=timezone.now() - timedelta(seconds=1))
        return order_id

    def verify_notice(self, order_id):
        response = self.client.get('/api/messages/', {'type': 'ORDER'})
        self.assertEqual(response.data['code'], 0, response.data)
        messages = [item for item in response.data['data']['messages'] if item['related_order_id'] == order_id]
        self.assertEqual(len(messages), 1)
        message = messages[0]
        self.assertEqual(message['title'], '订单超时已取消')
        self.assertIn('已退回兴安币余额', message['preview'])
        self.assertFalse(message['is_read'])
        self.assertEqual(message['action_url'], f'/pages/orderList/index?orderId={order_id}')
        detail = self.client.get(f'/api/orders/orders/{order_id}/').data['data']
        self.assertEqual((detail['status'], detail['payment_status']), ('CANCELLED', 'REFUNDED'))

    def test_task_and_command_each_record_once_after_wallet_refund(self):
        task_order = self.create_paid('PUBLIC')
        self.assertEqual(auto_cancel_pending_order.run(task_order), 'cancelled')
        self.assertNotEqual(auto_cancel_pending_order.run(task_order), 'cancelled')
        call_command('check_order_timeout', stdout=StringIO())
        self.verify_notice(task_order)

        command_order = self.create_paid('STAFF')
        call_command('check_order_timeout', stdout=StringIO())
        call_command('check_order_timeout', stdout=StringIO())
        self.assertNotEqual(auto_cancel_pending_order.run(command_order), 'cancelled')
        self.verify_notice(command_order)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 50000)
        for order_id in (task_order, command_order):
            self.assertEqual(Transaction.objects.filter(order_id=order_id, tx_type='REFUND').count(), 1)
            self.assertEqual(Message.objects.filter(recipient_account=self.account, related_order_id=order_id).count(), 1)

    def test_legacy_unpaid_cancel_does_not_claim_a_refund(self):
        order = make_order(self.boss, service=self.service, customer_account=self.account,
                           payment_status=Order.PaymentStatus.UNPAID,
                           auto_cancel_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(auto_cancel_pending_order.run(order.pk), 'cancelled')
        message = Message.objects.get(recipient_account=self.account, related_order_id=order.pk)
        self.assertIn('已自动取消', message.preview)
        self.assertNotIn('余额', message.preview)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='REFUND').exists())

    def test_new_pending_deadline_is_not_announced_as_cancelled(self):
        order_id = self.create_paid('STAFF')
        Order.objects.filter(pk=order_id).update(auto_cancel_at=timezone.now() + timedelta(minutes=10))
        call_command('check_order_timeout', stdout=StringIO())
        self.assertEqual(Order.objects.get(pk=order_id).status, 'PENDING')
        self.assertFalse(Message.objects.filter(related_order_id=order_id).exists())
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 40000)


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True)
class DirectTimeoutNoticeTest(DirectTimeoutFixture, APITransactionTestCase):
    def test_test_domain_refund_reservation_is_not_reported_as_wallet_refund(self):
        intent, wallet = self.make_paid_direct_order()
        self.assertEqual(auto_cancel_pending_order.run(intent.order_id), 'refund_reserved')
        call_command('check_order_timeout', stdout=StringIO())
        order = Order.objects.get(pk=intent.order_id)
        self.assertEqual((order.status, order.payment_status), ('CANCELLED', 'PAID'))
        self.assertIsNone(order.refunded_at)
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 50000)
        self.assertFalse(Message.objects.filter(related_order_id=order.pk).exists())

    def test_disabled_gate_does_not_mark_valid_quote_as_conflict_and_can_recover(self):
        intent, wallet = self.make_paid_direct_order()
        with override_settings(WECHAT_DIRECT_DOMAIN_TESTING=False):
            self.assertEqual(auto_cancel_pending_order.run(intent.order_id), 'skip')
            with self.assertLogs('orders.management.commands.check_order_timeout', level='ERROR'):
                call_command('check_order_timeout', stdout=StringIO())
            intent.refresh_from_db()
            self.assertEqual((intent.status, intent.review_reason), ('PAID', ''))
            self.assertEqual(Order.objects.get(pk=intent.order_id).status, 'PENDING')
            self.assertFalse(intent.channel_refunds.exists())
        # Re-enabling the isolated test domain preserves the genuine next step.
        self.assertEqual(auto_cancel_pending_order.run(intent.order_id), 'refund_reserved')
        intent.refresh_from_db()
        self.assertNotEqual(intent.review_reason, 'FEE_QUOTE_SNAPSHOT_CONFLICT')
        self.assertEqual(intent.channel_refunds.count(), 1)
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 50000)
