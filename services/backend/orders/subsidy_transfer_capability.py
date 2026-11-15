"""Capability switch for the safety-box transfer agreement workflow."""
from django.conf import settings


def production_enabled():
    # Explicit deployment opt-in; underlying subsidy, segment and proposal
    # capabilities still perform their own checks.
    return getattr(settings, 'VIP_SAFE_BOX_TRANSFER_NEGOTIATION_ENABLED', False) is True


def stage_enabled(test_flag):
    return production_enabled() or getattr(settings, test_flag, False) is True
