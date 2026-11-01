"""Opt-in file-backed database shared with an isolated ASGI test process."""
import os
from pathlib import Path

from .migration_test_settings import *  # noqa: F403

_database = os.environ.get('XA_ASGI_NETWORK_TEST_DB')
if not _database:
    raise RuntimeError('XA_ASGI_NETWORK_TEST_DB must name an isolated SQLite file')
_path = Path(_database).resolve()
_test_dir = (Path(BASE_DIR).parent.parent / '.tools').resolve()  # noqa: F405
if _path.parent != _test_dir or not _path.name.startswith('asgi-reservation-') or _path.suffix != '.sqlite3':
    raise RuntimeError('ASGI network database must use .tools/asgi-reservation-*.sqlite3')
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': _database,
                         'TEST': {'NAME': _database}}}
LEGACY_FORCE_AUTH_COMPAT = False
KOOK_ENABLED = False
WECOM_DISPATCH_ENABLED = False
