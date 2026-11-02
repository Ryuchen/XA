"""Opt-in settings for two local ASGI test processes sharing isolated Redis."""
import os

from .asgi_network_test_settings import *  # noqa: F403

_redis_url = os.environ.get('XA_ASGI_TEST_REDIS_URL')
_prefix = os.environ.get('XA_ASGI_TEST_REDIS_PREFIX')
if not _redis_url or not _prefix or not _prefix.startswith('xa_asgi_test_'):
    raise RuntimeError('Isolated ASGI Redis URL and prefix are required')
if os.environ.get('XA_ASGI_TEST_MEDIA_ROOT'):
    MEDIA_ROOT = os.environ['XA_ASGI_TEST_MEDIA_ROOT']
CHANNEL_LAYERS = {'default': {'BACKEND': 'channels_redis.core.RedisChannelLayer',
                              'CONFIG': {'hosts': [_redis_url], 'prefix': _prefix}}}
