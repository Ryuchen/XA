"""Serialize creation of an order aftersale with provider retirement."""
from functools import wraps
from django.conf import settings
from django.db.models import Q
from rest_framework.response import Response
from stores.db import transaction


def _provider_ids(order, scope):
    from .models import OrderParticipationSegment
    ids = set(order.providers.exclude(provider_account_id__isnull=True).values_list('provider_account_id', flat=True))
    ids.update(OrderParticipationSegment.objects.filter(attribution__order=order).exclude(
        provider_account_id__isnull=True).values_list('provider_account_id', flat=True))
    if order.provider_account_id:
        ids.add(order.provider_account_id)
    return ids if scope is None else ids.intersection(scope)


def protect_open(*, staff=False):
    def decorator(fn):
        @wraps(fn)
        def wrapped(view, request, *args, **kwargs):
            guarded = getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
            chat_id = request.data.get('chat_message_id') if staff else None
            if not guarded and chat_id is None:
                return fn(view, request, *args, **kwargs)
            from .models import Order
            raw_id = request.data.get('order_id') if staff else (kwargs.get('order_id') or (args[0] if args else None))
            try:
                order_id = int(raw_id)
            except (TypeError, ValueError, OverflowError):
                return fn(view, request, *args, **kwargs)
            if not 0 < order_id <= 9223372036854775807:
                return fn(view, request, *args, **kwargs)
            query = Order.objects.filter(pk=order_id)
            if not staff:
                owner = Q(customer_account=request.account)
                if request.legacy_user is not None:
                    owner |= Q(customer_account__isnull=True, customer=request.legacy_user)
                query = query.filter(owner)
            order = query.first()
            if not order:
                return fn(view, request, *args, **kwargs)
            chat_hint = None
            if chat_id is not None:
                from chat.aftersale_context import lookup
                chat_hint = lookup(chat_id)
            # Under the order lock these branches in the original entry are
            # read-only, preserving an already committed request's replay.
            if chat_hint is None:
                with transaction.atomic():
                    locked = query.select_for_update().first()
                    case = locked.aftersales.first() if locked else None
                    key = request.data.get('client_request_id')
                    prior = bool(case and key and ((not staff and case.client_request_id == key) or any(
                        isinstance(item, dict) and item.get('client_request_id') == key for item in (case.history or []))))
                    if case and (case.status == 'OPEN' or prior):
                        return fn(view, request, *args, **kwargs)
            raw_scope = request.data.get('affected_provider_account_ids')
            scope = (set(raw_scope) if isinstance(raw_scope, list) and raw_scope
                     and all(type(value) is int and value > 0 for value in raw_scope) else None)
            ids = _provider_ids(order, scope)
            from club_accounts.models import ClubAccount
            from club_accounts.provider_lifecycle import ProviderLifecycleError, ensure_provider_lifecycle, lock_provider_operations
            from site_messages.order_cycle import recipient_cycle
            accounts = list(ClubAccount.objects.filter(pk__in=ids, account_type=ClubAccount.AccountType.PROVIDER))
            try:
                if guarded and {account.pk for account in accounts} != ids:
                    raise ProviderLifecycleError('售后陪玩账号待核对')
                if guarded:
                    for account in accounts:
                        ensure_provider_lifecycle(account)
                with transaction.atomic():
                    if guarded:
                        lock_provider_operations(accounts)
                    context = None
                    if chat_hint is not None:
                        from chat.aftersale_context import lock_context
                        context = lock_context(chat_hint, chat_id, request.account)
                    locked = query.select_for_update().first()
                    if not locked or (guarded and _provider_ids(locked, scope) != ids):
                        raise ProviderLifecycleError('售后陪玩已变化')
                    for account in accounts if guarded else []:
                        cycle = recipient_cycle(locked, account.pk)
                        if (not cycle or cycle != account.business_cycle or not ClubAccount.objects.filter(
                                pk=account.pk, account_type=ClubAccount.AccountType.PROVIDER,
                                license_membership_active=True, business_cycle=cycle).exists()):
                            raise ProviderLifecycleError('售后陪玩来源周期待核对')
                    if context is not None:
                        from chat.aftersale_context import verify_order
                        request._consultation_aftersale_source = verify_order(context, locked, request.data.get('affected_provider_account_ids'))
                    return fn(view, request, *args, **kwargs)
            except ProviderLifecycleError:
                return Response({'code': 409, 'msg': '售后陪玩或来源周期待核对，请联系客服'}, status=409)
        return wrapped
    return decorator
