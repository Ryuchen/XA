"""Isolated ASGI server for team evidence with production-style report approval."""
from .timeout_worker_realtime_test_settings import *  # noqa: F403

ORDER_SETTLEMENT_MODE = 'REPORT_APPROVAL'
REQUIRE_ORDER_EVIDENCE_IMAGES = True
