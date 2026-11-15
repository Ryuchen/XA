"""Opt-in real Redis/worker transport checks against a disposable SQLite file."""
import os
from urllib.parse import urlparse

from .asgi_network_test_settings import *  # noqa: F403

_redis_url = os.environ.get('XA_REPAIR_TRANSPORT_REDIS_URL', '')
_parsed = urlparse(_redis_url)
_prefix = os.environ.get('XA_REPAIR_TRANSPORT_PREFIX', '')
if (_parsed.scheme != 'redis' or _parsed.hostname != '127.0.0.1'
        or not _parsed.password or not _parsed.port
        or not _prefix.startswith('xa_repair_transport_')):
    raise RuntimeError('Transport checks require a password-protected disposable loopback Redis and test prefix')

CELERY_BROKER_URL = _redis_url
CELERY_RESULT_BACKEND = None
CELERY_TASK_ALWAYS_EAGER = False
CELERY_BEAT_SCHEDULE = {}
CHANNEL_LAYERS = {'default': {'BACKEND': 'stores.channels.StoreChannelLayer', 'CONFIG': {
    'backend': 'channels_redis.core.RedisChannelLayer',
    'config': {'hosts': [_redis_url], 'prefix': _prefix},
}}}
KOOK_ENABLED = False
WECOM_DISPATCH_ENABLED = False
WECHAT_APPID = ''
WECHAT_SECRET = ''
