"""Conservative checks against assigning one configured database twice."""
import ipaddress
import os

from django.conf import settings


def database_identity(alias):
    config = settings.DATABASES.get(alias)
    if not config:
        raise ValueError('已登记门店的数据库配置缺失，无法核对隔离')
    return identity_from_config(alias, config)


def identity_from_config(alias, config):
    engine = str(config.get('ENGINE', '')).lower()
    name = str(config.get('NAME') or '')
    if 'sqlite' in engine:
        if name == ':memory:':
            # Separate SQLite :memory: connections do not share a database.
            return ('sqlite-memory', alias)
        if not name:
            raise ValueError('门店数据库名称无效，无法核对隔离')
        if name.startswith('file:'):
            if 'mode=memory' not in name:
                raise ValueError('SQLite 文件 URI 尚不能安全核对门店隔离')
            # Identical SQLite memory URIs can share a database.
            return ('sqlite-uri', name)
        return ('sqlite-file', os.path.normcase(os.path.realpath(name)))
    if 'mysql' in engine:
        vendor, default_port = 'mysql', 3306
    elif 'postgresql' in engine:
        vendor, default_port = 'postgresql', 5432
    else:
        raise ValueError('该数据库类型尚不能安全核对门店隔离')
    if not name:
        raise ValueError('门店数据库名称无效，无法核对隔离')
    host = str(config.get('HOST') or 'localhost').strip().lower()
    if host in ('localhost', '127.0.0.1', '::1', '[::1]'):
        host = 'loopback'
    else:
        try:
            host = str(ipaddress.ip_address(host.strip('[]')))
        except ValueError:
            pass
    try:
        port = int(config.get('PORT') or default_port)
        if not 1 <= port <= 65535:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError('门店数据库端口无效，无法核对隔离') from None
    return (vendor, host, port, name.casefold())


def assert_distinct_store_database(alias, *, exclude_store_id=None):
    from django.db import connections
    from .models import Store
    identity = database_identity(alias)
    if alias != 'default':
        control = database_identity('default')
        if identity == control:
            raise ValueError('门店数据库不能与平台控制库共用')
        if (identity[0] in ('mysql', 'postgresql') and control[0] == identity[0]
                and identity[3] == control[3]):
            raise ValueError('数据库名称与平台控制库重复，暂无法确认隔离')
    stores = Store.objects.all().order_by('id')
    if exclude_store_id is not None:
        stores = stores.exclude(pk=exclude_store_id)
    if connections['default'].in_atomic_block:
        # Locking reads see the latest committed registrations even if an
        # outer MySQL REPEATABLE READ transaction already has an older view.
        stores = stores.select_for_update()
    for existing in stores.only('database_alias'):
        other = database_identity(existing.database_alias)
        if other == identity:
            raise ValueError('该数据库与已登记门店共用，不能用于新门店')
        if identity[0] in ('mysql', 'postgresql') and other[0] == identity[0] and identity[3] == other[3]:
            # Different hosts can be aliases or proxies for the same server;
            # until server identity can be proven, require distinct DB names.
            raise ValueError('数据库名称与已登记门店重复，暂无法确认隔离')


def lock_store_registration():
    """Serialize directory registrations before comparing configured targets."""
    from django.db import connections
    from .models import Store, StoreRegistryLock
    if not connections['default'].in_atomic_block:
        raise RuntimeError('门店登记锁必须在平台目录事务内获取')
    if getattr(settings, 'ISOLATED_CONTROL_PLANE', False):
        StoreRegistryLock.objects.get_or_create(pk=1)
        return StoreRegistryLock.objects.select_for_update().get(pk=1)
    # The registered local preview still has the combined database and has
    # not applied this migration. Keep one consistent mutex in that mode.
    anchor = Store.objects.select_for_update().filter(database_alias='default').first()
    if anchor is None:
        raise ValueError('平台门店目录尚未初始化，无法安全登记门店')
    return anchor
