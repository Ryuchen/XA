"""Serialize real room completion with provider membership retirement."""
from functools import wraps

from django.conf import settings
from rest_framework.response import Response
from stores.db import transaction


def boss_confirmation(fn):
    @wraps(fn)
    def wrapped(view, request, order_id, completion_id):
        from .models import Order
        if (not getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
                or not getattr(settings, 'ROOM_COMPLETION_REVIEW_ENABLED', False)
                or not getattr(settings, 'ROOM_BOSS_CONFIRMATION_ENABLED', False)
                or type(order_id) is not int or not 0 < order_id <= 9223372036854775807
                or not Order.objects.filter(pk=order_id, customer_account=request.account,
                                            status=Order.Status.IN_SERVICE).exists()):
            return fn(view, request, order_id, completion_id)
        from club_accounts.provider_lifecycle import ProviderLifecycleError
        from engagement.settlement_lifecycle import capture, lock, verify
        try:
            accounts = capture(order_id)
        except (ProviderLifecycleError, ValueError):
            if Order.objects.filter(pk=order_id, customer_account=request.account,
                                    status=Order.Status.COMPLETED).exists():
                return fn(view, request, order_id, completion_id)
            return Response({'code': 409, 'msg': '结单陪玩或来源周期待核对'}, status=409)
        with transaction.atomic():
            try:
                lock(accounts)
                if not Order.objects.filter(pk=order_id, status=Order.Status.IN_SERVICE).exists():
                    return fn(view, request, order_id, completion_id)
                verify(order_id, accounts)
            except (ProviderLifecycleError, ValueError):
                return Response({'code': 409, 'msg': '结单陪玩或来源周期待核对'}, status=409)
            return fn(view, request, order_id, completion_id)
    return wrapped


def automatic_confirmation(fn):
    @wraps(fn)
    def wrapped(wait_id):
        if (not getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
                or not getattr(settings, 'ROOM_COMPLETION_REVIEW_ENABLED', False)
                or not getattr(settings, 'ROOM_BOSS_CONFIRMATION_ENABLED', False)
                or not getattr(settings, 'ROOM_AUTO_CONFIRMATION_ENABLED', False)):
            return fn(wait_id)
        from .models import OrderRoomConfirmation
        order_id = OrderRoomConfirmation.objects.filter(pk=wait_id, status='RUNNING').values_list(
            'request__room__order_id', flat=True).first()
        if not order_id:
            return fn(wait_id)
        from club_accounts.provider_lifecycle import ProviderLifecycleError
        from engagement.settlement_lifecycle import capture, lock, verify
        from .room_auto_confirmation import mark_lifecycle_review
        try:
            accounts = capture(order_id)
        except (ProviderLifecycleError, ValueError):
            return mark_lifecycle_review(wait_id)
        with transaction.atomic():
            try:
                lock(accounts)
                if not OrderRoomConfirmation.objects.filter(pk=wait_id, status='RUNNING').exists():
                    return fn(wait_id)
                verify(order_id, accounts)
            except (ProviderLifecycleError, ValueError):
                # Release lifecycle locks before entering the normal order ->
                # room -> confirmation review lock sequence.
                pass
            else:
                return fn(wait_id)
        return mark_lifecycle_review(wait_id)
    return wrapped
