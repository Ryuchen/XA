import os
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from unittest import skipUnless
from threading import Event
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from django.db import close_old_connections
from django.conf import settings
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
from orders.tests.factories import make_console_user, make_order, make_promotion, make_service, make_user, make_wallet
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


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated databases')
class GroupDispatchStoreIsolationTest(TransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    reset_sequences = True

    @patch('orders.dispatching.send_task_safely')
    @patch('orders.dispatching.channel_config', return_value=(True, 'test-target', '测试群'))
    def test_same_order_id_keeps_each_stores_price_and_currency(self, config, send):
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        identifiers = []
        for store, amount, expected in ((resolve_store(), 10000, '1000 浪花'),
                                        (xinan_store(), 25000, '2500 星辉')):
            with store_context(store):
                order = make_order(make_user(), amount=8000, original_amount=amount,
                    provider_income=6400, payment_status='PAID')
                record = queue_dispatch(order, 'WECOM')
                identifiers.append((order.pk, record.pk))
                self.assertIn(expected, record.content)
                self.assertNotIn('640', record.content)
        self.assertEqual(identifiers[0], identifiers[1])
        self.assertEqual(send.call_count, 2)

    @patch('orders.dispatching.send_task_safely')
    @patch('orders.dispatching.channel_config', return_value=(True, 'test-target', '测试群'))
    def test_audition_gate_does_not_affect_other_store_same_order_id(self, config, send):
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        identifiers = []
        for store, audition in ((resolve_store(), True), (xinan_store(), False)):
            with store_context(store):
                order = make_order(make_user(), amount=10000, original_amount=10000,
                    provider_income=8000, payment_status='PAID', requires_boss_selection=audition)
                identifiers.append(order.pk)
                record = queue_dispatch(order, 'WECOM')
                if audition:
                    self.assertIsNone(record)
                    self.assertFalse(WecomDispatchRecord.objects.filter(order=order).exists())
                else:
                    self.assertEqual(record.content_contract_version, 1)
        self.assertEqual(identifiers[0], identifiers[1])
        send.assert_called_once()


class WecomClientTest(SimpleTestCase):
    @patch('integrations.wecom.urlopen')
    def test_official_text_contract_and_no_mentions(self, mock_open):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"errcode":0,"errmsg":"ok"}'
        mock_open.return_value = response
        WecomClient(TEST_WEBHOOK).send_text('打手单钱：640 浪花（¥64.00）')
        req = mock_open.call_args.args[0]
        self.assertEqual(req.full_url, TEST_WEBHOOK)
        self.assertEqual(json.loads(req.data), {
            'msgtype': 'text', 'text': {'content': '打手单钱：640 浪花（¥64.00）'},
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
        from engagement.models import ClubOperationConfig
        ClubOperationConfig.objects.get_or_create(pk=1)
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
        channels = channels or ['KOOK', 'WECOM']
        response = self.client.get(self.url)
        keys = {item['channel']: item['confirmation_key'] for item in response.data['data']['channels'] if item['channel'] in channels} if response.status_code == 200 else {}
        return self.client.post(self.url, {'channels': channels, 'confirmation_keys': keys}, format='json')

    def test_preview_and_card_only_expose_order_original_price(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.data['data']
        self.assertEqual(data['provider_income'], 6400)
        for text in (data['content'], build_order_card(self.order, 'NEW_ORDER')):
            self.assertIn('服务原价：1000 浪花', text)
            for forbidden in ('8000', '640', '老板实付', '内部', '抽成', '64.00', '80.00'):
                self.assertNotIn(forbidden, text)
        self.assertNotIn('test-only-key', json.dumps(data))
        self.assertLessEqual(len(data['content'].encode('utf-8')), 2048)

    def test_price_change_after_order_does_not_change_advertised_original_price(self):
        self.service.price = 99000
        self.service.commission_rate = 99
        self.service.save()
        self.assertIn('服务原价：1000 浪花', build_dispatch_text(self.order))
        self.assertNotIn('打手单钱', build_dispatch_text(self.order))
        self.assertNotIn('报单审核通过后结算', build_dispatch_text(self.order))

    def test_dispatch_uses_the_purchased_unit_from_the_order_snapshot(self):
        self.order.gameplay_snapshot = {
            'name': '陪玩', 'platform_name': '', 'security_level_name': '', 'price_unit': '小时',
        }
        self.assertIn('1 小时', build_dispatch_text(self.order))

    def test_double_group_content_includes_team_requirement_even_for_custom_template(self):
        from orders.dispatch_templates import render_template
        # Paid team size is authoritative even when the legacy display mode
        # still says SINGLE. This does not open the separate delivery gate.
        self.order.required_provider_count = 2
        notice = '双陪订单，须两名合资格陪玩组队接单'
        texts = (
            build_dispatch_text(self.order),
            render_template(self.order, 'NEW_ORDER', '订单 {order_no} 原价 {original_amount} {currency_name}'),
            build_order_card(self.order, 'NEW_ORDER'),
        )
        for text in texts:
            self.assertIn(notice, text)
            self.assertIn('1000', text)
            self.assertNotIn('640', text)
            self.assertNotIn('单陪', text)
        self.order.required_provider_count = 1
        self.assertNotIn(notice, build_dispatch_text(self.order))
        self.assertNotIn(notice, render_template(self.order, 'NEW_ORDER'))

    def test_store_currency_and_legacy_template_use_safe_original_price(self):
        from stores.context import store_context
        from orders.dispatch_templates import render_template, template_error
        with store_context(SimpleNamespace(name='兴安电竞', currency_name='星辉', database_alias='default', slug='xinan')):
            text = render_template(self.order, 'NEW_ORDER', '打手收入 {provider_income} {order_no}')
            self.assertIn('服务原价：1000 星辉', text)
            self.assertNotIn('640', text)
            self.assertIn('兴安电竞', text)
        self.assertTrue(template_error('{order_no} {provider_income}'))

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '游戏抽成付款/分组派单消息确认/游戏账号字段/抽成草稿/客服代下抢单池口径属正式契约深水区（18-盘点 游戏抽成/派单/企微KOOK待联调）；最终联调启用')
    def test_order_without_original_price_cannot_send(self):
        record = queue_dispatch(self.order, 'WECOM')
        self.order.original_amount = 0
        self.order.save(update_fields=['original_amount'])
        data = self.client.get(self.url).data['data']
        self.assertFalse(data['can_send'])
        self.assertIn('原价快照缺失', data['reason'])
        self.assertIn('服务原价：待核对', data['content'])
        self.assertIsNone(queue_dispatch(self.order, 'WECOM'))
        with patch('integrations.wecom.WecomClient.send_text') as send:
            self.assertEqual(deliver_dispatch('WECOM', record.pk), 'failed:missing_original_price')
            send.assert_not_called()
        record.refresh_from_db()
        self.assertEqual(record.status, 'FAILED')

    @patch('orders.dispatching.send_task_safely')
    @patch('orders.views._schedule_auto_cancel')
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '游戏抽成付款/分组派单消息确认/游戏账号字段/抽成草稿/客服代下抢单池口径属正式契约深水区（18-盘点 游戏抽成/派单/企微KOOK待联调）；最终联调启用')
    def test_paid_boss_selection_order_never_enters_normal_broadcast(self, schedule, send):
        make_wallet(self.boss, balance=100000)
        self.login(self.boss)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post('/api/orders/create/', {
                'service_id': self.service.pk, 'dispatch_route': 'PUBLIC',
                'dispatch_mode': 'BOSS_SELECT', 'client_request_id': 'audition-not-public-grab',
            }, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        self.assertTrue(order.requires_boss_selection)
        self.assertEqual(order.status, 'PENDING')
        schedule.assert_called_once()
        send.assert_not_called()
        self.assertFalse(KookDispatchRecord.objects.filter(order=order).exists())
        self.assertFalse(WecomDispatchRecord.objects.filter(order=order).exists())
        self.login(self.staff)
        self.url = f'/api/admin/orders/{order.pk}/dispatch-message/'
        preview = self.client.get(self.url).data['data']
        self.assertFalse(preview['can_send'])
        self.assertIn('老板选人', preview['reason'])
        self.assertIsNone(queue_dispatch(order, 'KOOK'))
        self.assertIsNone(queue_dispatch(order, 'WECOM'))

    @patch('integrations.wecom.WecomClient.send_text')
    @patch('integrations.kook.KookClient.send_channel_message')
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '游戏抽成付款/分组派单消息确认/游戏账号字段/抽成草稿/客服代下抢单池口径属正式契约深水区（18-盘点 游戏抽成/派单/企微KOOK待联调）；最终联调启用')
    def test_queued_dispatch_is_skipped_if_order_becomes_boss_selection(self, kook, wecom):
        records = [queue_dispatch(self.order, channel) for channel in ('KOOK', 'WECOM')]
        Order.objects.filter(pk=self.order.pk).update(requires_boss_selection=True)
        for channel, record in zip(('KOOK', 'WECOM'), records):
            self.assertTrue(deliver_dispatch(channel, record.pk).startswith('skip:'))
            record.refresh_from_db()
            self.assertEqual(record.status, 'SKIPPED')
            self.assertIn('老板选人', record.last_error)
        kook.assert_not_called()
        wecom.assert_not_called()

    @patch('integrations.wecom.WecomClient.send_text')
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '游戏抽成付款/分组派单消息确认/游戏账号字段/抽成草稿/客服代下抢单池口径属正式契约深水区（18-盘点 游戏抽成/派单/企微KOOK待联调）；最终联调启用')
    def test_legacy_pending_content_requires_preview_and_manual_retry(self, send_mock):
        record = WecomDispatchRecord.objects.create(order=self.order, trigger='NEW_ORDER',
            channel_id=channel_config('WECOM')[1], content='旧的打手收益文案')
        self.assertEqual(deliver_dispatch('WECOM', record.pk), 'failed:outdated_content')
        send_mock.assert_not_called()
        record.refresh_from_db()
        self.assertEqual(record.status, 'FAILED')
        queue_dispatch(self.order, 'WECOM', manual=True)
        record.refresh_from_db()
        self.assertNotIn('打手收益', record.content)
        self.assertTrue(deliver_dispatch('WECOM', record.pk).startswith('sent:'))
        send_mock.assert_called_once_with(record.content)

    @patch('integrations.kook.KookClient.send_channel_message')
    def test_legacy_kook_card_is_not_sent(self, send_mock):
        record = KookDispatchRecord.objects.create(order=self.order, trigger='NEW_ORDER',
            channel_id='test-channel', content='旧的打手收益文案', card_content='旧卡片')
        channel = next(c for c in self.client.get(self.url).data['data']['channels'] if c['channel'] == 'KOOK')
        self.assertEqual((channel['content_source'], channel['content'], channel['card_details']), ('unavailable', '', None))
        self.assertEqual(deliver_dispatch('KOOK', record.pk), 'failed:outdated_content')
        record.refresh_from_db()
        self.assertEqual(record.status, 'FAILED')
        send_mock.assert_not_called()

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
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '游戏抽成付款/分组派单消息确认/游戏账号字段/抽成草稿/客服代下抢单池口径属正式契约深水区（18-盘点 游戏抽成/派单/企微KOOK待联调）；最终联调启用')
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
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '游戏抽成付款/分组派单消息确认/游戏账号字段/抽成草稿/客服代下抢单池口径属正式契约深水区（18-盘点 游戏抽成/派单/企微KOOK待联调）；最终联调启用')
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
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '游戏抽成付款/分组派单消息确认/游戏账号字段/抽成草稿/客服代下抢单池口径属正式契约深水区（18-盘点 游戏抽成/派单/企微KOOK待联调）；最终联调启用')
    def test_both_channels_send_same_payout_and_do_not_send_twice(self, kook_mock, wecom_mock):
        for channel in ('KOOK', 'WECOM'):
            record = self.record(channel)
            self.assertTrue(deliver_dispatch(channel, record.pk).startswith('sent:'))
            self.assertTrue(deliver_dispatch(channel, record.pk).startswith('already_sent:'))
            record.refresh_from_db()
            self.assertEqual(record.attempts, 1)
        wecom_mock.assert_called_once_with(build_dispatch_text(self.order))
        self.assertIn('服务原价：1000 浪花', kook_mock.call_args.kwargs['content'])
        kook_mock.assert_called_once()

    @patch('integrations.wecom.WecomClient.send_text')
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '游戏抽成付款/分组派单消息确认/游戏账号字段/抽成草稿/客服代下抢单池口径属正式契约深水区（18-盘点 游戏抽成/派单/企微KOOK待联调）；最终联调启用')
    def test_wecom_uses_queued_text_when_order_changes_before_delivery(self, send_mock):
        record = self.record('WECOM')
        self.assertIsNotNone(record)
        queued_text = record.content
        Order.objects.filter(pk=self.order.pk).update(game_region='验收后修改的区服')
        self.assertTrue(deliver_dispatch('WECOM', record.pk).startswith('sent:'))
        send_mock.assert_called_once_with(queued_text)
        self.assertNotIn('验收后修改的区服', queued_text)

    @patch('integrations.wecom.WecomClient.send_text', side_effect=WecomDeliveryUnknown('timeout'))
    @patch('integrations.kook.KookClient.send_channel_message', side_effect=KookDeliveryUnknown('timeout'))
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '游戏抽成付款/分组派单消息确认/游戏账号字段/抽成草稿/客服代下抢单池口径属正式契约深水区（18-盘点 游戏抽成/派单/企微KOOK待联调）；最终联调启用')
    def test_uncertain_result_cannot_be_resent_by_click_or_worker(self, kook_mock, wecom_mock):
        for channel in ('KOOK', 'WECOM'):
            record = self.record(channel)
            self.assertEqual(deliver_dispatch(channel, record.pk), 'unknown')
            queue_dispatch(self.order, channel, manual=True)
            self.assertEqual(deliver_dispatch(channel, record.pk), 'skip:delivery=UNKNOWN')
        self.assertEqual(kook_mock.call_count, 1)
        self.assertEqual(wecom_mock.call_count, 1)

    @patch('integrations.wecom.WecomClient.send_text', side_effect=WecomError('rejected'))
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '游戏抽成付款/分组派单消息确认/游戏账号字段/抽成草稿/客服代下抢单池口径属正式契约深水区（18-盘点 游戏抽成/派单/企微KOOK待联调）；最终联调启用')
    def test_explicit_rejection_can_be_retried_manually(self, send_mock):
        record = self.record('WECOM')
        self.assertEqual(deliver_dispatch('WECOM', record.pk), 'failed:rejected')
        queue_dispatch(self.order, 'WECOM', manual=True)
        send_mock.side_effect = None
        self.assertEqual(deliver_dispatch('WECOM', record.pk), 'sent:')
        self.assertEqual(send_mock.call_count, 2)

    @patch('integrations.wecom.WecomClient.send_text')
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '游戏抽成付款/分组派单消息确认/游戏账号字段/抽成草稿/客服代下抢单池口径属正式契约深水区（18-盘点 游戏抽成/派单/企微KOOK待联调）；最终联调启用')
    def test_worker_rechecks_order_and_channel_target(self, send_mock):
        record = self.record('WECOM')
        with override_settings(WECOM_DISPATCH_WEBHOOK=TEST_WEBHOOK + '-changed'):
            self.assertEqual(deliver_dispatch('WECOM', record.pk), 'failed:configuration')
        queue_dispatch(self.order, 'WECOM', manual=True)
        Order.objects.filter(pk=self.order.pk).update(payment_status='REFUNDED')
        self.assertEqual(deliver_dispatch('WECOM', record.pk), 'skip:status=PENDING')
        send_mock.assert_not_called()

    @patch('integrations.wecom.WecomClient.send_text')
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '游戏抽成付款/分组派单消息确认/游戏账号字段/抽成草稿/客服代下抢单池口径属正式契约深水区（18-盘点 游戏抽成/派单/企微KOOK待联调）；最终联调启用')
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
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '游戏抽成付款/分组派单消息确认/游戏账号字段/抽成草稿/客服代下抢单池口径属正式契约深水区（18-盘点 游戏抽成/派单/企微KOOK待联调）；最终联调启用')
    def test_broker_failure_is_visible_and_retryable(self, delay_mock):
        with self.captureOnCommitCallbacks(execute=True):
            self.post(['WECOM'])
        self.assertEqual(WecomDispatchRecord.objects.get().status, 'FAILED')

    @override_settings(KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '游戏抽成付款/分组派单消息确认/游戏账号字段/抽成草稿/客服代下抢单池口径属正式契约深水区（18-盘点 游戏抽成/派单/企微KOOK待联调）；最终联调启用')
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
        self.assertIn('服务原价：1000 浪花', build_dispatch_text(order))
