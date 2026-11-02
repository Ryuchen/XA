"""Isolated file database for the concurrent live browser refund test."""
import os
from pathlib import Path
from .test_settings import *  # noqa: F403

_database = os.environ.get('XA_NEGOTIATED_TEST_DB', '')
_path = Path(_database).resolve()
_directory = (Path(BASE_DIR).parent.parent / '.tools').resolve()  # noqa: F405
if _path.parent != _directory or not _path.name.startswith('negotiated-refund-test-') or _path.suffix != '.sqlite3':
    raise RuntimeError('XA_NEGOTIATED_TEST_DB must name an isolated .tools/negotiated-refund-test-*.sqlite3 file')
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(_path),
                         'TEST': {'NAME': str(_path)}, 'OPTIONS': {'timeout': 20}}}
