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
_control = _record.get('control_instance')
if (set(_targets) != {'default', 'store_a', 'store_b'} or _registry.stat().st_mode & 0o077
        or Path(_record['checkout']).resolve() != Path(__file__).resolve().parents[3]
        or type(_record['port']) is not int or not 1024 <= _record['port'] <= 65535):
    raise ImproperlyConfigured('SA14 需要本机私有登记的三个隔离 MySQL 数据库')

if _control and (type(_control['port']) is not int or not 1024 <= _control['port'] <= 65535
                 or _control['port'] == _record['port']):
    raise ImproperlyConfigured('SA14 独立控制实例须登记独占回环端口')

SA14_TEST_CONTROL_INSTANCE = {**_control, 'datadir': _registry.parent / 'mysql-control'} if _control else None
SA14_TEST_REDIS = _record.get('redis')
DATABASES = {}
for _alias, _target in _targets.items():
    _name = _target['name']
    if (not re.fullmatch(r'xa_test_[a-z0-9_]+', _name) or _target['test_name'] != 'test_' + _name
            or not re.fullmatch(r'xa_test_[a-z0-9_]+', _target['user']) or not _target['password']):
        raise ImproperlyConfigured('SA14 MySQL 库名、账号及派生测试库须独立登记')
    DATABASES[_alias] = {
        'ENGINE': 'django.db.backends.mysql', 'NAME': _name, 'USER': _target['user'],
        'PASSWORD': _target['password'], 'HOST': '127.0.0.1',
        'PORT': str(_control['port'] if _alias == 'default' and _control else _record['port']),
        'OPTIONS': {'charset': 'utf8mb4', 'isolation_level': 'read committed', 'connect_timeout': 2,
                    'read_timeout': 5, 'write_timeout': 5},
        'TEST': {'NAME': _target['test_name'], 'CHARSET': 'utf8mb4'},
    }

if (len({item['NAME'] for item in DATABASES.values()}) != 3
        or len({item['USER'] for item in DATABASES.values()}) != 3):
    raise ImproperlyConfigured('SA14 控制库和两家店必须使用各自的库名及账号')

class RegisteredMySQLRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        """Verify each endpoint before Django can create or migrate schemas."""
        identities = {}
        for alias, db_config in DATABASES.items():
            instance = SA14_TEST_CONTROL_INSTANCE if alias == 'default' else None
            expected_socket = (instance or _record)['socket']
            expected_datadir = instance['datadir'] if instance else _registry.parent / 'mysql'
            with pymysql.connect(host=db_config['HOST'], port=int(db_config['PORT']), user=db_config['USER'],
                                 password=db_config['PASSWORD'], connect_timeout=2,
                                 read_timeout=2) as db, db.cursor() as sql:
                sql.execute('SELECT @@socket, @@datadir, @@server_uuid')
                server_socket, datadir, server_id = sql.fetchone()
            if (Path(server_socket).resolve() != Path(expected_socket).resolve()
                    or Path(datadir).resolve() != expected_datadir.resolve()):
                raise ImproperlyConfigured(f'{alias} MySQL 与本次登记不符，未执行迁移')
            identities[alias] = server_id
        if _control and identities['default'] in (identities['store_a'], identities['store_b']):
            raise ImproperlyConfigured('控制库与店库共用 MySQL 实例，未执行迁移')
        return super().setup_databases(**kwargs)


TEST_RUNNER = 'config.certificate_mysql_test_settings.RegisteredMySQLRunner'
STORE_DOMAIN_INDEX_DIR = ''
CELERY_BROKER_URL = 'memory://'
CELERY_RESULT_BACKEND = 'cache+memory://'
