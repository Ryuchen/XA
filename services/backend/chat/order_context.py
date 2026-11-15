"""Order cards are references to an authorized store order, never parsed text."""
from django.db.models import Q
from club_accounts.models import ClubAccount, LegacyAccountMap
from console.permissions import get_account_permissions, is_console_account


def owned_order_queryset(account, *, lock=False):
    from orders.models import Order
    if not account or not account.is_active or not account.can_login:
        return Order.objects.none()
    legacy_id = LegacyAccountMap.objects.filter(account=account).values_list('legacy_user_id', flat=True).first()
    if account.account_type == ClubAccount.AccountType.BOSS:
        owned = Q(customer_account=account)
        if legacy_id:
            owned |= Q(customer_account__isnull=True, customer_id=legacy_id)
    elif account.account_type == ClubAccount.AccountType.PROVIDER:
        # EXISTS avoids DISTINCT/FOR UPDATE complications and mirrors order detail.
        from orders.models import OrderProvider
        assignment = Q(provider_account=account)
        if legacy_id:
            assignment |= Q(provider_account__isnull=True, provider_id=legacy_id)
        memberships = OrderProvider.objects.filter(assignment).values('order_id')
        owned = Q(provider_account=account) | Q(pk__in=memberships)
        if legacy_id:
            owned |= Q(provider_account__isnull=True, provider_id=legacy_id)
    else:
        return Order.objects.none()
    orders = Order.objects.filter(owned)
    return orders.select_for_update() if lock else orders


def resolve_order(account, reference, *, lock=False):
    if not isinstance(reference, str) or not reference or len(reference) > 32:
        return None
    if reference.isascii() and reference.isdigit() and len(reference) <= 18 and int(reference) > 0:
        query = Q(pk=int(reference))
    elif len(reference) == 23 and reference[:3].upper() == 'ORD' and all(c in '0123456789ABCDEF' for c in reference[3:].upper()):
        query = Q(order_no=reference.upper())
    else:
        return None
    return chat_order_queryset(account, lock=lock).select_related('service').filter(query).first()


def visible_order_queryset(account, *, lock=False):
    from orders.models import Order
    owned = owned_order_queryset(account)
    if account and account.account_type == ClubAccount.AccountType.PROVIDER:
        from orders.pool_visibility import visible_provider_pool
        pool, _ = visible_provider_pool(account)
        orders = Order.objects.filter(Q(pk__in=owned.values('pk')) | Q(pk__in=pool.values('pk')))
    else:
        orders = owned
    return orders.select_for_update() if lock else orders


def participated_order_queryset(account):
    """Orders with a recorded actual start, including a provider transferred out."""
    from orders.models import Order, OrderParticipationSegment, OrderWorkStint
    if (not account or not account.is_active or not account.can_login
            or account.account_type != ClubAccount.AccountType.PROVIDER):
        return Order.objects.none()
    work = OrderWorkStint.objects.filter(account=account, started_at__isnull=False).values('order_id')
    segments = OrderParticipationSegment.objects.filter(
        provider_account=account, started_at__isnull=False).values('attribution__order_id')
    return Order.objects.filter(Q(pk__in=work) | Q(pk__in=segments))


def chat_order_queryset(account, *, lock=False):
    """Chat references add actual past participation, never pool/detail rights."""
    from orders.models import Order
    visible = visible_order_queryset(account)
    participated = participated_order_queryset(account)
    orders = Order.objects.filter(Q(pk__in=visible.values('pk')) | Q(pk__in=participated.values('pk')))
    return orders.select_for_update() if lock else orders


def order_snapshot(order):
    return {'id': order.pk, 'order_no': order.order_no,
            'service_name': order.service_name_snapshot or order.service.name,
            'status': order.status, 'status_display': order.get_status_display()}


def matches_message_order(reference, message):
    """Match a retry against its own original association, without granting access."""
    if not message.related_order_id:
        return reference == ''
    if reference.isascii() and reference.isdigit() and len(reference) <= 18:
        return int(reference) == message.related_order_id
    snapshot = message.order_snapshot if isinstance(message.order_snapshot, dict) else {}
    original_number = snapshot.get('order_no') or message.related_order.order_no
    return (len(reference) == 23 and reference[:3].upper() == 'ORD'
            and all(c in '0123456789ABCDEF' for c in reference[3:].upper())
            and reference.upper() == original_number)


def _provider_case_scope(case, order_id, account_id):
    """Return whether this case is provably in the participant's scope.

    The financial blocking helper deliberately treats malformed scope as
    affected; that conservative rule must not grant access to private data.
    """
    from orders.models import OrderParticipationSegment
    accounts = case.affected_provider_account_ids
    segments = case.affected_segment_ids
    for value in (accounts, segments):
        if value is not None and (not isinstance(value, list) or not value
                or any(type(item) is not int or item <= 0 for item in value)
                or len(set(value)) != len(value)):
            return False
    if accounts is not None and account_id not in accounts:
        return False
    if segments is None:
        return True
    rows = list(OrderParticipationSegment.objects.filter(
        attribution__order_id=order_id, pk__in=segments, started_at__isnull=False
    ).values_list('pk', 'provider_account_id'))
    if len(rows) != len(segments):
        return False
    if accounts is not None and {owner for _, owner in rows} != set(accounts):
        return False
    return any(owner == account_id for _, owner in rows)


def serialized_order_card(request, message, context=None):
    if not message.related_order_id:
        return None
    account = getattr(request, 'account', None)
    from .authorization import can_read_session
    cache = context.setdefault('_chat_order_card_access', {}) if context is not None else {}
    key = (message.session_id, message.related_order_id)
    if key not in cache:
        if not can_read_session(account, message.session):
            cache[key] = (False, False)
        elif account.account_type == ClubAccount.AccountType.STAFF:
            allowed = is_console_account(account) and 'order:view' in get_account_permissions(account)
            cache[key] = (allowed, allowed)
        else:
            # The order detail endpoint also admits the currently visible pool.
            # Re-evaluate it for each response; losing eligibility revokes access.
            can_open = visible_order_queryset(account).filter(pk=message.related_order_id).exists()
            allowed = can_open or participated_order_queryset(account).filter(pk=message.related_order_id).exists()
            cache[key] = (allowed, can_open)
    allowed, can_open = cache[key]
    if not allowed:
        return {'available': False}
    # The saved card describes what was sent. Its status must not be presented
    # as the current order status after the order has moved on.
    from orders.models import AfterSale, Order
    order = Order.objects.filter(pk=message.related_order_id).only('status').first()
    if order is None:
        return {'available': False}
    snapshot = dict(message.order_snapshot) if isinstance(message.order_snapshot, dict) else {}
    # Pool visibility alone does not establish participation. Provider access
    # is restricted to their own validated case scope, including segment scope.
    provider_participant = (account.account_type == ClubAccount.AccountType.PROVIDER
                            and (owned_order_queryset(account).filter(pk=order.pk).exists()
                                 or participated_order_queryset(account).filter(pk=order.pk).exists()))
    can_view_case = (account.account_type == ClubAccount.AccountType.STAFF
                     or (account.account_type == ClubAccount.AccountType.BOSS
                         and owned_order_queryset(account).filter(pk=order.pk).exists())
                     or provider_participant)
    case_data = {'available': False}
    if can_view_case:
        case = AfterSale.objects.filter(order_id=order.pk).only(
            'id', 'status', 'affected_provider_account_ids', 'affected_segment_ids').first()
        if case and provider_participant and not _provider_case_scope(case, order.pk, account.pk):
            case = None
        case_data = {'available': True, 'case': None if case is None else {
            'id': case.pk, 'status': case.status, 'status_display': case.get_status_display(),
        }}
    historical_detail_url = (f'/api/chat/messages/{message.pk}/participation/'
                             if provider_participant and not can_open else None)
    return {'available': True, 'can_open': can_open, 'participation_detail_url': historical_detail_url, **snapshot,
            'status': order.status, 'status_display': order.get_status_display(),
            'after_sale': case_data, 'snapshot': snapshot}
