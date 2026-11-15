"""Lost queue work is recovered without retrying uncertain external sends."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from console.grab_config import save_grab_window
from orders.dispatching import QUEUE_ERROR, deliver_dispatch, queue_dispatch
from orders.grab_group_recovery import LEASE_SECONDS, recover_due
from orders.grab_pool_snapshot import create_initial_snapshot
from orders.grab_team_draft import first_round
from orders.tests.factories import make_order, make_service, make_user
from stores.db import transaction


def setup_order():
    save_grab_window(30, 0, lambda *args: None)
    order = make_order(make_user(), service=make_service(), payment_status='PAID',
        original_amount=10000, amount=8000, required_provider_count=2,
        escort_mode='DOUBLE', pending_timeout_minutes=30)
    with transaction.atomic():
        pool = create_initial_snapshot(order.pk)
        first_round(pool.pk)
        order.pool_entered_at, order.auto_cancel_at = pool.opened_at, pool.total_deadline
        order.save(update_fields=['pool_entered_at', 'auto_cancel_at'])
    return order, pool.opened_at + timedelta(seconds=300)


@override_settings(GRAB_POOL_GROUP_DISPATCH_ENABLED=True, WECOM_DISPATCH_ENABLED=True,
    WECOM_DISPATCH_WEBHOOK='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=isolated-test')
class GrabGroupRecoveryTest(TransactionTestCase):
    def setUp(self):
        self.order, self.due = setup_order()

    @patch('orders.dispatching.send_task_safely')
    @patch('orders.grab_group_recovery.send_task_safely')
    def test_lost_enqueue_is_recovered_after_due_with_bounded_lease(self, recover_send, initial_send):
        record = queue_dispatch(self.order, 'WECOM')
        self.assertEqual(record.pool_delivery_due_at, self.due)
        initial_send.assert_called_once_with('WECOM', record.pk, eta=self.due)
        with patch('django.utils.timezone.now', return_value=self.due - timedelta(seconds=1)):
            self.assertEqual(recover_due(), 0)
        with patch('django.utils.timezone.now', return_value=self.due):
            self.assertEqual(recover_due(), 1)
            self.assertEqual(recover_due(), 0)
        recover_send.assert_called_once_with('WECOM', record.pk)
        record.refresh_from_db()
        self.assertEqual(record.pool_recovery_lease_until, self.due + timedelta(seconds=LEASE_SECONDS))
        with patch('django.utils.timezone.now', return_value=self.due + timedelta(seconds=LEASE_SECONDS)):
            self.assertEqual(recover_due(), 1)
        self.assertEqual(recover_send.call_count, 2)

    @patch('integrations.wecom.WecomClient.send_text')
    def test_enqueue_exception_is_retried_then_delivered_once(self, send):
        with patch('orders.tasks.send_wecom_dispatch.apply_async', side_effect=RuntimeError('broker down')):
            record = queue_dispatch(self.order, 'WECOM')
        record.refresh_from_db()
        self.assertEqual((record.status, record.last_error, record.pool_enqueue_failed), ('FAILED', QUEUE_ERROR, True))
        with patch('django.utils.timezone.now', return_value=self.due), \
                patch('orders.grab_group_recovery.send_task_safely') as recover_send:
            self.assertEqual(recover_due(), 1)
            recover_send.assert_called_once_with('WECOM', record.pk)
            self.assertEqual(deliver_dispatch('WECOM', record.pk), 'sent:')
            self.assertEqual(deliver_dispatch('WECOM', record.pk), 'already_sent:')
            self.assertEqual(recover_due(), 0)
        send.assert_called_once()

    @patch('orders.dispatching.send_task_safely')
    @patch('orders.grab_group_recovery.send_task_safely')
    def test_external_unknown_sending_and_rejected_are_not_retried(self, recover_send, initial_send):
        record = queue_dispatch(self.order, 'WECOM')
        with patch('django.utils.timezone.now', return_value=self.due):
            for status, reason in [('SENDING', ''), ('UNKNOWN', ''), ('SENT', ''),
                                   ('FAILED', '外部渠道明确拒绝'), ('FAILED', QUEUE_ERROR)]:
                type(record).objects.filter(pk=record.pk).update(status=status, last_error=reason,
                                                                 pool_enqueue_failed=False)
                self.assertEqual(recover_due(), 0)
        recover_send.assert_not_called()

    @patch('orders.dispatching.send_task_safely')
    @patch('orders.grab_group_recovery.send_task_safely')
    def test_external_rejection_cannot_impersonate_queue_failure(self, recover_send, initial_send):
        from integrations.wecom import WecomError
        record = queue_dispatch(self.order, 'WECOM')
        with patch('django.utils.timezone.now', return_value=self.due), \
                patch('integrations.wecom.WecomClient.send_text', side_effect=WecomError(QUEUE_ERROR)):
            self.assertEqual(deliver_dispatch('WECOM', record.pk), 'failed:rejected')
            record.refresh_from_db()
            self.assertEqual((record.last_error, record.pool_enqueue_failed), (QUEUE_ERROR, False))
            self.assertEqual(recover_due(), 0)
        recover_send.assert_not_called()

    @patch('orders.dispatching.send_task_safely')
    @patch('orders.grab_group_recovery.send_task_safely')
    @patch('integrations.wecom.WecomClient.send_text')
    def test_recovered_cancelled_order_is_not_sent(self, send, recover_send, initial_send):
        record = queue_dispatch(self.order, 'WECOM')
        type(self.order).objects.filter(pk=self.order.pk).update(status='CANCELLED')
        with patch('django.utils.timezone.now', return_value=self.due):
            self.assertEqual(recover_due(), 1)
            self.assertEqual(deliver_dispatch('WECOM', record.pk), 'skip:status=CANCELLED')
            self.assertEqual(recover_due(), 0)
        send.assert_not_called()

    @patch('orders.dispatching.send_task_safely')
    @patch('orders.grab_group_recovery.send_task_safely')
    @patch('integrations.wecom.WecomClient.send_text')
    def test_recovered_order_at_deadline_is_not_sent(self, send, recover_send, initial_send):
        record = queue_dispatch(self.order, 'WECOM')
        with patch('django.utils.timezone.now', return_value=self.order.auto_cancel_at):
            self.assertEqual(recover_due(), 1)
            self.assertEqual(deliver_dispatch('WECOM', record.pk), 'skip:status=PENDING')
            self.assertEqual(recover_due(), 0)
        record.refresh_from_db()
        self.assertEqual(record.status, 'SKIPPED')
        send.assert_not_called()

    def test_disabled_recovery_does_not_query_or_enqueue(self):
        with override_settings(GRAB_POOL_GROUP_DISPATCH_ENABLED=False):
            self.assertEqual(recover_due(), 0)

    @skipUnlessDBFeature('has_select_for_update')
    @patch('orders.dispatching.send_task_safely')
    @patch('orders.grab_group_recovery.send_task_safely')
    def test_competing_recovery_workers_only_claim_once(self, recovery_send, initial_send):
        record = queue_dispatch(self.order, 'WECOM')
        barrier = Barrier(2)

        def worker():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return recover_due()
            finally:
                close_old_connections()

        with patch('django.utils.timezone.now', return_value=self.due), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            self.assertEqual(sorted(future.result(timeout=20) for future in futures), [0, 1])
        recovery_send.assert_called_once_with('WECOM', record.pk)


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated databases')
@override_settings(GRAB_POOL_GROUP_DISPATCH_ENABLED=True)
class GrabGroupRecoveryStoreTest(TransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    reset_sequences = True

    @patch('orders.dispatching.channel_config', return_value=(True, 'test-target', '测试群'))
    @patch('orders.dispatching.send_task_safely')
    @patch('orders.grab_group_recovery.send_task_safely')
    def test_identical_records_are_recovered_in_their_own_store(self, recovery_send, initial_send, config):
        from stores.context import current_store, resolve_store, store_context
        from stores.tests_hosts import xinan_store
        queued, rows = [], []
        recovery_send.side_effect = lambda channel, pk: queued.append((current_store().slug, channel, pk))
        for store in (resolve_store(), xinan_store()):
            with store_context(store):
                order, due = setup_order()
                record = queue_dispatch(order, 'WECOM')
                rows.append((store, order.pk, record.pk, due))
        self.assertEqual(rows[0][1:3], rows[1][1:3])
        for store, _, _, due in rows:
            with store_context(store), patch('django.utils.timezone.now', return_value=due):
                self.assertEqual(recover_due(), 1)
                self.assertEqual(recover_due(), 0)
        self.assertEqual(queued, [(row[0].slug, 'WECOM', row[2]) for row in rows])
