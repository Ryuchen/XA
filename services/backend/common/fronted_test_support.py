"""Current React HTTP contract fixtures, separate from browser acceptance.

The default transport is Django's disposable LiveServer test database. Explicit
network opt-in retains the historical two-worker/Redis integration boundary.
"""
from contextlib import contextmanager
import os
import shutil
from unittest import SkipTest
from pathlib import Path
from django.conf import settings
from django.test import LiveServerTestCase
from rest_framework.test import APIClient


class ReactLiveHTTPTestCase(LiveServerTestCase):
    client_class = APIClient


def fronted_root(role):
    if not shutil.which('node'):
        raise SkipTest('Requires Node for the current React HTTP contracts')
    legacy = 'XA_' + role.upper() + '_VUE_ROOT'
    return (os.environ.get('XA_' + role.upper() + '_REACT_ROOT')
            or os.environ.get(legacy)
            or str(Path(settings.BASE_DIR).parent.parent / 'fronted' / role))


@contextmanager
def current_http(test, network_factory, **kwargs):
    if os.environ.get('XA_REACT_CONTRACT_REAL_NETWORK') == '1':
        with network_factory(**kwargs) as network:
            yield network
    else:
        yield test.live_server_url, None
