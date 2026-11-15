"""Isolated, real Redis/worker/beat verification for VIP subsidy retries."""
from .reservation_beat_test_settings import *  # noqa: F403
from .settings import CELERY_BEAT_SCHEDULE as PRODUCTION_SCHEDULE

VIP_SAFE_BOX_SUBSIDY_ENABLED = True
CELERY_BEAT_SCHEDULE = {name: PRODUCTION_SCHEDULE[name] for name in
                        ('retry-completion-subsidies', 'confirm-member-consumption')}
