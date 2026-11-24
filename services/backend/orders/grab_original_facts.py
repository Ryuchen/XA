"""Read-only, evidence-aware original-price facts for future grab ranking.

This does not assign orders.  ``amount`` is deliberately unavailable if a
relevant historical order, refund, add-on or attribution cannot be verified.
The eventual dispatcher must obtain a consistent database snapshot and
revalidate the facts with its order/round and provider admission locks.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import Count, Q, Sum, Value

from users.models import EscortProfile
from .models import Order, OrderParticipationSegment, OrderProvider


@dataclass(frozen=True)
class OriginalAmountFact:
    amount: int | None
    verified_subtotal: int
    review_order_ids: tuple[int, ...]
    excess_refund_units: int | None = 0


def _verified_unstarted_transfers(account_ids, order_ids, *, as_of):
    """A single first handoff can prove its recipient has never started.

    This only excludes that person's contribution; it never supplies an
    original share or treats any started participation as zero.
    """
    from .models import OrderOriginalAttribution, OrderWorkStint
    from wallet.models import ProviderReport
    pending = OrderWorkStint.objects.filter(order_id__in=order_ids, account_id__in=account_ids,
        incoming__isnull=False, started_at__isnull=True, released_at__isnull=True,
        incoming__created_at__lte=as_of, incoming__sequence=1,
        incoming__seat__transfer_count=1, order__status=Order.Status.IN_SERVICE,
    ).select_related('incoming__seat')
    result = set()
    for work in pending:
        event, seat = work.incoming, work.incoming.seat
        if (seat.order_id != work.order_id or seat.current_account_id != work.account_id
                or event.new_account_id != work.account_id or event.old_account_id != seat.initial_account_id
                or seat.initial_account_id == work.account_id or seat.events.count() != 1
                or OrderOriginalAttribution.objects.filter(order_id=work.order_id).exists()):
            continue
        own = OrderWorkStint.objects.filter(order_id=work.order_id, account_id=work.account_id)
        if own.count() != 1:
            continue
        legacy_user = EscortProfile.objects.filter(account_id=work.account_id).values_list('user_id', flat=True).first()
        reports = ProviderReport.objects.filter(order_id=work.order_id).filter(
            Q(provider_account_id=work.account_id) | Q(provider_account__isnull=True, provider_id=legacy_user))
        if reports.exclude(entry_image='').exclude(entry_image__isnull=True).exists():
            continue
        previous = OrderWorkStint.objects.filter(order_id=work.order_id, account_id=event.old_account_id,
            started_at__isnull=False, started_at__lte=event.created_at, released_at__gte=event.created_at)
        if previous.count() == 1:
            result.add((work.order_id, work.account_id))
    return result


def original_amount_facts(account_ids, *, as_of, since=None, loaded_profiles=None):
    """Calculate 30-day started segments, preserving incomplete evidence.

    Acceptance time determines the 30-day window; a segment contributes only
    after its own actual start.  When a recorded transfer is later than as_of,
    its original pre-transfer share is restored for that historical instant.
    Complete ordinary-wallet refunds, single-player partial refunds, and
    multi-player negotiated refunds use their actual posted refund facts.
    Refunds without individual allocation remain unresolved.
    A caller that just loaded the profiles from this store can reuse them.
    """
    if not isinstance(as_of, datetime) or as_of.utcoffset() is None:
        raise ValueError('接单额统计时间必须包含时区')
    ids = set(account_ids)
    if any(type(pk) is not int or pk <= 0 for pk in ids):
        raise ValueError('陪玩账号无效')
    if loaded_profiles is None:
        profiles = dict(EscortProfile.objects.filter(account_id__in=ids).values_list('account_id', 'user_id'))
    else:
        from stores.context import current_database
        database = current_database()
        profiles = {}
        for profile in loaded_profiles:
            if profile._state.db != database or profile._state.adding:
                raise ValueError('陪玩账号或本店资料不存在')
            profiles[profile.account_id] = profile.user_id
    if set(profiles) != ids:
        raise ValueError('陪玩账号或本店资料不存在')
    if not ids:
        return {}
    if since is None:
        since = as_of - timedelta(days=30)
    elif not isinstance(since, datetime) or since.utcoffset() is None or since > as_of:
        raise ValueError('统计起始时间无效')
    subtotals = {pk: 0 for pk in ids}
    reviews = {pk: set() for pk in ids}
    excess_refunds = {pk: 0 for pk in ids}
    excess_unknown = set()
    # Account identity wins; legacy identity is only a fallback for NULL.
    legacy = {user_id: account_id for account_id, user_id in profiles.items()}
    segments = list(OrderParticipationSegment.objects.filter(
        provider_account_id__in=ids, accepted_at__gte=since, accepted_at__lte=as_of,
        started_at__isnull=False, started_at__lte=as_of,
    ).select_related('attribution__order', 'original_transfer_out__next_segment').order_by('pk'))
    order_ids = {segment.attribution.order_id for segment in segments}
    totals = dict(OrderParticipationSegment.objects.filter(
        Q(started_at__isnull=False) | Q(released_at__isnull=True), attribution__order_id__in=order_ids,
    ).values('attribution__order_id').annotate(total=Sum('original_share'))
                  .values_list('attribution__order_id', 'total'))
    refunded = set(Order.objects.filter(pk__in=order_ids, refund_records__created_at__lte=as_of)
                   .values_list('pk', flat=True))
    addons = set(Order.objects.filter(source_order_id__in=order_ids, is_gift_snapshot=False,
                                     created_at__lte=as_of).values_list('source_order_id', flat=True))
    from .grab_refunded_facts import negotiated_provider_burdens, verified_coin_refund, zero_refund_terminations
    zero_ended = zero_refund_terminations(order_ids, as_of=as_of)
    from .models import OrderOriginalShareTransfer
    from .team_rules import required_team_size
    counts = dict(OrderParticipationSegment.objects.filter(attribution__order_id__in=refunded, started_at__isnull=False)
                  .values('attribution__order_id').annotate(total=Count('pk'))
                  .values_list('attribution__order_id', 'total'))
    transferred = set(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order_id__in=refunded)
                      .values_list('previous_segment__attribution__order_id', flat=True))
    all_transferred = set(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order_id__in=order_ids,
        created_at__lte=as_of).values_list('previous_segment__attribution__order_id', flat=True))
    orders = {segment.attribution.order_id: segment.attribution.order for segment in segments}
    attributions = {segment.attribution.order_id: segment.attribution for segment in segments}
    verified_refunds = {}
    player_burdens = {}
    terminated = {}
    transferred_facts = {}
    from .grab_transferred_refunds import facts as transferred_refund_facts
    for order_id in all_transferred - addons:
        from .points_service_payment import needs_integrated_processing
        if not needs_integrated_processing(orders[order_id]):
            transferred_facts[order_id] = transferred_refund_facts(orders[order_id], as_of=as_of)
    for order_id in refunded - addons:
        order = orders[order_id]
        attribution = attributions[order_id]
        allowed_review = (attribution.status == 'REVIEW' and attribution.review_reason in (
            '退款后的个人原价份额尚未核对', '协商退款后原价份额待核对'))
        if ((attribution.status != 'ACTIVE' and not allowed_review) or order.is_gift_snapshot
                or attribution.original_amount != order.original_amount or attribution.original_amount < order.amount
                or attribution.original_amount % 10 or attribution.rounding_surplus not in (0, 10)
                or totals.get(order_id) != attribution.original_amount + attribution.rounding_surplus):
            continue
        from .points_service_payment import needs_integrated_processing
        points_order = needs_integrated_processing(order)
        if points_order:
            from .grab_points_refunded_facts import points_refund_fact
            fact = points_refund_fact(order, as_of=as_of)
        else:
            fact = verified_coin_refund(order, as_of=as_of)
        if order_id in transferred_facts:
            if fact and transferred_facts[order_id] is not None:
                verified_refunds[order_id] = fact
                window_segment_ids = {segment.pk for segment in segments}
                for account_id, amount, segment_ids in transferred_facts[order_id][2]:
                    if account_id in excess_refunds and window_segment_ids.intersection(segment_ids):
                        excess_refunds[account_id] += amount
            continue
        simple_single = counts.get(order_id) == 1 and required_team_size(order) == 1
        simple_double = counts.get(order_id) == 2 and required_team_size(order) == 2
        if fact and not fact.is_full and simple_double and order_id not in transferred:
            members = list(OrderParticipationSegment.objects.filter(attribution=attribution, started_at__isnull=False)
                           .values_list('provider_account_id', flat=True))
            if len(set(members)) == 2 and None not in members:
                if points_order:
                    from .grab_points_refunded_facts import points_burdens
                    allocation = points_burdens(order, fact.refund_ids, members)
                    if allocation is not None:
                        player_burdens[order_id], terminated[order_id] = allocation
                else:
                    allocation = negotiated_provider_burdens(order, fact.refund_ids, members, as_of=as_of)
                    if allocation is not None:
                        player_burdens[order_id], terminated[order_id] = allocation
        elif fact and order_id not in transferred and (simple_single or fact.is_full):
            members = list(OrderParticipationSegment.objects.filter(attribution=attribution, started_at__isnull=False)
                           .values_list('provider_account_id', flat=True))
            if points_order:
                from .grab_points_refunded_facts import points_burdens
                allocation = points_burdens(order, fact.refund_ids, members)
            else:
                allocation = negotiated_provider_burdens(order, fact.refund_ids, members, as_of=as_of)
            if allocation is not None:
                player_burdens[order_id], terminated[order_id] = allocation
        if fact and (fact.is_full or (simple_single and order_id not in transferred)
                     or order_id in player_burdens):
            verified_refunds[order_id] = fact
    for segment in segments:
        account_id, attribution = segment.provider_account_id, segment.attribution
        order = attribution.order
        refund = verified_refunds.get(order.pk)
        transferred_value = transferred_facts.get(order.pk)
        if order.pk in refunded and order.pk not in transferred_facts:
            # Other refund paths have not yet supplied a per-segment excess.
            excess_unknown.add(account_id)
        valid_refund_review = (refund and attribution.status == 'REVIEW' and attribution.review_reason in (
            '退款后的个人原价份额尚未核对', '协商退款后原价份额待核对'))
        if (order.is_gift_snapshot or (attribution.status != 'ACTIVE' and not valid_refund_review)
                or (order.payment_status != 'PAID' and not refund)
                or attribution.original_amount != order.original_amount or attribution.original_amount < order.amount
                or attribution.original_amount % 10 or attribution.rounding_surplus not in (0, 10)
                or totals.get(order.pk) != attribution.original_amount + attribution.rounding_surplus
                or (order.pk in refunded and not refund) or order.pk in addons
                or (order.pk in transferred_facts and transferred_value is None)
                or segment.started_at < segment.accepted_at or segment.original_share < 0):
            reviews[account_id].add(order.pk)
            continue
        try:
            event = segment.original_transfer_out
        except OrderParticipationSegment.original_transfer_out.RelatedObjectDoesNotExist:
            event = None
        share = segment.original_share
        if event:
            successor = event.next_segment
            if (event.created_at != segment.released_at or event.created_at < segment.started_at
                    or successor.attribution_id != segment.attribution_id or successor.slot != segment.slot
                    or successor.accepted_at != event.created_at or event.retained_share != segment.original_share
                    or event.original_share_before != event.retained_share + event.transferred_share):
                reviews[account_id].add(order.pk)
                continue
            if event.created_at > as_of:
                share = event.original_share_before
        elif (segment.released_at and segment.released_at <= as_of
              and (segment.pk not in transferred_value[1] if transferred_value is not None else
                   (terminated.get(order.pk, {}).get(account_id) != segment.released_at
                    and zero_ended.get(order.pk, {}).get(account_id) != segment.released_at))):
            # A started segment needs a transfer or an accepted scoped termination.
            reviews[account_id].add(order.pk)
            continue
        if refund:
            amount = (transferred_value[0].get(segment.pk, 0) if transferred_value is not None
                      else player_burdens.get(order.pk, {}).get(account_id, refund.refunded_units))
            share = 0 if refund.is_full else max(0, share - amount)
        subtotals[account_id] += share
    # Old assignments without a verifiable attribution cannot be silently
    # treated as zero. Use both explicit assignment and legacy primary fields.
    overlapping = (Q(order__in_service_at__gte=since) | Q(order__completed_at__gte=since)
                   | Q(order__cancelled_at__gte=since) | Q(released_at__gte=since)
                   | Q(order__status__in=('GRABBED', 'IN_SERVICE'))
                   | Q(order__completed_at__isnull=True, order__cancelled_at__isnull=True))
    assignments = OrderProvider.objects.filter(
        Q(provider_account_id__in=ids) | Q(provider_account__isnull=True, provider_id__in=legacy),
        overlapping, order__is_gift_snapshot=False,
        order__in_service_at__isnull=False, order__in_service_at__lte=as_of, created_at__lte=as_of,
    ).order_by().values_list('order_id', 'provider_account_id', 'provider_id',
                             'order__original_attribution__status', Value(False))
    primary_overlap = (Q(in_service_at__gte=since) | Q(completed_at__gte=since) | Q(cancelled_at__gte=since)
                       | Q(status__in=('GRABBED', 'IN_SERVICE'))
                       | Q(completed_at__isnull=True, cancelled_at__isnull=True))
    primary = Order.objects.filter(
        Q(provider_account_id__in=ids) | Q(provider_account__isnull=True, provider_id__in=legacy),
        primary_overlap, is_gift_snapshot=False,
        in_service_at__isnull=False, in_service_at__lte=as_of, created_at__lte=as_of,
    ).order_by().values_list('pk', 'provider_account_id', 'provider_id',
                             'original_attribution__status', Value(False))
    # Team entry can be real before the overall order reaches IN_SERVICE.
    # A legacy report has no trustworthy per-entry timestamp, so unresolved
    # report evidence must trigger review rather than being treated as zero.
    from wallet.models import ProviderReport
    reports = ProviderReport.objects.filter(
        Q(provider_account_id__in=ids) | Q(provider_account__isnull=True, provider_id__in=legacy),
        Q(created_at__gte=since) | Q(order__in_service_at__gte=since) | Q(order__completed_at__gte=since)
        | Q(order__cancelled_at__gte=since) | Q(order__status__in=('GRABBED', 'IN_SERVICE')),
        order__isnull=False, order__is_gift_snapshot=False, created_at__lte=as_of,
    ).exclude(entry_image='').exclude(entry_image__isnull=True).order_by().values_list(
        'order_id', 'provider_account_id', 'provider_id', 'order__original_attribution__status', Value(True))
    observations = list(assignments.union(primary, reports, all=True))
    observed_orders = {row[0] for row in observations}
    from .work_original_facts import verified_amounts
    independent_amounts, independent_pairs = verified_amounts(ids, since=since, as_of=as_of)
    for account_id, amount in independent_amounts.items():
        subtotals[account_id] += amount
    pairs = OrderParticipationSegment.objects.filter(
        attribution__order_id__in=observed_orders, provider_account_id__in=ids,
    ).values_list('attribution__order_id', 'provider_account_id', 'started_at')
    recorded_pairs, started_pairs = set(), set()
    for order_id, account_id, started_at in pairs:
        pair = (order_id, account_id)
        recorded_pairs.add(pair)
        if started_at is not None and started_at <= as_of:
            started_pairs.add(pair)
    unstarted_pairs = _verified_unstarted_transfers(ids, observed_orders, as_of=as_of)
    for order_id, account_id, user_id, status, is_report in observations:
        account_id = account_id or legacy.get(user_id)
        pair = (order_id, account_id)
        evidence = started_pairs if is_report else recorded_pairs
        if (account_id in reviews and pair not in unstarted_pairs and pair not in independent_pairs
                and ((status != 'ACTIVE' and order_id not in verified_refunds) or pair not in evidence)):
            reviews[account_id].add(order_id)
    # An accepted handover is a complete responsibility snapshot: only the
    # latest snapshot effective at as_of contributes, never all its versions.
    from .models import OrderAddonPayment
    addon_payments = list(OrderAddonPayment.objects.filter(
        Q(intent__provider_id__in=ids) | Q(handovers__shares__segment__provider_account_id__in=ids),
        paid_at__lte=as_of,
    ).select_related('intent__segment__attribution').distinct())
    transferred_addon_segments = set(OrderOriginalShareTransfer.objects.filter(
        previous_segment_id__in=[row.intent.segment_id for row in addon_payments], created_at__lte=as_of,
    ).values_list('previous_segment_id', flat=True))
    for payment in addon_payments:
        intent = payment.intent
        order_id = intent.order_id
        from .grab_addon_facts import addon_segment_facts
        facts = addon_segment_facts(payment, as_of)
        handover = payment.handovers.filter(status='ACCEPTED', accepted_at__lte=as_of).exists()
        if facts is None or (not handover and intent.segment_id in transferred_addon_segments):
            affected = {intent.provider_id}
            affected.update(payment.handovers.filter(status='ACCEPTED', accepted_at__lte=as_of)
                            .values_list('shares__segment__provider_account_id', flat=True))
            for account_id in affected & ids:
                reviews[account_id].add(order_id)
            continue
        for segment, amount in facts:
            account_id = segment.provider_account_id
            if (account_id in ids and since <= segment.accepted_at <= as_of
                    and segment.started_at and segment.started_at <= as_of):
                if segment.started_at < segment.accepted_at:
                    reviews[account_id].add(order_id)
                else:
                    subtotals[account_id] += amount
    return {pk: OriginalAmountFact(None if reviews[pk] else subtotals[pk], subtotals[pk],
                                   tuple(sorted(reviews[pk])), None if reviews[pk] or pk in excess_unknown
                                   else excess_refunds[pk]) for pk in sorted(ids)}
