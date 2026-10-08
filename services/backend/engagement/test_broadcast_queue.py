from datetime import timedelta
from unittest.mock import patch
from django.db import transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from orders.tests.factories import make_user
from .models import Broadcast
from .services import broadcast
from .tasks import publish_broadcast, drain_broadcast_outbox, enqueue_broadcast


@override_settings(CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class BroadcastQueueTests(APITestCase):
    def setUp(self):
        self.user = make_user()
        self.account = get_or_create_account_for_legacy_user(self.user)
        link_legacy_relations(self.user, self.account)
        self.client.force_authenticate(self.user)

    def test_only_committed_events_enqueue_and_worker_publishes_once(self):
        with patch('engagement.tasks.enqueue_broadcast') as enqueue, self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                row = broadcast('GIFT', 'gift-test:1', account=self.account, provider='陪玩', reward='礼物')
                self.assertFalse(enqueue.called)
        enqueue.assert_called_once_with(row.pk)
        self.assertEqual(self.client.get('/api/club/feed/').data['data'], [])
        self.assertTrue(publish_broadcast.run(row.pk))
        self.assertFalse(publish_broadcast.run(row.pk))
        feed = self.client.get('/api/club/feed/?page=1&kind=GIFT').data['data']
        self.assertEqual(feed['items'][0]['id'], row.pk)
        self.assertNotIn('account', feed['items'][0])

    def test_rollback_has_no_event_and_broker_failure_can_retry(self):
        with self.captureOnCommitCallbacks(execute=True), patch('engagement.tasks.enqueue_broadcast') as enqueue:
            try:
                with transaction.atomic():
                    broadcast('REVIEW', 'rollback:1', account=self.account)
                    raise RuntimeError('rollback')
            except RuntimeError:
                pass
        enqueue.assert_not_called()
        self.assertFalse(Broadcast.objects.exists())
        row = broadcast('REVIEW', 'retry:1', account=self.account)
        with patch('engagement.tasks.publish_broadcast.apply_async', side_effect=ConnectionError('offline')):
            enqueue_broadcast(row.pk)
        row.refresh_from_db(); self.assertIsNone(row.published_at)
        with patch('engagement.tasks.enqueue_broadcast') as retry:
            self.assertEqual(drain_broadcast_outbox.run(), 1)
            retry.assert_called_once_with(row.pk)

    def test_cursor_filter_and_hidden_or_expired_events(self):
        now = timezone.now()
        for i in range(34):
            Broadcast.objects.create(event_key=f'page:{i}', kind='LOTTERY_TAG', content=str(i),
                published_at=now, starts_at=now, expires_at=now + timedelta(days=1))
        first = self.client.get('/api/club/feed/?page=1&kind=LOTTERY').data['data']
        self.assertEqual(len(first['items']), 30)
        second = self.client.get(f"/api/club/feed/?before={first['next']}&kind=LOTTERY").data['data']
        self.assertEqual(len(second['items']), 4)
        self.assertFalse(set(row['id'] for row in first['items']) & set(row['id'] for row in second['items']))
        Broadcast.objects.update(is_visible=False)
        self.assertEqual(self.client.get('/api/club/feed/').data['data'], [])

    def test_delivery_failure_retries_readable_event_without_duplicate_feed_rows(self):
        row = broadcast('GIFT', 'delivery-retry:1', account=self.account, provider='陪玩', reward='礼物')
        with patch('channels.layers.get_channel_layer') as get_layer:
            async def fail(*args):
                raise ConnectionError('channel unavailable')
            get_layer.return_value.group_send = fail
            with self.assertRaises(ConnectionError):
                publish_broadcast.run(row.pk)
        row.refresh_from_db()
        self.assertIsNotNone(row.published_at)
        self.assertIsNone(row.notified_at)
        self.assertTrue(publish_broadcast.run(row.pk))
        self.assertEqual(len(self.client.get('/api/club/feed/').data['data']), 1)
