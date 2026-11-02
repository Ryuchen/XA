"""Opt-in isolated Redis broker for a real order-timeout worker test."""
import os
from urllib.parse import urlparse

from .asgi_network_test_settings import *  # noqa: F403

_broker = os.environ.get('XA_ORDER_TIMEOUT_TEST_BROKER', '')
_parsed = urlparse(_broker)
if _parsed.scheme != 'redis' or _parsed.hostname != '127.0.0.1' or not _parsed.password or not _parsed.port:
    raise RuntimeError('Order timeout test requires a password-protected loopback Redis')
CELERY_BROKER_URL = _broker
CELERY_RESULT_BACKEND = None
CELERY_TASK_ALWAYS_EAGER = False
