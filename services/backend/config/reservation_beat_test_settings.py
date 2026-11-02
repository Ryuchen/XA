"""Explicitly isolated broker for the real reservation worker/beat test."""
import os
from urllib.parse import urlparse

from .asgi_network_test_settings import *  # noqa: F403

_broker = os.environ.get('XA_RESERVATION_TEST_BROKER', '')
_parsed = urlparse(_broker)
if _parsed.scheme != 'redis' or _parsed.hostname != '127.0.0.1' or not _parsed.password or not _parsed.port:
    raise RuntimeError('Reservation beat test requires a password-protected loopback Redis')
CELERY_BROKER_URL = _broker
CELERY_RESULT_BACKEND = None
# Preserve the actual production schedule while preventing unrelated jobs from
# touching even isolated test accounts during this focused acceptance.
CELERY_BEAT_SCHEDULE = {'expire-reservations': CELERY_BEAT_SCHEDULE['expire-reservations']}  # noqa: F405
