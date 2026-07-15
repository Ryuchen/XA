import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APITestCase

from integrations.kook import KookClient
from orders.kook_dispatch import build_order_card, enqueue_kook_dispatch
from orders.models import KookDispatchRecord, Order
from orders.tasks import send_kook_dispatch
from orders.tests.factories import make_order, make_provider, make_service, make_user


class KookClientTest(SimpleTestCase):
    @patch('integrations.kook.urlopen')
    def test_send_channel_card_uses_official_v3_contract(self, urlopen_mock):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            'code': 0, 'message': '操作成功',
            'data': {'msg_id': 'kook-message-1', 'msg_timestamp': 123},
        }).encode()
        urlopen_mock.return_value = response

        result = KookClient(token='secret', base_url='https://www.kookapp.cn/api/v3').send_channel_message(
            channel_id='channel-1', content='[]', message_type=10, nonce='nonce-1',
        )

        request = urlopen_mock.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, 'https://www.kookapp.cn/api/v3/message/create')
        self.assertEqual(request.headers['Authorization'], 'Bot secret')
        self.assertEqual(payload, {
            'type': 10, 'target_id': 'channel-1', 'content': '[]', 'nonce': 'nonce-1',
        })
        self.assertEqual(result.message_id, 'kook-message-1')


@override_settings(
    KOOK_ENABLED=True,
    KOOK_BOT_TOKEN='test-token',
    KOOK_DISPATCH_CHANNEL_ID='channel-1',
    KOOK_DISPATCH_NEW_ORDERS=True,
    KOOK_DISPATCH_REJECTED_ORDERS=True,
    KOOK_DISPATCH_MENTION_ROLE_IDS=('12345',),
    KOOK_ADMIN_ORDER_URL='https://admin.example.com/orders',
)
class KookOrderDispatchTest(TestCase):
    def setUp(self):
        self.customer = make_user()
        self.service = make_service(name='王者陪玩', price=1000)
        self.order = make_order(
            self.customer, service=self.service, status=Order.Status.PENDING,
            amount=2000, game_rounds=2, game_region='微信区',
            game_nickname='测试老板', remark='优先打野',
        )

    def test_card_contains_dispatch_fields_and_safe_plain_text(self):
        cards = json.loads(build_order_card(self.order, KookDispatchRecord.Trigger.NEW_ORDER))
        serialized = json.dumps(cards, ensure_ascii=False)
        self.assertIn(self.order.order_no, serialized)
        self.assertIn('王者陪玩', serialized)
        self.assertIn('200 兴安币', serialized)
        self.assertIn('(rol)12345(rol)', serialized)
        self.assertIn('order_no=', serialized)

    @patch('orders.kook_dispatch._send_task_safely')
    def test_enqueue_is_idempotent_for_same_dispatch_sequence(self, send_mock):
        with self.captureOnCommitCallbacks(execute=True):
            first = enqueue_kook_dispatch(self.order, KookDispatchRecord.Trigger.NEW_ORDER)
            second = enqueue_kook_dispatch(self.order, KookDispatchRecord.Trigger.NEW_ORDER)
        self.assertEqual(first.id, second.id)
        self.assertEqual(KookDispatchRecord.objects.count(), 1)
        send_mock.assert_called_once_with(first.id)

    @patch('integrations.kook.KookClient.send_channel_message')
    def test_task_persists_sent_message_id(self, send_mock):
        from integrations.kook import KookMessageResult
        send_mock.return_value = KookMessageResult('message-123', 123)
        record = KookDispatchRecord.objects.create(
            order=self.order, sequence=0,
            trigger=KookDispatchRecord.Trigger.NEW_ORDER, channel_id='channel-1',
        )

        result = send_kook_dispatch.run(record.id)

        record.refresh_from_db()
        self.assertEqual(result, 'sent:message-123')
        self.assertEqual(record.status, KookDispatchRecord.Status.SENT)
        self.assertEqual(record.message_id, 'message-123')
        self.assertEqual(record.attempts, 1)

    def test_task_skips_order_that_is_no_longer_pending(self):
        self.order.status = Order.Status.GRABBED
        self.order.save(update_fields=['status'])
        record = KookDispatchRecord.objects.create(
            order=self.order, sequence=0,
            trigger=KookDispatchRecord.Trigger.NEW_ORDER, channel_id='channel-1',
        )

        result = send_kook_dispatch.run(record.id)

        record.refresh_from_db()
        self.assertEqual(result, 'skip:status=GRABBED')
        self.assertEqual(record.status, KookDispatchRecord.Status.SKIPPED)


class KookDispatchFlowTest(APITestCase):
    def setUp(self):
        self.customer = make_user()
        self.provider = make_provider()
        self.service = make_service()

    @patch('orders.views.enqueue_kook_dispatch')
    @patch('orders.views._schedule_auto_cancel')
    def test_provider_rejection_triggers_second_dispatch(self, schedule_mock, enqueue_mock):
        order = make_order(
            self.customer, service=self.service, provider=self.provider,
            status=Order.Status.GRABBED,
        )
        self.client.force_authenticate(self.provider)

        response = self.client.post(f'/api/orders/orders/{order.id}/reject/', {'reason': '临时冲突'})

        self.assertEqual(response.data['code'], 0)
        order.refresh_from_db()
        self.assertEqual(order.reject_count, 1)
        enqueue_mock.assert_called_once_with(
            order, KookDispatchRecord.Trigger.PROVIDER_REJECTED,
        )
        schedule_mock.assert_called_once_with(order)
