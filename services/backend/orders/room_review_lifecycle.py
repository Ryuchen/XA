"""Serialize room completion requests and reviews with provider retirement."""
from functools import wraps

from django.conf import settings
from rest_framework.response import Response
from stores.db import transaction


def _run(fn, view, request, order_id, args, kwargs, replay):
    from club_accounts.provider_lifecycle import ProviderLifecycleError
    from engagement.settlement_lifecycle import capture, lock, verify
    try:
        accounts = capture(order_id)
    except (ProviderLifecycleError, ValueError):
        result = replay()
        if result is not None:
            return result
        return Response({'code': 409, 'msg': '结单陪玩或原业务周期待核对'}, status=409)
    with transaction.atomic():
        try:
            lock(accounts)
            verify(order_id, accounts)
        except (ProviderLifecycleError, ValueError):
            result = replay()
            if result is not None:
                return result
            return Response({'code': 409, 'msg': '结单陪玩或原业务周期待核对'}, status=409)
        return fn(view, request, *args, **kwargs)


def submit(fn):
    @wraps(fn)
    def wrapped(view, request, order_id):
        from .models import Order, OrderRoomCompletionRequest
        if (not getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
                or not getattr(settings, 'ROOM_COMPLETION_REVIEW_ENABLED', False)
                or type(order_id) is not int or not 0 < order_id <= 9223372036854775807):
            return fn(view, request, order_id)
        order = Order.objects.filter(pk=order_id).first()
        if not order:
            return fn(view, request, order_id)
        from .rooms import message_role
        if message_role(order, request.account) != 'PROVIDER':
            return fn(view, request, order_id)
        # Existing request keys are a read-only branch in the original view.
        key = request.data.get('client_request_id') if isinstance(request.data, dict) else None
        def replay():
            if not isinstance(key, str) or not key:
                return None
            with transaction.atomic():
                Order.objects.select_for_update().filter(pk=order_id).first()
                if OrderRoomCompletionRequest.objects.filter(room__order_id=order_id,
                        submitted_by=request.account, request_id=key).exists():
                    return fn(view, request, order_id)
            return None
        result = replay()
        if result is not None:
            return result
        return _run(fn, view, request, order_id, (order_id,), {}, replay)
    return wrapped


def review(fn):
    @wraps(fn)
    def wrapped(view, request, completion_id):
        from .models import Order, OrderRoomCompletionRequest
        if (not getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
                or not getattr(settings, 'ROOM_COMPLETION_REVIEW_ENABLED', False)
                or type(completion_id) is not int or not 0 < completion_id <= 9223372036854775807):
            return fn(view, request, completion_id)
        from .room_objections import _authorized
        if not _authorized(request.account):
            return fn(view, request, completion_id)
        initial = OrderRoomCompletionRequest.objects.filter(pk=completion_id).values('room__order_id').first()
        if not initial:
            return fn(view, request, completion_id)
        order_id = initial['room__order_id']
        # A reviewed request is terminal; preserve the original replay and
        # conflict responses without requiring a still-active membership.
        def replay():
            with transaction.atomic():
                Order.objects.select_for_update().filter(pk=order_id).first()
                row = OrderRoomCompletionRequest.objects.filter(pk=completion_id, room__order_id=order_id).first()
                if row and row.status != 'PENDING':
                    return fn(view, request, completion_id)
            return None
        result = replay()
        if result is not None:
            return result
        return _run(fn, view, request, order_id, (completion_id,), {}, replay)
    return wrapped
