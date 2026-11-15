"""Fast two-store isolation tests; no persistent databases or services."""
from config.test_settings import *  # noqa: F403

DATABASES = {
    'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'},
    'store_b': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'},
}
DATABASE_ROUTERS = ['stores.router.StoreRouter']
STORE_DATABASE_ALIASES = ('store_b',)
STORE_CHANNEL_LAYER = {'BACKEND': 'channels.layers.InMemoryChannelLayer'}
CHANNEL_LAYERS = {'default': {'BACKEND': 'stores.channels.StoreChannelLayer'}}
