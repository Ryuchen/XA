import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from django.db import close_old_connections
from django.test import SimpleTestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from integrations.kook import KookDeliveryUnknown, KookMessageResult
from integrations.wecom import WecomClient, WecomDeliveryUnknown, WecomError, valid_webhook
from orders.dispatching import build_dispatch_text, channel_config, deliver_dispatch, queue_dispatch
from orders.kook_dispatch import build_order_card
from orders.models import KookDispatchRecord, Order, WecomDispatchRecord
from orders.tests.factories import make_console_user, make_order, make_promotion, make_service, make_user
from wallet.models import Transaction, Wallet

TEST_WEBHOOK = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-only-key'


@override_settings(WECOM_DISPATCH_ENABLED=True, WECOM_DISPATCH_WEBHOOK=TEST_WEBHOOK)
class GroupDispatchConcurrencyTest(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    @patch('integrations.wecom.WecomClient.send_text')
    def test_duplicate_worker_skips_while_first_external_send_is_in_progress(self, send_mock):
        order = make_order(make_user(), amount=10000, provider_income=8000, payment_status='PAID')
        record = WecomDispatchRecord.objects.create(
            order=order, trigger='NEW_ORDER', channel_id=channel_config('WECOM')[1],
        )
        started, release = Event(), Event()

        def fake_send(content):
            started.set()
            if not release.wait(10):
                raise RuntimeError('Test sender was not released')

        def first_worker():
            close_old_connections()
            try:
                return deliver_dispatch('WECOM', record.pk)
            finally:
                close_old_connections()

        send_mock.side_effect = fake_send
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(first_worker)
            try:
                self.assertTrue(started.wait(10))
                self.assertEqual(deliver_dispatch('WECOM', record.pk), 'skip:delivery=SENDING')
            finally:
                release.set()
            self.assertEqual(first.result(timeout=10), 'sent:')
        send_mock.assert_called_once()
        record.refresh_from_db()
        self.assertEqual((record.status, record.attempts), ('SENT', 1))


class WecomClientTest(SimpleTestCase):
    @patch('integrations.wecom.urlopen')
    def test_official_text_contract_and_no_mentions(self, mock_open):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"errcode":0,"errmsg":"ok"}'
        mock_open.return_value = response
        WecomClient(TEST_WEBHOOK).send_text('打手单钱：640 兴安币（¥64.00）')
        req = mock_open.call_args.args[0]
        self.assertEqual(req.full_url, TEST_WEBHOOK)
        self.assertEqual(json.loads(req.data), {
            'msgtype': 'text', 'text': {'content': '打手单钱：640 兴安币（¥64.00）'},
        })

    def test_webhook_rejects_nonofficial_hosts_and_redirect_targets(self):
        for url in ('http://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x',
                    'https://qyapi.weixin.qq.com.evil.test/cgi-bin/webhook/send?key=x',
                    'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x&key=y',
                    'https://qyapi.weixin.qq.com/cgi-bin/webhook/send',
                    'http://127.0.0.1:8000/admin/'):
            with self.subTest(url=url):
                self.assertFalse(valid_webhook(url))
        self.assertTrue(valid_webhook(TEST_WEBHOOK))

    @patch('integrations.wecom.urlopen', side_effect=URLError(TEST_WEBHOOK))
    def test_network_error_redacts_key_and_is_not_safe_to_retry(self, mock_open):
        with self.assertRaises(WecomDeliveryUnknown) as caught:
            WecomClient(TEST_WEBHOOK).send_text('派单')
        self.assertNotIn('test-only-key', str(caught.exception))

    @patch('integrations.wecom.urlopen')
    def test_utf8_byte_limit_prevents_request(self, mock_open):
        with self.assertRaises(WecomError):
            WecomClient(TEST_WEBHOOK).send_text('单' * 683)
        mock_open.assert_not_called()


@override_settings(
    LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=True, KOOK_BOT_TOKEN='test-only-token',
    KOOK_DISPATCH_CHANNEL_ID='test-channel', WECOM_DISPATCH_ENABLED=True,
    WECOM_DISPATCH_WEBHOOK=TEST_WEBHOOK,
)
class GroupDispatchTest(APITestCase):
    def setUp(self):
        self.boss = make_user()
        self.staff = make_console_user(['order:view', 'order:dispatch'])
        self.viewer = make_console_user(['order:view'])
        for user in (self.boss, self.staff, self.viewer):
            account = get_or_create_account_for_legacy_user(user)
            if user != self.boss:
                membership = user.admin_membership
                membership.account = account
                membership.save(update_fields=['account'])
        self.service = make_service(price=10000, commission_rate=20)
        self.order = make_order(
            self.boss, service=self.service, amount=8000, original_amount=10000,
            promo_discount=2000, provider_income=6400, shop_income=1600,
            commission_rate=20, payment_status=Order.PaymentStatus.PAID,
            remark='内部原价100元，老板实付80元',
        )
        self.url = f'/api/admin/orders/{self.order.pk}/dispatch-message/'
        self.login(self.staff)

    def login(self, user):
        token, _ = issue_account_tokens(get_or_create_account_for_legacy_user(user))
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def post(self, channels=None):
        return self.client.post(self.url, {'channels': channels or ['KOOK', 'WECOM']}, format='json')

    def test_preview_and_card_only_expose_frozen_provider_payout(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.data['data']
        self.assertEqual(data['provider_income'], 6400)
        for text in (data['content'], build_order_card(self.order, 'NEW_ORDER')):
            self.assertIn('640 兴安币（¥64.00）', text)
            for forbidden in ('10000', '8000', '原价', '老板实付', '内部', '抽成', '100.00', '80.00'):
                self.assertNotIn(forbidden, text)
        self.assertNotIn('test-only-key', json.dumps(data))
        self.assertLessEqual(len(data['content'].encode('utf-8')), 2048)

    def test_price_change_after_order_does_not_change_advertised_income(self):
        self.service.price = 99000
        self.service.commission_rate = 99
        self.service.save()
        self.assertIn('¥64.00', build_dispatch_text(self.order))

    def test_dispatch_uses_the_purchased_unit_from_the_order_snapshot(self):
        self.order.gameplay_snapshot = {
            'name': '陪玩', 'platform_name': '', 'security_level_name': '', 'price_unit': '小时',
        }
        self.assertIn('1 小时', build_dispatch_text(self.order))

    def test_rbac_boss_and_readonly_staff_cannot_broadcast(self):
        for user in (self.boss, self.viewer):
            self.login(user)
            self.assertEqual(self.client.get(self.url).status_code, 403)
            self.assertEqual(self.post().status_code, 403)

    @override_settings(KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
    def test_copy_preview_works_without_any_channel_credentials(self):
        data = self.client.get(self.url).data['data']
        self.assertTrue(data['can_send'])
        self.assertTrue(all(not channel['configured'] for channel in data['channels']))
        self.assertEqual(self.post().status_code, 400)
        self.assertFalse(KookDispatchRecord.objects.exists())
        self.assertFalse(WecomDispatchRecord.objects.exists())

    @patch('orders.dispatching.send_task_safely')
    def test_double_click_and_automatic_queue_share_the_same_records(self, send_mock):
        balance = list(Wallet.objects.values_list('pk', 'balance'))
        tx_count = Transaction.objects.count()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.post().status_code, 200)
            self.assertEqual(self.post().status_code, 200)
            queue_dispatch(self.order, 'KOOK')
            queue_dispatch(self.order, 'WECOM')
        self.assertEqual(send_mock.call_count, 2)
        self.assertEqual(KookDispatchRecord.objects.count(), 1)
        self.assertEqual(WecomDispatchRecord.objects.count(), 1)
        self.assertEqual(list(Wallet.objects.values_list('pk', 'balance')), balance)
        self.assertEqual(Transaction.objects.count(), tx_count)

    @patch('orders.dispatching.send_task_safely')
    def test_non_dispatchable_orders_do_not_create_deliveries(self, send_mock):
        changes = [
            {'payment_status': 'UNPAID'}, {'status': 'GRABBED'}, {'status': 'CANCELLED'},
            {'auto_cancel_at': timezone.now() - timedelta(seconds=1)}, {'escort_mode': 'DOUBLE'},
            {'provider': self.boss},
        ]
        for change in changes:
            with self.subTest(change=change):
                old = {key: getattr(self.order, key) for key in change}
                Order.objects.filter(pk=self.order.pk).update(**change)
                self.assertEqual(self.post().status_code, 409)
                Order.objects.filter(pk=self.order.pk).update(**old)
        self.assertFalse(KookDispatchRecord.objects.exists())
        self.assertFalse(WecomDispatchRecord.objects.exists())
        send_mock.assert_not_called()

    @patch('orders.dispatching.send_task_safely')
    def test_rejected_order_gets_a_new_dispatch_sequence(self, send_mock):
        with self.captureOnCommitCallbacks(execute=True):
            self.post()
            Order.objects.filter(pk=self.order.pk).update(reject_count=1)
            self.post()
        self.assertEqual(send_mock.call_count, 4)
        self.assertEqual(WecomDispatchRecord.objects.count(), 2)

    def record(self, channel):
        record = queue_dispatch(self.order, channel)
        return record

    @patch('integrations.wecom.WecomClient.send_text')
    @patch('integrations.kook.KookClient.send_channel_message', return_value=KookMessageResult('msg-1'))
    def test_both_channels_send_same_payout_and_do_not_send_twice(self, kook_mock, wecom_mock):
        for channel in ('KOOK', 'WECOM'):
            record = self.record(channel)
            self.assertTrue(deliver_dispatch(channel, record.pk).startswith('sent:'))
            self.assertTrue(deliver_dispatch(channel, record.pk).startswith('already_sent:'))
            record.refresh_from_db()
            self.assertEqual(record.attempts, 1)
        wecom_mock.assert_called_once_with(build_dispatch_text(self.order))
        self.assertIn('640 兴安币（¥64.00）', kook_mock.call_args.kwargs['content'])
        kook_mock.assert_called_once()

    @patch('integrations.wecom.WecomClient.send_text', side_effect=WecomDeliveryUnknown('timeout'))
    @patch('integrations.kook.KookClient.send_channel_message', side_effect=KookDeliveryUnknown('timeout'))
    def test_uncertain_result_cannot_be_resent_by_click_or_worker(self, kook_mock, wecom_mock):
        for channel in ('KOOK', 'WECOM'):
            record = self.record(channel)
            self.assertEqual(deliver_dispatch(channel, record.pk), 'unknown')
            queue_dispatch(self.order, channel, manual=True)
            self.assertEqual(deliver_dispatch(channel, record.pk), 'skip:delivery=UNKNOWN')
        self.assertEqual(kook_mock.call_count, 1)
        self.assertEqual(wecom_mock.call_count, 1)

    @patch('integrations.wecom.WecomClient.send_text', side_effect=WecomError('rejected'))
    def test_explicit_rejection_can_be_retried_manually(self, send_mock):
        record = self.record('WECOM')
        self.assertEqual(deliver_dispatch('WECOM', record.pk), 'failed:rejected')
        queue_dispatch(self.order, 'WECOM', manual=True)
        send_mock.side_effect = None
        self.assertEqual(deliver_dispatch('WECOM', record.pk), 'sent:')
        self.assertEqual(send_mock.call_count, 2)

    @patch('integrations.wecom.WecomClient.send_text')
    def test_worker_rechecks_order_and_channel_target(self, send_mock):
        record = self.record('WECOM')
        with override_settings(WECOM_DISPATCH_WEBHOOK=TEST_WEBHOOK + '-changed'):
            self.assertEqual(deliver_dispatch('WECOM', record.pk), 'failed:configuration')
        queue_dispatch(self.order, 'WECOM', manual=True)
        Order.objects.filter(pk=self.order.pk).update(payment_status='REFUNDED')
        self.assertEqual(deliver_dispatch('WECOM', record.pk), 'skip:status=PENDING')
        send_mock.assert_not_called()

    @patch('integrations.wecom.WecomClient.send_text')
    def test_worker_skips_old_dispatch_round_after_rejection(self, send_mock):
        record = self.record('WECOM')
        Order.objects.filter(pk=self.order.pk).update(reject_count=1)
        deliver_dispatch('WECOM', record.pk)
        record.refresh_from_db()
        self.assertEqual(record.status, 'SKIPPED')
        send_mock.assert_not_called()

    def test_quote_uses_actual_promotion_without_writing_money(self):
        make_promotion(discount_rate=80)
        tx_count = Transaction.objects.count()
        response = self.client.get('/api/admin/orders/dispatch-quote/', {
            'customer_id': get_or_create_account_for_legacy_user(self.boss).pk,
            'service_id': self.service.pk, 'game_rounds': 2,
        })
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['data']['amount'], 16000)
        self.assertEqual(response.data['data']['promo_discount'], 4000)
        self.assertEqual(Transaction.objects.count(), tx_count)

    @patch('orders.tasks.send_wecom_dispatch.delay', side_effect=RuntimeError('broker down'))
    def test_broker_failure_is_visible_and_retryable(self, delay_mock):
        with self.captureOnCommitCallbacks(execute=True):
            self.post(['WECOM'])
        self.assertEqual(WecomDispatchRecord.objects.get().status, 'FAILED')

    @override_settings(KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
    def test_actual_discounted_dispatch_freezes_64_payout_and_16_club_income(self):
        self.boss.wallet.balance = 20000
        self.boss.wallet.save(update_fields=['balance'])
        make_promotion(discount_rate=80)
        account = get_or_create_account_for_legacy_user(self.boss)
        response = self.client.post('/api/admin/orders/dispatch/', {
            'customer_id': account.pk, 'service_id': self.service.pk,
            'game_rounds': 1, 'client_request_id': 'discount-dispatch-test',
        }, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        self.assertEqual((order.original_amount, order.amount, order.provider_income, order.shop_income), (10000, 8000, 6400, 1600))
        self.assertIn('640 兴安币（¥64.00）', build_dispatch_text(order))
