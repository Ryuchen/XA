"""Deterministic, deployment-controlled MySQL targets for newly created stores.

The feature defaults on when a deployment supplies a template alias. No caller
may supply connection credentials or an arbitrary database name. Materializing
a connection does not create a database.
"""
from collections.abc import Mapping
from copy import deepcopy
import re
from threading import RLock

from django.conf import settings
from django.db import connections

from .database_identity import identity_fingerprint, identity_from_config

_lock = RLock()
_registered = set()
_SLUG = re.compile(r'[a-z][a-z0-9-]{0,47}\Z', re.ASCII)
_MISSING_CLAIM = '门店数据库缺少归属记录，请联系平台核对'
_UNTRACKED_TARGET_OPTIONS = frozenset({
    'host', 'port', 'database', 'db', 'unix_socket', 'read_default_file',
    'read_default_group', 'init_command',
})


def enabled():
    return bool(getattr(settings, 'DYNAMIC_STORE_DATABASE_ENABLED', False))


def configured():
    alias = getattr(settings, 'DYNAMIC_STORE_DATABASE_TEMPLATE_ALIAS', '')
    template = settings.DATABASES.get(alias)
    return bool(enabled() and alias and alias != 'default' and template
                and 'mysql' in template.get('ENGINE', ''))


def alias_for_slug(slug):
    if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
        raise ValueError('门店标识格式无效')
    return 'tenant_' + slug.replace('-', '_')


def uses_dynamic_connection(store):
    return enabled() and store.database_alias == alias_for_slug(store.slug)


def discover_store_connection(store):
    """Resolve the directory-owned target without opening the tenant database.

    An uninitialized registration can lack a claim until provisioning starts.
    In that case, only the connection definition can be prepared.
    """
    if uses_dynamic_connection(store):
        from .models import StoreProvisioningClaim
        fingerprint = StoreProvisioningClaim.objects.filter(store_id=store.pk).values_list(
            'database_fingerprint', flat=True).first()
        if fingerprint is None and (store.initialized_at is not None or store.is_active):
            raise ValueError(_MISSING_CLAIM)
        materialize(store.slug, expected_alias=store.database_alias, expected_fingerprint=fingerprint)
        store._verified_database_fingerprint = fingerprint
    return store.database_alias in settings.DATABASES


def ensure_store_connection(store):
    """Bind dynamic tenant I/O to a directory claim or verified signed route."""
    if not uses_dynamic_connection(store):
        return
    expected = getattr(store, '_verified_database_fingerprint', None)
    if expected is None:
        discover_store_connection(store)
        if store._verified_database_fingerprint is None:
            raise ValueError(_MISSING_CLAIM)
    else:
        materialize(store.slug, expected_alias=store.database_alias, expected_fingerprint=expected)


def store_connection_ready(store):
    """Resolve a registered initialized store without opening its database."""
    if store is None or not store.initialized_at:
        return False
    try:
        return discover_store_connection(store)
    except ValueError:
        return False


def materialize(slug, expected_alias=None, *, expected_fingerprint=None):
    if not enabled():
        return None
    alias = alias_for_slug(slug)
    if expected_alias is not None and expected_alias != alias:
        raise ValueError('门店数据库标识不匹配')
    template_alias = getattr(settings, 'DYNAMIC_STORE_DATABASE_TEMPLATE_ALIAS', '')
    template = settings.DATABASES.get(template_alias)
    if not configured() or template_alias == alias:
        raise ValueError('门店数据库模板未正确配置')
    candidate = deepcopy(template)
    options = candidate.get('OPTIONS', {})
    if not isinstance(options, Mapping) or _UNTRACKED_TARGET_OPTIONS.intersection(options):
        raise ValueError('门店数据库模板包含无法核对归属的连接选项')
    candidate['NAME'] = alias
    # Connections added after Django's startup normalization must carry the
    # same TEST defaults, without inheriting the template's test database.
    candidate['TEST'] = {'CHARSET': None, 'COLLATION': None, 'MIGRATE': True,
                         'MIRROR': None, 'NAME': None}
    identity = identity_from_config(alias, candidate)
    if expected_fingerprint is not None and identity_fingerprint(identity) != expected_fingerprint:
        raise ValueError('目标数据库配置已变化，请联系平台核对门店归属')
    with _lock:
        existing = settings.DATABASES.get(alias)
        if existing is not None:
            if alias not in _registered:
                raise ValueError('门店数据库标识与部署配置冲突')
            if identity_from_config(alias, existing) != identity:
                raise ValueError('门店数据库配置冲突')
        else:
            settings.DATABASES[alias] = candidate
            if connections.databases is not settings.DATABASES:
                connections.databases[alias] = candidate
            _registered.add(alias)
        aliases = list(getattr(settings, 'STORE_DATABASE_ALIASES', ()))
        if alias not in aliases:
            settings.STORE_DATABASE_ALIASES = (*aliases, alias)
    return alias
