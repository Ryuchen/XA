"""Celery CLI sessions may consume only the registered SA14 test databases."""
import os
import re
from urllib.parse import quote

import redis
from django.core.exceptions import ImproperlyConfigured
from kombu import Exchange, Queue

from .certificate_mysql_test_settings import *  # noqa: F403

_queue = os.environ.get('XA_SA14_CELERY_QUEUE', '')
_media = os.environ.get('XA_SA14_CELERY_MEDIA', '')
if (not re.fullmatch(r'xa_sa14_cli_[0-9a-f]{32}', _queue) or not _media
        or not SA14_TEST_CONTROL_INSTANCE or not SA14_TEST_REDIS):
    raise ImproperlyConfigured('Celery CLI 需要登记的专用三库、Redis 和独占队列')
if any(os.environ.get(key) for key in ('CELERY_BROKER_URL', 'CELERY_BROKER_READ_URL',
                                      'CELERY_BROKER_WRITE_URL', 'CELERY_RESULT_BACKEND')):
    raise ImproperlyConfigured('Celery CLI 只使用本次登记的 Redis 地址')

DATABASES = {alias: {**db, 'NAME': db['TEST']['NAME']} for alias, db in DATABASES.items()}
_redis = SA14_TEST_REDIS
_port = _redis['port']
with redis.Redis(host='127.0.0.1', port=_port, password=_redis['password'],
                 socket_connect_timeout=2, socket_timeout=2) as _client:
    _server = _client.info('server')
if (_server['process_id'], _server['tcp_port']) != (_redis['pid'], _port):
    raise ImproperlyConfigured('Redis 实例与本次登记不符')
_url = f'redis://:{quote(_redis["password"], safe="")}@127.0.0.1:{_port}'
CELERY_BROKER_URL = f'{_url}/0'
CELERY_RESULT_BACKEND = f'{_url}/1'
CELERY_TASK_DEFAULT_QUEUE = _queue
CELERY_TASK_DEFAULT_EXCHANGE = _queue
CELERY_TASK_DEFAULT_ROUTING_KEY = _queue
CELERY_TASK_QUEUES = (Queue(_queue, Exchange(_queue), routing_key=_queue),)
CELERY_BROKER_TRANSPORT_OPTIONS = {'global_keyprefix': _queue + ':'}
CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS = {'global_keyprefix': _queue + ':'}
_beat_entry = os.environ.get('XA_SA14_CELERY_BEAT_ENTRY', 'confirm-member-consumption')
if _beat_entry not in {'confirm-member-consumption', 'recover-addon-refund-notices'}:
    raise ImproperlyConfigured('Celery CLI 只能调度已登记的隔离验收任务')
CELERY_BEAT_SCHEDULE = {_beat_entry: CELERY_BEAT_SCHEDULE[_beat_entry]}
CELERY_WORKER_ENABLE_REMOTE_CONTROL = False

MEDIA_ROOT = _media
ORDER_SETTLEMENT_MODE = 'REPORT_APPROVAL'
STORE_CERTIFICATE_ENFORCEMENT_ENABLED = True
LICENSE_BENEFIT_ENFORCEMENT_ENABLED = True
LICENSE_SNAPSHOT_CACHE_ENABLED = True
ORDINARY_SERVICE_FUNDING_ENABLED = True
ORIGINAL_ATTRIBUTION_ENABLED = True
SEGMENT_ECONOMICS_CAPTURE_ENABLED = True
PLATFORM_IDENTITY_ENABLED = True
PLATFORM_PASS_REPAYMENT_ENABLED = True
