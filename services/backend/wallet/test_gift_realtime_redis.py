"""Gift wallet and inbox events through an explicitly isolated Redis channel."""
import os
from unittest import skipUnless

from django.test import override_settings
from channels.layers import get_channel_layer
from channels_redis.core import RedisChannelLayer
from wallet.test_recharge_live_workflow import RechargeLiveWorkflowTest

REDIS_URL = os.environ.get('XA_GIFT_REDIS_URL', '')


@skipUnless(REDIS_URL, 'XA_GIFT_REDIS_URL must point to isolated Redis')
@override_settings(CHANNEL_LAYERS={'default': {
    'BACKEND': 'channels_redis.core.RedisChannelLayer',
    'CONFIG': {'hosts': [REDIS_URL], 'prefix': 'xa_gift_integration'},
}})
class GiftRealtimeRedisTest(RechargeLiveWorkflowTest):
    def test_gift_basket_updates_boss_provider_and_wallet_reviewer(self):
        self.assertIsInstance(get_channel_layer(), RedisChannelLayer)
        super().test_gift_basket_updates_boss_provider_and_wallet_reviewer()
