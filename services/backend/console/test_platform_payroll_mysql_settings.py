"""Only the per-run loopback MySQL container is admitted by these test settings."""
import os
import re
from django.core.exceptions import ImproperlyConfigured
from config.migration_test_settings import *  # noqa: F403

_run = os.environ.get('XA_PLATFORM_PAYROLL_MYSQL_RUN', '')
_port = os.environ.get('XA_PLATFORM_PAYROLL_MYSQL_PORT', '')
# 本文件是 settings 模块而非用例：仅当被真正用作 DJANGO_SETTINGS_MODULE 时才
# 强制随机隔离 MySQL；被测试发现器按 test_ 前缀顺带导入时不应抛错污染 census。
if os.environ.get('DJANGO_SETTINGS_MODULE') == 'console.test_platform_payroll_mysql_settings' and (
        os.environ.get('XA_PLATFORM_PAYROLL_MYSQL_ISOLATED') != '1'
        or not re.fullmatch(r'[0-9a-f]{12}', _run)
        or not _port.isdigit() or not 1024 <= int(_port) <= 65535):
    raise ImproperlyConfigured('平台工资测试仅允许随机隔离 MySQL')
DATABASES = {}
for _alias, _suffix in (('default', 'a'), ('store_b', 'b')):
    _name = f'xa_test_payroll_{_run}_{_suffix}'
    DATABASES[_alias] = {'ENGINE': 'django.db.backends.mysql', 'NAME': _name, 'USER': 'xa_test_payroll',
        'PASSWORD': 'payroll-isolated-user', 'HOST': '127.0.0.1', 'PORT': _port,
        'OPTIONS': {'charset': 'utf8mb4', 'isolation_level': 'read committed'},
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
