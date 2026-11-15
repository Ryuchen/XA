"""Opt-in isolated broker and SQLite file for the real room beat test."""
from .reservation_beat_test_settings import *  # noqa: F403
from .settings import CELERY_BEAT_SCHEDULE as PRODUCTION_SCHEDULE

ROOM_COMPLETION_REVIEW_ENABLED = True
ROOM_BOSS_CONFIRMATION_ENABLED = True
ROOM_AUTO_CONFIRMATION_ENABLED = True
ORDER_SETTLEMENT_MODE = 'REPORT_APPROVAL'
CELERY_BEAT_SCHEDULE = {'auto-confirm-room-orders': PRODUCTION_SCHEDULE['auto-confirm-room-orders']}
