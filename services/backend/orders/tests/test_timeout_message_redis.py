"""Run timeout message delivery against an explicitly isolated Redis instance."""
import os
from unittest import skipUnless

import redis
from channels.layers import get_channel_layer
from channels_redis.core import RedisChannelLayer
from django.test import override_settings

from . import test_timeout_message_realtime as baseline


REDIS_URL = os.environ.get('XA_TIMEOUT_MESSAGE_REDIS_URL', '')


@skipUnless(REDIS_URL, 'XA_TIMEOUT_MESSAGE_REDIS_URL must point to isolated Redis')
@override_settings(CHANNEL_LAYERS={
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {'hosts': [REDIS_URL], 'prefix': 'xa_timeout_message_integration'},
    },
})
class TimeoutMessageRedisTest(baseline.TimeoutMessageRealtimeTest):
    def test_real_redis_channel_layer_is_active(self):
        self.assertIsInstance(get_channel_layer(), RedisChannelLayer)
        self.assertTrue(redis.Redis.from_url(REDIS_URL, socket_connect_timeout=3).ping())
