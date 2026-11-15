"""Isolated broker and file database for the real settlement scheduler test."""
from .reservation_beat_test_settings import *  # noqa: F403
from .settings import CELERY_BEAT_SCHEDULE as PRODUCTION_SCHEDULE

# Keep the real 60-second schedule and task; exclude unrelated periodic jobs.
CELERY_BEAT_SCHEDULE = {'confirm-member-consumption': PRODUCTION_SCHEDULE['confirm-member-consumption']}
