"""Only for the explicitly isolated provisioning recovery verification script."""
import os
from pathlib import Path

from .multitenant_test_settings import *  # noqa: F403

_folder = Path(os.environ['XA_PROVISION_TEST_FOLDER']).resolve(strict=True)
_default = _folder / 'directory.sqlite3'
_target = _folder / 'store.sqlite3'
if _default.exists() or _target.exists() or not _folder.name.startswith('xa-provision-check-'):
    raise RuntimeError('Provisioning verification requires a new, isolated directory')
DATABASES = {
    'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(_default)},
    'store_b': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(_target)},
}
