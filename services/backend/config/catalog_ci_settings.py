"""Settings for the disposable, private CI catalog-verification database."""
import os

from .migration_test_settings import *  # noqa: F403

DATABASES = {'default': {
    'ENGINE': 'django.db.backends.mysql', 'NAME': 'xa_verify',
    'USER': 'xa_verify', 'PASSWORD': os.environ['XA_CATALOG_TEST_PASSWORD'],
    'HOST': 'db', 'PORT': '3306',
    'OPTIONS': {'charset': 'utf8mb4', 'isolation_level': 'read committed'},
    'TEST': {'NAME': 'xa_verify', 'CHARSET': 'utf8mb4'},
}}
