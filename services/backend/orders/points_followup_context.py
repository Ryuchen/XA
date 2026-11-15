"""Call-local recursion context for isolated funding evidence checks.

An active entry means the complete economic proof is currently being checked
by an outer frame. It must never be used to accept an unchecked top-level
receipt or to persist a partial release.
"""
from contextlib import contextmanager
from contextvars import ContextVar

from rest_framework.exceptions import ValidationError

from stores.context import current_database


_active = ContextVar('points_followup_revision_checks', default=frozenset())


def is_active(posting_id):
    return (current_database(), posting_id) in _active.get()


@contextmanager
def checking(posting_id):
    key = (current_database(), posting_id)
    current = _active.get()
    if key in current or len(current) >= 128:
        raise ValidationError('后续退款凭证核验层级异常')
    token = _active.set(current | {key})
    try:
        yield
    finally:
        _active.reset(token)
