"""独立 MySQL 验收库，保留真实迁移和行锁；只允许使用明确的测试库名称。"""
import os

from django.core.exceptions import ImproperlyConfigured

from .migration_test_settings import *  # noqa: F403

_database_name = os.environ.get('XA_TEST_MYSQL_DATABASE', 'xa_test_release')
if not _database_name.startswith('xa_test_') or not _database_name.replace('_', '').isalnum():
    raise ImproperlyConfigured('XA_TEST_MYSQL_DATABASE 必须为 xa_test_ 开头的测试库名称')

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': _database_name,
        'USER': os.environ.get('XA_TEST_MYSQL_USER', 'xa_test'),
        'PASSWORD': os.environ.get('XA_TEST_MYSQL_PASSWORD', ''),
        'HOST': os.environ.get('XA_TEST_MYSQL_HOST', '127.0.0.1'),
        'PORT': os.environ.get('XA_TEST_MYSQL_PORT', '13306'),
        'OPTIONS': {'charset': 'utf8mb4', 'isolation_level': 'read committed'},
        'TEST': {'NAME': f'test_{_database_name}', 'CHARSET': 'utf8mb4'},
    },
}
