"""Provider completion facts captured at the actual team order action."""
from django.db.models import Count, Exists, F, OuterRef, Q
from django.utils import timezone

from .models import (Order, OrderOriginalAttribution, OrderParticipationSegment,
                     OrderProvider, OrderProviderCompletionFact, OrderStatusLog, OrderTransferSeat,
                     OrderTransferSeatEvent, OrderWorkStint)


def capture_team_completion(order, account, image_name, *, at=None):
    """Called while the order is locked, after accepting this member's image."""
    if not image_name or not account or not order.pk:
        return
    segments = list(OrderParticipationSegment.objects.select_for_update().filter(
        attribution__order=order, attribution__status='ACTIVE', provider_account=account,
        released_at__isnull=True, started_at__isnull=False)[:2])
    if len(segments) > 1:
        return
    if segments:
        OrderProviderCompletionFact.objects.get_or_create(segment=segments[0], defaults={
            'order': order, 'provider_account': account, 'evidence': str(image_name),
            'completed_at': at or timezone.now()})
    elif not OrderOriginalAttribution.objects.filter(order=order).exists():
        # Without per-stint facts a later transfer makes this evidence
        # ambiguous; the reader deliberately excludes all such orders.
        OrderProviderCompletionFact.objects.get_or_create(order=order, provider_account=account,
            segment=None, defaults={'evidence': str(image_name), 'completed_at': at or timezone.now()})


def _single_transfers(start, now):
    """Recover only independently evidenced stints, without guessing entry."""
    orders = Order.objects.filter(escort_mode=Order.EscortMode.SINGLE, is_gift_snapshot=False,
        in_service_at__isnull=False, in_service_at__lte=now).filter(
        Q(completed_at__gte=start) | Q(providers__released_at__gte=start)
        | Q(completed_at__isnull=True, cancelled_at__gte=start)
        | Q(transfer_limit_seats__events__created_at__gte=start)).distinct().annotate(
        has_seat=Exists(OrderTransferSeat.objects.filter(order_id=OuterRef('pk'), transfer_count__gt=0)),
        has_segment=Exists(OrderParticipationSegment.objects.filter(attribution__order_id=OuterRef('pk'))),
        has_work=Exists(OrderWorkStint.objects.filter(order_id=OuterRef('pk')))
    ).filter(has_seat=True, has_segment=False, has_work=False)
    ends, known_orders, incomplete = {}, set(), False
    for order in orders:
        seats = list(OrderTransferSeat.objects.filter(order=order).prefetch_related('events'))
        if len(seats) != 1:
            incomplete = True
            continue
        seat = seats[0]
        events = sorted(seat.events.all(), key=lambda event: event.sequence)
        current = seat.initial_account_id
        valid = (len(events) == seat.transfer_count and 0 < len(events) <= 2 and seat.slot == 1)
        for sequence, event in enumerate(events, start=1):
            valid = valid and event.sequence == sequence and event.old_account_id == current and event.new_account_id != current
            if sequence > 1 and event.created_at < events[sequence - 2].created_at:
                valid = False
            current = event.new_account_id
        if not valid or current != seat.current_account_id or current != order.provider_account_id:
            incomplete = True
            continue
        logs = list(OrderStatusLog.objects.filter(order=order, action__in=(
            OrderStatusLog.Action.START, OrderStatusLog.Action.COMPLETE)).order_by('created_at', 'pk'))
        starts = [row for row in logs if row.action == OrderStatusLog.Action.START and
                  row.from_status == Order.Status.GRABBED and row.to_status == Order.Status.IN_SERVICE]
        finishes = [row for row in logs if row.action == OrderStatusLog.Action.COMPLETE and
                    row.from_status == Order.Status.IN_SERVICE and row.to_status == Order.Status.COMPLETED]
        if len(starts) != 1 or not starts[0].operator_account_id or starts[0].created_at < order.in_service_at:
            incomplete = True
            continue
        started = starts[0]
        if any(event.created_at == started.created_at for event in events):
            incomplete = True
            continue
        account = seat.initial_account_id
        for event in events:
            if event.created_at < started.created_at:
                account = event.new_account_id
        if account != started.operator_account_id:
            incomplete = True
            continue
        service_events = [event for event in events if event.created_at > started.created_at]
        if not service_events:
            if (account == current and order.completed_at and order.completed_at <= now and
                    len(finishes) == 1 and finishes[0].operator_account_id == current and
                    finishes[0].created_at >= order.completed_at):
                ends[(order.pk, current)] = order.completed_at
                known_orders.add(order.pk)
            else:
                incomplete = True
            continue
        if service_events[0].old_account_id != account:
            incomplete = True
            continue
        assignments = {row.provider_account_id: row for row in OrderProvider.objects.filter(order=order)}
        verified = set()
        completion = (order.completed_at if order.completed_at and events[-1].created_at <= order.completed_at <= now and
                      len(finishes) == 1 and finishes[0].operator_account_id == current and
                      finishes[0].created_at >= order.completed_at else None)
        if account == current:
            # A returned participant's latest known end is their actual finish;
            # the original assignment's released_at has been cleared.
            if completion:
                ends[(order.pk, account)] = completion
                verified.add(account)
        else:
            assignment = assignments.get(account)
            released = assignment.released_at if assignment else None
            if released and service_events[0].created_at <= released <= now:
                ends[(order.pk, account)] = released
                verified.add(account)
        if completion:
            ends[(order.pk, current)] = completion
            verified.add(current)
        participants = {service_events[0].old_account_id} | {event.new_account_id for event in service_events}
        if participants <= verified:
            known_orders.add(order.pk)
        else:
            incomplete = True
    return ends, known_orders, incomplete


def counts(start, now):
    segments = OrderParticipationSegment.objects.filter(started_at__isnull=False,
        started_at__lte=now, attribution__order__is_gift_snapshot=False).values_list(
        'attribution__order_id', 'provider_account_id', 'released_at',
        'attribution__order__completed_at', 'attribution__order__cancelled_at',
        'completion_fact__completed_at', 'attribution__order__escort_mode')
    ends, active, complete = {}, set(), True
    for order_id, account_id, released, completed, cancelled, personal, mode in segments:
        key = (order_id, account_id)
        # Team completion belongs to each person. Missing personal evidence
        # must not be replaced by the other player's eventual order finish.
        end = (released or personal or (completed if mode != Order.EscortMode.DOUBLE else None)
               or (cancelled if not completed else None))
        if (mode == Order.EscortMode.DOUBLE and not released and not personal
                and completed and start <= completed <= now):
            complete = False
        if released and personal:
            end = max(released, personal)
        if end is None or end > now:
            active.add(key)
        elif key not in ends or end > ends[key]:
            ends[key] = end

    work = list(OrderWorkStint.objects.filter(started_at__isnull=False, started_at__lte=now,
        order__is_gift_snapshot=False).values_list('order_id', 'account_id', 'released_at', 'completed_at',
        'order__completed_at', 'order__cancelled_at', 'order__escort_mode'))
    work_orders = {row[0] for row in work}
    for order_id, account_id, released, personal, completed, cancelled, mode in work:
        key = (order_id, account_id)
        end = (released or personal or (completed if mode != Order.EscortMode.DOUBLE else None)
               or (cancelled if not completed else None))
        if (mode == Order.EscortMode.DOUBLE and not released and not personal
                and completed and start <= completed <= now):
            complete = False
        if released and personal:
            end = max(released, personal)
        if end is None or end > now:
            active.add(key)
        elif key not in ends or end > ends[key]:
            ends[key] = end

    # These facts were written by the team completion endpoint, not inferred
    # from a mutable report image. Exclude transfers until per-stint evidence
    # exists for them.
    transfers = OrderStatusLog.objects.filter(order_id=OuterRef('order_id'), action=OrderStatusLog.Action.TRANSFER)
    unsegmented = OrderProviderCompletionFact.objects.filter(segment__isnull=True,
        completed_at__lte=now, order__is_gift_snapshot=False).annotate(
        has_transfer=Exists(transfers), has_segment=Exists(OrderParticipationSegment.objects.filter(
            attribution__order_id=OuterRef('order_id')))).filter(has_transfer=False, has_segment=False)
    proven_team = {}
    for order_id, account_id, completed in unsegmented.values_list('order_id', 'provider_account_id', 'completed_at'):
        if order_id in work_orders:
            continue
        ends[(order_id, account_id)] = completed
        proven_team.setdefault(order_id, set()).add(account_id)

    transferred_ends, proven_transferred, transfer_incomplete = _single_transfers(start, now)
    ends.update(transferred_ends)
    complete = complete and not transfer_incomplete
    # With original-price tracking disabled, service-time team transfers lack
    # per-person entry facts even while the order is still in progress.
    unknown_team_transfers = OrderTransferSeatEvent.objects.filter(
        seat__order__escort_mode=Order.EscortMode.DOUBLE, seat__order__is_gift_snapshot=False,
        seat__order__in_service_at__isnull=False, created_at__gt=F('seat__order__in_service_at'),
        created_at__lte=now).filter(Q(created_at__gte=start)
        | Q(seat__order__providers__released_at__gte=start)
        | Q(seat__order__provider_completion_facts__completed_at__gte=start)
        | Q(seat__order__completed_at__gte=start)).annotate(has_segment=Exists(
        OrderParticipationSegment.objects.filter(attribution__order_id=OuterRef('seat__order_id'))),
        has_work=Exists(OrderWorkStint.objects.filter(order_id=OuterRef('seat__order_id')))
    ).filter(has_segment=False, has_work=False)
    if unknown_team_transfers.exists():
        complete = False
    missing_transfer_history = OrderTransferSeatEvent.objects.filter(
        seat__order__is_gift_snapshot=False, seat__order__in_service_at__isnull=False,
        created_at__gt=F('seat__order__in_service_at'), created_at__lte=now).filter(
        Q(created_at__gte=start) | Q(seat__order__completed_at__gte=start)
        | Q(seat__order__providers__released_at__gte=start)).annotate(
        has_work=Exists(OrderWorkStint.objects.filter(order_id=OuterRef('seat__order_id'))),
        has_departure=Exists(OrderWorkStint.objects.filter(order_id=OuterRef('seat__order_id'),
            account_id=OuterRef('old_account_id'), released_at__gte=OuterRef('created_at')))
    ).filter(has_work=True, has_departure=False)
    if missing_transfer_history.exists():
        complete = False

    # With attribution disabled, a single player who personally started and
    # completed a never-transferred order is still independently provable.
    start_logs = OrderStatusLog.objects.filter(order_id=OuterRef('pk'), action=OrderStatusLog.Action.START,
        from_status=Order.Status.GRABBED, to_status=Order.Status.IN_SERVICE,
        operator_account_id=OuterRef('provider_account_id'))
    finish_logs = OrderStatusLog.objects.filter(order_id=OuterRef('pk'), action=OrderStatusLog.Action.COMPLETE,
        from_status=Order.Status.IN_SERVICE, to_status=Order.Status.COMPLETED,
        operator_account_id=OuterRef('provider_account_id'))
    order_transfers = OrderStatusLog.objects.filter(order_id=OuterRef('pk'), action=OrderStatusLog.Action.TRANSFER)
    matching = OrderProvider.objects.filter(order_id=OuterRef('pk'), provider_account_id=OuterRef('provider_account_id'),
        provider_id=OuterRef('provider_id'), released_at__isnull=True)
    singles = Order.objects.filter(escort_mode=Order.EscortMode.SINGLE, is_gift_snapshot=False,
        completed_at__gte=start, completed_at__lte=now, in_service_at__isnull=False,
        provider_account_id__isnull=False, provider_id__isnull=False).annotate(
        provider_count=Count('providers', distinct=True), has_matching=Exists(matching),
        has_start=Exists(start_logs), has_finish=Exists(finish_logs), has_transfer=Exists(order_transfers),
        has_segment=Exists(OrderParticipationSegment.objects.filter(attribution__order_id=OuterRef('pk'))),
        has_work=Exists(OrderWorkStint.objects.filter(order_id=OuterRef('pk')))).filter(
        provider_count=1, has_matching=True, has_start=True, has_finish=True, has_transfer=False, has_segment=False, has_work=False)
    proven_single = set()
    for order_id, account_id, completed, began in singles.values_list('pk', 'provider_account_id', 'completed_at', 'in_service_at'):
        if began <= completed:
            ends[(order_id, account_id)] = completed
            proven_single.add(order_id)
    unresolved = Order.objects.filter(is_gift_snapshot=False, in_service_at__isnull=False).filter(
        Q(completed_at__gte=start, completed_at__lte=now)
        | Q(completed_at__isnull=True, cancelled_at__gte=start, cancelled_at__lte=now)).annotate(
        has_segment=Exists(OrderParticipationSegment.objects.filter(attribution__order_id=OuterRef('pk')))
    ).filter(has_segment=False).values_list('pk', 'escort_mode')
    for order_id, mode in unresolved:
        if order_id in proven_single or order_id in proven_transferred or order_id in work_orders:
            continue
        participants = set(OrderProvider.objects.filter(order_id=order_id, released_at__isnull=True)
                           .values_list('provider_account_id', flat=True))
        if (mode == Order.EscortMode.DOUBLE and len(participants) == 2 and None not in participants
                and participants == proven_team.get(order_id, set())):
            continue
        complete = False
    result = {}
    for key, end in ends.items():
        if key not in active and start <= end <= now:
            result[key[1]] = result.get(key[1], 0) + 1
    return result, complete
