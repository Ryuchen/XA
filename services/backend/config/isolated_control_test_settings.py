"""Three disposable databases: independent control and two store schemas."""
from .multitenant_test_settings import *  # noqa: F403

DATABASES = {
    'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'},
    'store_a': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'},
    'store_b': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'},
}
STORE_DATABASE_ALIASES = ('store_a', 'store_b')
ISOLATED_CONTROL_PLANE = True
