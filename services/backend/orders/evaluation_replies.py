"""Conditional updates shared by staff and provider reply endpoints."""
from django.db.models import F
from django.utils import timezone

from .models import Evaluation


def valid_reply_version(value):
    return type(value) is int and 0 <= value < 2**63 - 1


def reply_notice_preview(evaluation):
    from .evaluation_targets import evaluation_provider_name
    name = evaluation_provider_name(evaluation)
    service = evaluation.order.service_name_snapshot or '服务订单'
    return f'{name} · 「{service}」的评价收到回复，可在订单中查看'[:255]


def write_reply_if_current(evaluation, content, version, author):
    now = timezone.now()
    changed = Evaluation.objects.filter(pk=evaluation.pk, reply_version=version).update(
        reply_content=content, reply_author=author, replied_at=now, updated_at=now,
        reply_version=F('reply_version') + 1,
    )
    if changed:
        evaluation.refresh_from_db()
    return bool(changed)
