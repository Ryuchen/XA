"""Optional integration: run the report workflow through a real Redis channel layer.

Set XA_REPORT_CLOCK_REDIS_URL to an isolated, disposable Redis instance.  The
inherited scenarios use real API operations, transaction commits, JWTs and the
ASGI websocket consumer; only the channel transport differs from the default
in-memory test.  No existing Redis keys are deleted.
"""
import os
from unittest import skipUnless

import redis
from channels.layers import get_channel_layer
from channels_redis.core import RedisChannelLayer
from django.test import override_settings

from . import test_report_clock_boss_realtime as baseline


REDIS_URL = os.environ.get('XA_REPORT_CLOCK_REDIS_URL', '')


@skipUnless(REDIS_URL, 'XA_REPORT_CLOCK_REDIS_URL must point to an isolated Redis instance')
@override_settings(CHANNEL_LAYERS={
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {'hosts': [REDIS_URL], 'prefix': 'xa_report_clock_integration'},
    },
})
class ReportClockRedisIntegrationTest(baseline.ReportClockBossRealtimeTest):
    def test_real_redis_channel_layer_is_active(self):
        self.assertIsInstance(get_channel_layer(), RedisChannelLayer)
        self.assertTrue(redis.Redis.from_url(REDIS_URL, socket_connect_timeout=3).ping())
