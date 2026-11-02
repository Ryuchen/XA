"""Isolated UI recharge server with a disposable proof-image directory."""
import os
from .timeout_worker_realtime_test_settings import *  # noqa: F403

MEDIA_ROOT = os.environ['XA_ISOLATED_MEDIA_ROOT']
REQUIRE_RECHARGE_PROOF = True
