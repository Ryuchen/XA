"""Real MySQL regression tests alongside the running local Compose application.

Test database IDs can overlap the application's IDs. Never deliver test tasks
or notifications to the application's Redis worker and WebSocket consumers.
The deployed HTTP smoke and queue checks use normal config.settings separately.
"""
import tempfile

from .mysql_test_settings import *  # noqa: F403

CELERY_BROKER_URL = 'memory://'
CELERY_RESULT_BACKEND = 'cache+memory://'
CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

# The web container also mounts the application's media volume. Test uploads
# belong to this test process and must never share that persistent directory.
_test_media = tempfile.TemporaryDirectory(prefix='xa-container-test-media-')
MEDIA_ROOT = _test_media.name
