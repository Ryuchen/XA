import os
import tempfile

if os.environ.get('XA_ORDER_TEST_MYSQL') == '1':
    from .mysql_test_settings import *  # noqa: F403
else:
    from .migration_test_settings import *  # noqa: F403

CELERY_BROKER_URL = 'memory://'
CELERY_RESULT_BACKEND = 'cache+memory://'
CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'xa-isolated-order-tests'}}
_test_media = tempfile.TemporaryDirectory(prefix='xa-order-tests-')
MEDIA_ROOT = _test_media.name
