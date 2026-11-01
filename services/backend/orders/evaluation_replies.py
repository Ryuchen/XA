"""Conditional updates shared by staff and provider reply endpoints."""
from django.db.models import F
from django.utils import timezone

from .models import Evaluation


def valid_reply_version(value):
    return type(value) is int and 0 <= value < 2**63 - 1


def write_reply_if_current(evaluation, content, version):
    now = timezone.now()
    changed = Evaluation.objects.filter(pk=evaluation.pk, reply_version=version).update(
        reply_content=content, replied_at=now, updated_at=now,
        reply_version=F('reply_version') + 1,
    )
    if changed:
        evaluation.refresh_from_db()
    return bool(changed)
