"""Registered disposable MySQL control and tenant schemas for SA14."""
import json
import os
import re
from pathlib import Path

import pymysql
from django.core.exceptions import ImproperlyConfigured
from django.test.runner import DiscoverRunner

from .isolated_control_test_settings import *  # noqa: F403

_registry = Path(os.environ['XA_SA14_MYSQL_REGISTRATION'])
_record = json.loads(_registry.read_text())
_targets = _record['databases']
if (set(_targets) != {'default', 'store_a', 'store_b'} or _registry.stat().st_mode & 0o077
        or Path(_record['checkout']).resolve() != Path(__file__).resolve().parents[3]
        or type(_record['port']) is not int or not 1024 <= _record['port'] <= 65535):
    raise ImproperlyConfigured('SA14 需要本机私有登记的三个隔离 MySQL 数据库')

DATABASES = {}
for _alias, _target in _targets.items():
    _name = _target['name']
    if (not re.fullmatch(r'xa_test_[a-z0-9_]+', _name) or _target['test_name'] != 'test_' + _name
            or not re.fullmatch(r'xa_test_[a-z0-9_]+', _target['user']) or not _target['password']):
        raise ImproperlyConfigured('SA14 MySQL 库名、账号及派生测试库须独立登记')
    DATABASES[_alias] = {
        'ENGINE': 'django.db.backends.mysql', 'NAME': _name, 'USER': _target['user'],
        'PASSWORD': _target['password'], 'HOST': '127.0.0.1', 'PORT': str(_record['port']),
        'OPTIONS': {'charset': 'utf8mb4', 'isolation_level': 'read committed', 'connect_timeout': 2,
                    'read_timeout': 5, 'write_timeout': 5},
        'TEST': {'NAME': _target['test_name'], 'CHARSET': 'utf8mb4'},
    }

if (len({item['NAME'] for item in DATABASES.values()}) != 3
        or len({item['USER'] for item in DATABASES.values()}) != 3):
    raise ImproperlyConfigured('SA14 控制库和两家店必须使用各自的库名及账号')

class RegisteredMySQLRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        """Verify the instance before Django can create or migrate schemas."""
        control = DATABASES['default']
        with pymysql.connect(host=control['HOST'], port=int(control['PORT']), user=control['USER'],
                             password=control['PASSWORD'], connect_timeout=2, read_timeout=2) as db, db.cursor() as sql:
            sql.execute('SELECT @@socket, @@datadir')
            server_socket, datadir = map(Path, sql.fetchone())
        if (server_socket.resolve() != Path(_record['socket']).resolve()
                or datadir.resolve() != (_registry.parent / 'mysql').resolve()):
            raise ImproperlyConfigured('MySQL 实例与本次登记不符，未执行迁移')
        return super().setup_databases(**kwargs)


TEST_RUNNER = 'config.certificate_mysql_test_settings.RegisteredMySQLRunner'
STORE_DOMAIN_INDEX_DIR = ''
CELERY_BROKER_URL = 'memory://'
CELERY_RESULT_BACKEND = 'cache+memory://'
