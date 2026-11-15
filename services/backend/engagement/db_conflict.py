"""Expose transactional MySQL lock conflicts as safely retryable API errors."""
from functools import wraps
from django.db import OperationalError
from rest_framework.exceptions import APIException
from stores.db import transaction


class StoreLockConflict(APIException):
    status_code = 503
    default_code = 'STORE_LOCK_CONFLICT'
    default_detail = '操作与其他业务发生并发冲突，请重试；如有请求编号请保持不变'


def api_lock_conflict(view):
    """Called outside the view's atomic block; never retry in a nested transaction."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except OperationalError as exc:
            if not exc.args or exc.args[0] not in (1205, 1213):
                raise
            # If a deployment wraps the view in another tenant transaction,
            # that entire transaction must be rolled back as well.
            if transaction.get_connection().in_atomic_block:
                transaction.set_rollback(True)
            raise StoreLockConflict() from exc
    return wrapped
