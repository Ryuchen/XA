"""Validate deployment-owned database aliases before exposing store registration."""
import copy
import json
import re
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


def install_store_databases(databases, raw):
    try:
        configured = json.loads(raw or '{}')
    except (TypeError, ValueError) as exc:
        raise ImproperlyConfigured('XA_STORE_DATABASES must be a JSON object') from exc
    if not isinstance(configured, dict):
        raise ImproperlyConfigured('XA_STORE_DATABASES must be a JSON object')
    names = set()
    for alias, config in databases.items():
        name = str(config.get('NAME', ''))
        if name and name != ':memory:':
            names.add(_database_identity(config))
    for alias, overrides in configured.items():
        if (not isinstance(alias, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,47}', alias)
                or alias in databases or alias == 'default'):
            raise ImproperlyConfigured('Store database alias must be unique and cannot be default')
        if not isinstance(overrides, dict) or not overrides.get('NAME'):
            raise ImproperlyConfigured(f'Store database {alias} must explicitly configure NAME')
        database = copy.deepcopy(databases['default'])
        database.pop('TEST', None)
        database.update(overrides)
        identity = _database_identity(database)
        if identity in names:
            raise ImproperlyConfigured(f'Store database {alias} is shared with another store/database')
        if str(database['NAME']) != ':memory:':
            names.add(identity)
        databases[alias] = database
    return tuple(configured)


def _database_identity(database):
    name = str(database.get('NAME', ''))
    if 'sqlite' in database.get('ENGINE', ''):
        return ('sqlite', str(Path(name).resolve()).casefold())
    # Conservatively require distinct MySQL database names across all configured
    # hosts as localhost / an IP / DNS aliases can refer to the same server.
    return ('database', name.casefold())
