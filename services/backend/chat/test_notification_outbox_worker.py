"""Opt-in Celery integration using only a namespaced queue on registered Redis."""
import os
import time
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from chat.models import ChatMessage, ChatNotificationOutbox
from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens
from stores.context import resolve_store, store_context


@skipUnless(os.environ.get('XA_CHAT_OUTBOX_WORKER_TEST') == '1', 'requires isolated file database and registered Redis')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, CHAT_NOTIFICATION_OUTBOX_ENABLED=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class ChatOutboxWorkerTest(TransactionTestCase):
    def test_beat_dispatches_store_scan_to_real_worker(self):
        import redis
        from celery import Celery, current_app
        from celery.beat import ScheduleEntry, Scheduler
        from celery.contrib.testing.worker import start_worker
        from celery.signals import before_task_publish
        from kombu import Exchange, Queue

        configured = os.environ.get('XA_ASGI_NETWORK_TEST_DB', '')
        target = Path(configured).resolve()
        self.assertTrue(configured and target.parent == (Path(settings.BASE_DIR).parent.parent / '.tools').resolve()
                        and target.name.startswith('asgi-reservation-') and target.suffix == '.sqlite3')
        self.assertEqual(connection.vendor, 'sqlite')
        self.assertEqual(Path(connection.settings_dict['NAME']).resolve(), target)
        redis_client = redis.Redis(host='127.0.0.1', port=26379, db=1, socket_connect_timeout=2, socket_timeout=2)
        self.assertTrue(redis_client.ping())
        self.addCleanup(redis_client.close)
        unique = uuid4().hex
        prefix = f'xa_chat_worker_test_{unique}:'
        queue = f'xa_chat_worker_test_{unique}'
        store = resolve_store('shenglang')
        with store_context(store):
            boss = ClubAccount.objects.create_account(username=f'worker-test-{unique}', account_type='BOSS')
            client = APIClient(HTTP_HOST='sl.esports.club')
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss)[0])
            with patch('chat.notifier.get_channel_layer', return_value=None):
                response = client.post('/api/chat/send/', {'content': '隔离重试', 'client_message_id': 'worker-test-001'}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            row = ChatNotificationOutbox.objects.get()
            self.assertEqual(row.last_error, 'CHANNEL_UNAVAILABLE')
            ChatNotificationOutbox.objects.filter(pk=row.pk).update(next_attempt_at=timezone.now()-timedelta(seconds=1))

        previous_app = current_app._get_current_object()
        app = Celery(f'xa_chat_test_{unique}', task_cls='stores.tasks:StoreTask', set_as_current=False)
        app.conf.update(broker_url='redis://127.0.0.1:26379/1', result_backend=None,
            broker_transport_options={'global_keyprefix': prefix}, task_default_queue=queue,
            task_default_exchange=queue, task_default_routing_key=queue,
            task_queues=(Queue(queue, Exchange(queue, type='direct'), routing_key=queue),),
            task_ignore_result=True, worker_enable_remote_control=False, worker_send_task_events=False,
            worker_hijack_root_logger=False, broker_connection_retry_on_startup=False)
        app.set_current()
        published = []
        def capture(sender=None, headers=None, **kwargs):
            if sender == 'chat.tasks.drain_chat_notifications':
                published.append((headers or {}).get('store_slug'))
        before_task_publish.connect(capture, weak=False)
        try:
            import chat.tasks  # noqa: F401 -- register shared task on the isolated app
            app.finalize()
            self.assertIn('chat.tasks.drain_chat_notifications', app.tasks)
            with start_worker(app, pool='solo', queues=[queue], perform_ping_check=False,
                              without_heartbeat=True, shutdown_timeout=15):
                entry = ScheduleEntry(name='isolated-chat-scan', task='chat.tasks.drain_chat_notifications',
                                      schedule=30.0, last_run_at=app.now()-timedelta(seconds=31), app=app)
                scheduler = Scheduler(app=app, schedule={entry.name: entry}, lazy=True)
                self.assertEqual(scheduler.tick(), 0)
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    row.refresh_from_db()
                    if row.completed_at:
                        break
                    time.sleep(.1)
                self.assertIsNotNone(row.completed_at, 'periodic worker did not retry the stored notification')
                self.assertEqual(row.attempts, 2)
                self.assertEqual(ChatMessage.objects.count(), 1)
                self.assertIn(None, published)
                self.assertIn('shenglang', published)
        finally:
            before_task_publish.disconnect(capture)
            app.close()
            previous_app.set_current()
            # Exact task-owned broker keys only; never flush or inspect other queues.
            redis_client.delete(prefix + queue, prefix + '_kombu.binding.' + queue)
