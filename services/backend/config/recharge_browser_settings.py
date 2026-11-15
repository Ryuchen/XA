"""Disposable browser test database and isolated receipt files."""
import os
from .asgi_network_test_settings import *  # noqa: F403

MEDIA_ROOT = os.environ['XA_RECHARGE_TEST_MEDIA']
REQUIRE_RECHARGE_PROOF = True
