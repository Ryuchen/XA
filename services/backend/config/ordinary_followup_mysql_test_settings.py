"""Only the randomly named, network-isolated MySQL test container may be used."""
import os
import re

from django.core.exceptions import ImproperlyConfigured
from .migration_test_settings import *  # noqa: F403

_run_id = os.environ.get('XA_FOLLOWUP_MYSQL_RUN_ID', '')
_hostname = f'xa-followup-mysql-{_run_id}'
if (os.environ.get('XA_FOLLOWUP_MYSQL_ISOLATED') != '1' or not re.fullmatch(r'[0-9a-f]{12}', _run_id)
        or os.environ.get('XA_FOLLOWUP_MYSQL_HOST') != _hostname):
    raise ImproperlyConfigured('追加售后 MySQL 验证只允许随机命名的隔离测试容器')

DATABASES = {}
for _alias, _suffix in (('default', 'a'), ('store_b', 'b')):
    _name = f'xa_test_followup_{_run_id}_{_suffix}'
    DATABASES[_alias] = {'ENGINE': 'django.db.backends.mysql', 'NAME': _name, 'USER': 'root', 'PASSWORD': '',
        'HOST': _hostname, 'PORT': '3306', 'OPTIONS': {'charset': 'utf8mb4', 'isolation_level': 'read committed'},
        'TEST': {'NAME': f'test_{_name}', 'CHARSET': 'utf8mb4'}}
DATABASE_ROUTERS = ['stores.router.StoreRouter']
STORE_DATABASE_ALIASES = ('store_b',)
STORE_CHANNEL_LAYER = {'BACKEND': 'channels.layers.InMemoryChannelLayer'}
CHANNEL_LAYERS = {'default': {'BACKEND': 'stores.channels.StoreChannelLayer'}}
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                      'KEY_FUNCTION': 'stores.cache.make_key'}}
LEGACY_FORCE_AUTH_COMPAT = False
CELERY_TASK_ALWAYS_EAGER = False
KOOK_ENABLED = False
WECOM_DISPATCH_ENABLED = False
