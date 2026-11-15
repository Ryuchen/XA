"""Two disposable databases; all business logic and migrations execute normally."""
from .migration_test_settings import *  # noqa: F403

DATABASES = {
    'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'},
    'store_b': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'},
}
DATABASE_ROUTERS = ['stores.router.StoreRouter']
STORE_DATABASE_ALIASES = ('store_b',)
STORE_CHANNEL_LAYER = {'BACKEND': 'channels.layers.InMemoryChannelLayer'}
CHANNEL_LAYERS = {'default': {'BACKEND': 'stores.channels.StoreChannelLayer'}}
CACHES = {'default': {
    'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    'KEY_FUNCTION': 'stores.cache.make_key',
}}
LEGACY_FORCE_AUTH_COMPAT = False
CELERY_TASK_ALWAYS_EAGER = False
KOOK_ENABLED = False
WECOM_DISPATCH_ENABLED = False
