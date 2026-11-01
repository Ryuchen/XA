"""CI-only SQLite database shared safely by live-server test threads."""
import os
from pathlib import Path

from .test_settings import *  # noqa: F403

_test_db = Path(os.environ['XA_CI_SQLITE_TEST_DB']).resolve()
_runner_temp = Path(os.environ['RUNNER_TEMP']).resolve()
if _test_db.parent != _runner_temp or _test_db.name != 'xa-ci-backend-test.sqlite3':
    raise RuntimeError('CI SQLite test database must be in RUNNER_TEMP')
DATABASES['default']['TEST'] = {'NAME': str(_test_db)}  # noqa: F405
