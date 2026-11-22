"""Group delivery uses the order's pool facts; all external sends are mocked."""
import os
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from unittest import skipUnless

from console.grab_config import save_grab_window
from orders.dispatching import deliver_dispatch, dispatch_preview, queue_dispatch, reschedule_pool_dispatch
from orders.grab_pool_snapshot import create_initial_snapshot
from orders.grab_team_draft import first_round
from orders.models import Order, OrderGrabRound, OrderGrabTeamRoundState
from orders.tests.factories import configure_isolated_wecom, make_order, make_service, make_user
from stores.context import resolve_store, store_context
from stores.db import transaction


class GrabGroupTaskEnvelopeTest(SimpleTestCase):
    def test_recovery_is_registered_for_each_store(self):
        from stores.tasks import PERIODIC_STORE_TASKS
        name = 'orders.tasks.recover_pool_group_dispatch'
        self.assertIn(name, PERIODIC_STORE_TASKS)
        self.assertEqual(settings.CELERY_BEAT_SCHEDULE['recover-pool-group-dispatch']['task'], name)

    def test_delayed_task_preserves_eta_and_store_header_for_both_channels(self):
        from orders.dispatching import send_task_safely
        from stores.context import store_context
        due = timezone.now() + timedelta(minutes=5)
        with store_context(SimpleNamespace(slug='xinan')), patch('celery.Task.apply_async') as publish:
            for channel in ('KOOK', 'WECOM'):
                send_task_safely(channel, 42, eta=due)
                kwargs = publish.call_args.kwargs
                self.assertEqual(kwargs['args'], [42])
                self.assertEqual(kwargs['eta'], due)
                self.assertEqual(kwargs['headers']['store_slug'], 'xinan')


@override_settings(GRAB_POOL_GROUP_DISPATCH_ENABLED=True)
class GrabGroupDispatchTest(TestCase):
    def setUp(self):
        self.enterContext(store_context(resolve_store()))
        configure_isolated_wecom()
        save_grab_window(30, 0, lambda *args: None)
        self.order = make_order(make_user(), service=make_service(), payment_status='PAID',
            original_amount=10000, amount=8000, required_provider_count=2,
            escort_mode='DOUBLE', dispatch_route='STAFF', pending_timeout_minutes=30)
        with transaction.atomic():
            self.pool = create_initial_snapshot(self.order.pk)
            self.round = first_round(self.pool.pk)
            OrderGrabTeamRoundState.objects.create(round=self.round)
            self.order.pool_entered_at = self.pool.opened_at
            self.order.auto_cancel_at = self.pool.total_deadline
            self.order.save(update_fields=['pool_entered_at', 'auto_cancel_at'])
        self.due = self.pool.opened_at + timedelta(seconds=300)

    @patch('orders.dispatching.send_task_safely')
    @patch('integrations.wecom.WecomClient.send_text')
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_delayed_queue_early_worker_and_due_delivery_are_idempotent(self, send, schedule):
        self.assertEqual(dispatch_preview(self.order)['earliest_delivery_at'], self.due)
        with self.captureOnCommitCallbacks(execute=True):
            record = queue_dispatch(self.order, 'WECOM')
            self.assertEqual(queue_dispatch(self.order, 'WECOM').pk, record.pk)
        schedule.assert_called_once_with('WECOM', record.pk, eta=self.due)
        self.assertIn('双陪订单，须两名合资格陪玩组队接单', record.content)
        self.assertEqual(record.content_contract_version, 2)
        schedule.reset_mock()
        with patch('django.utils.timezone.now', return_value=self.due - timedelta(seconds=1)):
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(deliver_dispatch('WECOM', record.pk), 'deferred')
        schedule.assert_called_once_with('WECOM', record.pk, eta=self.due)
        send.assert_not_called()
        record.refresh_from_db()
        self.assertEqual((record.status, record.attempts), ('PENDING', 0))
        with patch('django.utils.timezone.now', return_value=self.due):
            self.assertEqual(deliver_dispatch('WECOM', record.pk), 'sent:')
            self.assertEqual(deliver_dispatch('WECOM', record.pk), 'already_sent:')
        send.assert_called_once_with(record.content)

    @patch('integrations.wecom.WecomClient.send_text')
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_cancel_or_assignment_before_delayed_worker_never_sends(self, send):
        for status in ('CANCELLED', 'GRABBED'):
            with self.subTest(status=status), transaction.atomic():
                record = queue_dispatch(self.order, 'WECOM')
                Order.objects.filter(pk=self.order.pk).update(status=status)
                with patch('django.utils.timezone.now', return_value=self.due):
                    self.assertEqual(deliver_dispatch('WECOM', record.pk), f'skip:status={status}')
                send.assert_not_called()
                transaction.set_rollback(True)

    @patch('orders.dispatching.send_task_safely')
    @patch('integrations.wecom.WecomClient.send_text')
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_reopened_round_is_simultaneous_and_keeps_original_window(self, send, schedule):
        record = queue_dispatch(self.order, 'WECOM')
        type(record).objects.filter(pk=record.pk).update(pool_recovery_lease_until=self.due)
        opened = self.round.deadline
        with patch('django.utils.timezone.now', return_value=opened):
            reopened = OrderGrabRound.objects.create(pool=self.pool, number=2, opened_at=opened,
                deadline=opened + timedelta(seconds=30), total_deadline=self.pool.total_deadline,
                simultaneous_visibility=True)
            OrderGrabTeamRoundState.objects.create(round=reopened)
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(reschedule_pool_dispatch(self.order.pk), 1)
            schedule.assert_called_with('WECOM', record.pk, eta=opened)
            record.refresh_from_db()
            self.assertEqual((record.pool_delivery_due_at, record.pool_recovery_lease_until), (opened, None))
            self.assertEqual(dispatch_preview(self.order)['earliest_delivery_at'], opened)
            self.assertEqual(deliver_dispatch('WECOM', record.pk), 'sent:')
        send.assert_called_once()

    @patch('integrations.wecom.WecomClient.send_text')
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_invalid_pool_or_switch_change_never_sends(self, send):
        record = queue_dispatch(self.order, 'WECOM')
        with override_settings(GRAB_POOL_GROUP_DISPATCH_ENABLED=False):
            self.assertTrue(deliver_dispatch('WECOM', record.pk).startswith('skip:'))
        record.refresh_from_db()
        self.assertEqual(record.status, 'SKIPPED')
        send.assert_not_called()
        with transaction.atomic():
            self.round.simultaneous_visibility = True
            self.round.save(update_fields=['simultaneous_visibility'])
            self.assertIn('首轮抢单快照不一致', dispatch_preview(self.order)['reason'])
            self.assertIsNone(queue_dispatch(self.order, 'WECOM'))
            transaction.set_rollback(True)

    @patch('integrations.wecom.WecomClient.send_text')
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_corrupt_frozen_window_is_rejected_before_group_delivery(self, send):
        record = queue_dispatch(self.order, 'WECOM')
        self.pool.window_seconds = 29
        self.pool.save(update_fields=['window_seconds'])
        self.assertIn('抢单池窗口快照不一致', dispatch_preview(self.order)['reason'])
        with patch('django.utils.timezone.now', return_value=self.due):
            self.assertTrue(deliver_dispatch('WECOM', record.pk).startswith('skip:'))
        send.assert_not_called()

    @override_settings(KOOK_ENABLED=True, KOOK_BOT_TOKEN='test-token',
                       KOOK_DISPATCH_CHANNEL_ID='test-channel')
    @patch('integrations.kook.KookClient.send_channel_message')
    def test_kook_receives_safe_double_card_after_same_deadline(self, send):
        from integrations.kook import KookMessageResult
        send.return_value = KookMessageResult('test-message')
        record = queue_dispatch(self.order, 'KOOK')
        self.assertIsNotNone(record)
        with patch('django.utils.timezone.now', return_value=self.due):
            self.assertEqual(deliver_dispatch('KOOK', record.pk), 'sent:test-message')
        content = send.call_args.kwargs['content']
        self.assertIn('双陪订单，须两名合资格陪玩组队接单', content)
        self.assertIn('服务原价：1000', content)
        self.assertNotIn('陪玩收益', content)

    @override_settings(KOOK_ENABLED=True, KOOK_BOT_TOKEN='test-token',
                       KOOK_DISPATCH_CHANNEL_ID='test-channel')
    @patch('integrations.kook.KookClient.send_channel_message')
    def test_current_round_missing_state_holds_existing_message_until_recovered(self, send):
        from integrations.kook import KookMessageResult

        send.return_value = KookMessageResult('test-message')
        record = queue_dispatch(self.order, 'KOOK')
        opened = self.round.deadline
        reopened = OrderGrabRound.objects.create(pool=self.pool, number=2, opened_at=opened,
            deadline=opened + timedelta(seconds=30), total_deadline=self.pool.total_deadline,
            simultaneous_visibility=True)
        with patch('django.utils.timezone.now', return_value=opened):
            self.assertFalse(dispatch_preview(self.order)['can_send'])
            self.assertIsNone(queue_dispatch(self.order, 'KOOK'))
            self.assertEqual(reschedule_pool_dispatch(self.order.pk), 0)
            self.assertEqual(deliver_dispatch('KOOK', record.pk), 'waiting:round_review')
            record.refresh_from_db()
            self.assertEqual((record.status, record.attempts, record.pool_delivery_due_at),
                             ('PENDING', 0, self.due))
            send.assert_not_called()
            OrderGrabTeamRoundState.objects.create(round=reopened)
            self.assertEqual(reschedule_pool_dispatch(self.order.pk), 1)
            self.assertEqual(deliver_dispatch('KOOK', record.pk), 'sent:test-message')
        send.assert_called_once()


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated databases')
@override_settings(GRAB_POOL_GROUP_DISPATCH_ENABLED=True)
class GrabGroupDispatchStoreTest(TransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    reset_sequences = True

    @patch('orders.dispatching.send_task_safely')
    @patch('integrations.wecom.WecomClient')
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/绑定排名/团队抢单/分组派单/入场快照属正式契约深水区（18-盘点 抢单池/陪玩生命周期待联调）；最终联调启用')
    def test_equal_ids_keep_store_timing_context_and_currency(self, client, schedule):
        from stores.context import current_store
        from stores.tests_hosts import xinan_store
        from wallet.models import SystemConfig
        scheduled = []
        schedule.side_effect = lambda channel, pk, eta: scheduled.append((current_store().slug, channel, pk, eta))
        rows = []
        for store, delay in ((resolve_store(), 300), (xinan_store(), 600)):
            with store_context(store):
                configure_isolated_wecom(store.slug)
                save_grab_window(30, 0, lambda *args: None)
                SystemConfig.objects.update_or_create(key='provider_pass_delay_seconds_NONE',
                                                       defaults={'value': str(delay)})
                order = make_order(make_user(), service=make_service(), payment_status='PAID',
                    original_amount=10000, amount=8000, required_provider_count=2,
                    escort_mode='DOUBLE', pending_timeout_minutes=30)
                with transaction.atomic():
                    pool = create_initial_snapshot(order.pk)
                    OrderGrabTeamRoundState.objects.create(round=first_round(pool.pk))
                    order.pool_entered_at, order.auto_cancel_at = pool.opened_at, pool.total_deadline
                    order.save(update_fields=['pool_entered_at', 'auto_cancel_at'])
                record = queue_dispatch(order, 'WECOM')
                rows.append((store, order.pk, record.pk, pool.opened_at + timedelta(seconds=delay)))
        self.assertEqual(rows[0][1:3], rows[1][1:3])
        self.assertEqual([(x[0], x[2], x[3]) for x in scheduled],
            [(row[0].slug, row[2], row[3]) for row in rows])
        for row, currency in zip(rows, ('浪花', '星辉')):
            store, _, record_id, due = row
            with store_context(store), patch('django.utils.timezone.now', return_value=due):
                self.assertEqual(deliver_dispatch('WECOM', record_id), 'sent:')
                self.assertIn(currency, client.return_value.send_text.call_args.args[0])
        self.assertEqual(client.return_value.send_text.call_count, 2)
