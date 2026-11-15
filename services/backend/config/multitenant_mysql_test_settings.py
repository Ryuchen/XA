"""Isolated two-store MySQL regression; credentials stay in the ignored runtime."""
from copy import deepcopy

from .fronted_mysql_test_settings import *  # noqa: F403

DATABASES = deepcopy(DATABASES)  # noqa: F405
DATABASES['default']['NAME'] = 'xa_test_stores_a_20261104'
DATABASES['default']['TEST']['NAME'] = 'test_xa_test_stores_a_20261104'
DATABASES['store_b'] = deepcopy(DATABASES['default'])
DATABASES['store_b']['NAME'] = 'xa_test_stores_b_20261104'
DATABASES['store_b']['TEST']['NAME'] = 'test_xa_test_stores_b_20261104'
DATABASE_ROUTERS = ['stores.router.StoreRouter']
STORE_DATABASE_ALIASES = ('store_b',)
STORE_CHANNEL_LAYER = {'BACKEND': 'channels.layers.InMemoryChannelLayer'}
CHANNEL_LAYERS = {'default': {'BACKEND': 'stores.channels.StoreChannelLayer'}}
CACHES = {'default': {
    'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    'KEY_FUNCTION': 'stores.cache.make_key',
}}
LOGIN_OTP_REQUIRED = False
LEGACY_FORCE_AUTH_COMPAT = False
CELERY_TASK_ALWAYS_EAGER = False
