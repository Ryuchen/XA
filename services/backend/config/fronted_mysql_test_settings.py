"""Real MySQL regression DB, with no shared cache, notifications, tasks or media."""
import tempfile
from .fronted_acceptance_settings import *  # noqa: F403

CELERY_BROKER_URL = 'memory://'
CELERY_RESULT_BACKEND = 'cache+memory://'
CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
_media = tempfile.TemporaryDirectory(prefix='xa-fronted-mysql-test-')
MEDIA_ROOT = _media.name
