"""Opt-in isolated browser test: one directory file and one new tenant file."""
import os
from pathlib import Path

from .asgi_network_test_settings import *  # noqa: F403

_tenant = Path(os.environ['XA_PLATFORM_STORE_TEST_DB']).resolve()
_folder = (Path(BASE_DIR).parent.parent / '.tools').resolve()  # noqa: F405
if _tenant.parent != _folder or not _tenant.name.startswith('asgi-reservation-store-') or _tenant.suffix != '.sqlite3':
    raise RuntimeError('Platform browser tenant must be an isolated .tools test file')
DATABASES['store_new'] = {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(_tenant)}  # noqa: F405
DATABASE_ROUTERS = ['stores.router.StoreRouter']
STORE_DATABASE_ALIASES = ('store_new',)
PLATFORM_IDENTITY_ENABLED = True
VIP_MONTHLY_DECISIONS_ENABLED = os.environ.get('XA_MONTHLY_CUTOVER_BROWSER') == '1'
