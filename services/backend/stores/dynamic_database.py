"""Deterministic, deployment-controlled MySQL targets for newly created stores.

The feature defaults on when a deployment supplies a template alias. No caller
may supply connection credentials or an arbitrary database name. Materializing
a connection does not create a database.
"""
from copy import deepcopy
import re
from threading import RLock

from django.conf import settings
from django.db import connections

_lock = RLock()
_registered = set()
_SLUG = re.compile(r'[a-z][a-z0-9-]{0,47}\Z', re.ASCII)


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


def discover_store_connection(store):
    """Register this directory-owned tenant after a process restart.

    This only reconstructs an in-process connection definition; it does not
    open, create, migrate, or alter the target database.
    """
    if enabled() and store.database_alias == alias_for_slug(store.slug):
        materialize(store.slug, expected_alias=store.database_alias)
    return store.database_alias in settings.DATABASES


def store_connection_ready(store):
    """Resolve a registered initialized store without opening its database."""
    if store is None or not store.initialized_at:
        return False
    try:
        return discover_store_connection(store)
    except ValueError:
        return False


def materialize(slug, expected_alias=None):
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
    candidate['NAME'] = alias
    # Connections added after Django's startup normalization must carry the
    # same TEST defaults, without inheriting the template's test database.
    candidate['TEST'] = {'CHARSET': None, 'COLLATION': None, 'MIGRATE': True,
                         'MIRROR': None, 'NAME': None}
    with _lock:
        existing = settings.DATABASES.get(alias)
        if existing is not None:
            if alias not in _registered:
                raise ValueError('门店数据库标识与部署配置冲突')
            from .database_identity import identity_from_config
            if identity_from_config(alias, existing) != identity_from_config(alias, candidate):
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
