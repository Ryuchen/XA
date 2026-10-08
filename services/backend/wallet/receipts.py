"""Recover a committed funds operation without executing it again.

Receipts are written inside the debit transaction. Old keys without a receipt
remain conflicts: never guess a matching order from its amount or timestamp.
"""
import hashlib
import json
from functools import wraps

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response

from .models import IdempotencyKey
from .services import DERIVED_KEY_PREFIX, normalize_request_id


def _fingerprint(request):
    payload = {key: value for key, value in request.data.items() if key != 'client_request_id'}
    raw = json.dumps(
        {'path': request.path, 'payload': payload},
        sort_keys=True, separators=(',', ':'), ensure_ascii=False, cls=DjangoJSONEncoder,
    )
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _replay(request, scope):
    request_id = normalize_request_id(request.data.get('client_request_id'))
    if not request_id or request_id.startswith(DERIVED_KEY_PREFIX):
        return None
    receipt = IdempotencyKey.objects.filter(account=request.account, request_id=request_id).first()
    if receipt is None:
        return None
    if receipt.scope != scope or (
        receipt.request_fingerprint and receipt.request_fingerprint != _fingerprint(request)
    ):
        return Response({'code': 409, 'errorCode': 'REQUEST_ID_CONFLICT',
                         'msg': '交易编号已用于其他操作，请核对原交易记录'})
    if not receipt.request_fingerprint or receipt.response is None:
        return Response({'code': 409, 'errorCode': 'FUND_RESULT_UNAVAILABLE',
                         'msg': '该请求已处理，请在订单或资金记录中核对结果'})
    return Response({'code': 409, 'errorCode': 'FUND_ALREADY_PROCESSED',
                     'msg': '已恢复上次操作结果，本次未重复扣款', 'fundResult': receipt.response})


def recover_fund_result(scope):
    """Check before mutable business rules and after a competing request wins."""
    def decorate(view):
        @wraps(view)
        def wrapped(self, request, *args, **kwargs):
            replay = _replay(request, scope)
            if replay is not None:
                return replay
            result = view(self, request, *args, **kwargs)
            if isinstance(result.data, dict) and result.data.get('code') != 0:
                # Another request may have committed while this one waited for
                # wallet/profile locks, changing its balance or eligibility.
                replay = _replay(request, scope)
                if replay is not None:
                    return replay
            return result
        return wrapped
    return decorate


def fund_success(request, scope, *, data, msg):
    """Call before leaving the same atomic block as the funds write."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('Funds receipt must be saved in the debit transaction')
    body = json.loads(JSONRenderer().render({'code': 0, 'msg': msg, 'data': data}))
    request_id = normalize_request_id(request.data.get('client_request_id'))
    if request_id and not request_id.startswith(DERIVED_KEY_PREFIX):
        updated = IdempotencyKey.objects.filter(
            account=request.account, request_id=request_id, scope=scope, response__isnull=True,
        ).update(request_fingerprint=_fingerprint(request), response=body)
        if updated != 1:
            raise RuntimeError('Funds receipt has no unique claimed request')
    return Response(body)
