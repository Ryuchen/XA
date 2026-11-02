"""Isolated broker and channel layer for a cross-process timeout test."""
import os
from urllib.parse import urlparse

from .order_timeout_worker_test_settings import *  # noqa: F403

_channel_url = os.environ.get('XA_TIMEOUT_REALTIME_REDIS_URL', '')
_parsed_channel = urlparse(_channel_url)
if (_parsed_channel.scheme != 'redis' or _parsed_channel.hostname != '127.0.0.1'
        or not _parsed_channel.password or not _parsed_channel.port):
    raise RuntimeError('Timeout realtime test requires a password-protected loopback Redis')
CHANNEL_LAYERS = {'default': {'BACKEND': 'channels_redis.core.RedisChannelLayer',
                             'CONFIG': {'hosts': [_channel_url], 'prefix': 'xa_timeout_worker_realtime'}}}
