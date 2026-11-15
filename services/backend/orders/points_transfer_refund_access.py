"""Transaction-scoped capability for the narrow MySQL points-transfer path.

This grants no user permission and does not by itself expose an endpoint.
Each business entry must authenticate its actor and enter its own phase after
locking its order. The currently implemented phase is transfer capture only.
"""
from contextlib import contextmanager

from django.conf import settings
from rest_framework.exceptions import ValidationError

from stores.context import current_store
from stores.db import transaction


def permits(order=None, *, phase=None):
    from .points_business_access import permits as business_permits
    return business_permits(order, phase=phase, kind='transfer')


def has_paid_addon_before(order, boundary):
    """Use the actual payment time when rebuilding an earlier refund."""
    from .models import OrderAddonPayment
    payments = OrderAddonPayment.objects.filter(intent__order=order)
    if not boundary or payments.filter(buyer_transaction__created_at=boundary).exists():
        raise ValidationError('加购付款与主单退款时间无法区分')
    return payments.filter(buyer_transaction__created_at__lt=boundary).exists()


def _pending_allowed(payment_id, allowed):
    return payment_id in allowed if isinstance(allowed, (set, frozenset, tuple, list)) else payment_id == allowed


def _mixed_followup_payments(order, *, pending_intent_id=None, pending_payment_id=None):
    """Recognize old and new payments; the caller verifies both event chains."""
    from .models import OrderAddonPayment, OrderAddonFundingReleaseRun, PointsNegotiationPosting
    postings = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order)
                    .order_by('points_refund__refund_id')[:3])
    if len(postings) != 2 or order.refund_records.count() != 2:
        return False
    second = postings[1]
    payments = list(OrderAddonPayment.objects.filter(intent__order=order).select_related('buyer_transaction'))
    if any(not row.buyer_transaction.created_at or row.buyer_transaction.created_at == second.created_at
           for row in payments):
        return False
    unpaid = set(order.addon_intents.exclude(pk__in=[row.intent_id for row in payments]).values_list('pk', flat=True))
    # Intents carry no paid funds. Only the intent actually being paid must
    # still be unpaid; unrelated intents are not part of the funding history.
    if pending_intent_id is not None and pending_intent_id not in unpaid:
        return False
    new = [row for row in payments if row.buyer_transaction.created_at > second.created_at]
    if not new and pending_intent_id is None:
        return False
    for payment in payments:
        for handover in payment.handovers.all():
            if handover.status == 'PENDING':
                if not _pending_allowed(payment.pk, pending_payment_id):
                    return False
            elif handover.status == 'ACCEPTED':
                if not handover.accepted_at or handover.accepted_at == second.created_at:
                    return False
                runs = OrderAddonFundingReleaseRun.objects.filter(handover=handover)
                if handover.accepted_at > second.created_at:
                    runs = runs.filter(points_followup_snapshot__kind='TRANSFER_FOLLOWUP_V1',
                                       points_followup_snapshot__second_posting_id=second.pk)
                if not runs.exists():
                    return False
            elif handover.status in ('REJECTED', 'SUPERSEDED'):
                if (handover.kind not in ('STANDARD', 'VOLUNTARY') or not handover.created_at
                        or handover.created_at == second.created_at):
                    return False
            else:
                return False
    return all(OrderAddonFundingReleaseRun.objects.filter(payment=row,
        points_followup_snapshot__kind='TRANSFER_FOLLOWUP_V1',
        points_followup_snapshot__second_posting_id=second.pk,
        points_followup_snapshot__addon_payment_id=row.pk).exists() for row in new)


def _origin_addon_history(order, *, pending_payment_id=None, pending_intent_id=None, allow_voluntary=False):
    """Recognize the shape only; the bound scope verifies every event."""
    from django.utils import timezone
    from .models import (OrderAddonPayment, OrderAddonFundingReleaseRun, OrderOriginalShareTransfer,
                         PointsNegotiationPosting, PointsPostRefundTransferRevision)
    postings = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order)
                    .order_by('points_refund__refund_id')[:3])
    if ((pending_payment_id and not allow_voluntary) or (pending_intent_id and not allow_voluntary)
            or len(postings) not in (1, 2)
            or order.refund_records.count() != len(postings)):
        return False
    first = postings[0]
    boundary = postings[1].created_at if len(postings) == 2 else timezone.now()
    payments = list(OrderAddonPayment.objects.filter(intent__order=order).select_related('buyer_transaction'))
    unpaid_ids = set(order.addon_intents.exclude(pk__in=[item.intent_id for item in payments]).values_list('pk', flat=True))
    if (not payments or (pending_intent_id is not None and pending_intent_id not in unpaid_ids)
            or PointsPostRefundTransferRevision.objects.filter(order=order, created_at__lt=boundary).exists()
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
                created_at__gte=first.created_at, created_at__lte=boundary).exists()
            or any(not item.buyer_transaction.created_at or
                   (item.buyer_transaction.created_at >= first.created_at and
                    (not allow_voluntary or not OrderAddonFundingReleaseRun.objects.filter(payment=item).exists()))
                   for item in payments)):
        return False
    for payment in payments:
        handovers = list(payment.handovers.all())
        if any(not (item.status == 'ACCEPTED' and item.accepted_at and item.accepted_at < first.created_at)
               and not (allow_voluntary and item.kind == 'VOLUNTARY'
                        and item.status in ('ACCEPTED', 'REJECTED', 'SUPERSEDED', 'PENDING')
                        and (item.status != 'PENDING' or _pending_allowed(payment.pk, pending_payment_id)))
               for item in handovers):
            return False
    return any((allow_voluntary and first.created_at < item.buyer_transaction.created_at < boundary)
               or item.refund_records.filter(created_at__gt=first.created_at, created_at__lt=boundary).exists()
               or (allow_voluntary and item.handovers.filter(kind='VOLUNTARY', status='ACCEPTED',
                   accepted_at__gt=first.created_at, accepted_at__lt=boundary).exists()) for item in payments)


def require_refund_or_isolated():
    """Only the bound refund phase can replace the isolated-test gate."""
    if permits(phase='refund'):
        return
    from .points_checkout_test import require_isolated_checkout
    require_isolated_checkout()


def _prestart_slot_counts(order, handoff):
    from .models import OrderParticipationSegment, OrderPrestartHandoff
    from .points_prestart_release import capture
    capture(order, handoff)
    segments = list(OrderParticipationSegment.objects.filter(attribution__order=order).order_by('pk'))
    slots = {segment.slot for segment in segments}
    chains = {slot: [segment for segment in segments if segment.slot == slot] for slot in slots}
    positions = {slot: 0 for slot in slots}
    counts = {slot: 0 for slot in slots}
    for event in OrderPrestartHandoff.objects.filter(order=order, pk__lte=handoff.pk).order_by('pk'):
        matches = [slot for slot in slots if positions[slot] < len(chains[slot])
                   and chains[slot][positions[slot]].provider_account_id == event.old_provider_id]
        if len(matches) != 1 or positions[matches[0]] + 1 >= len(chains[matches[0]]):
            raise ValidationError('改派席位历史待核对')
        slot = matches[0]
        positions[slot] += 1
        counts[slot] += 1
    return counts


def independent_refund_after_handovers(order, transfers):
    """Narrow event order: handovers complete before refund of another add-on."""
    from .models import OrderAddonPayment
    payments = list(OrderAddonPayment.objects.filter(intent__order=order).select_related('buyer_transaction'))
    if len(payments) < 2 or not transfers or order.refund_records.exists():
        return False
    accepted = [item for payment in payments for item in payment.handovers.filter(status='ACCEPTED')]
    if (not accepted or any(payment.handovers.exclude(status='ACCEPTED').exists()
            or payment.handovers.filter(status='ACCEPTED').count() > 1 for payment in payments)):
        return False
    latest = max(item.accepted_at for item in accepted if item.accepted_at) if all(
        item.accepted_at for item in accepted) else None
    if not latest:
        return False
    return all(not payment.refund_records.exists() if payment.handovers.exists() else
        not payment.refund_records.filter(created_at__lte=latest).exists() for payment in payments)


def first_handover_refund(order, transfers, *, pending_payment_id=None):
    """One handover with ordered independent add-on refund events."""
    from .models import OrderAddonPayment, OrderAddonRefund
    if not transfers or order.refund_records.count() > 1:
        return False
    payments = list(OrderAddonPayment.objects.filter(intent__order=order).select_related('buyer_transaction'))
    handovers = [row for payment in payments for row in payment.handovers.all()]
    refunds = list(OrderAddonRefund.objects.filter(order=order).order_by('created_at', 'pk'))
    accepted = [row for row in handovers if row.status == 'ACCEPTED']
    pending = [row for row in handovers if row.status == 'PENDING']
    inactive = [row for row in handovers if row.status in ('REJECTED', 'SUPERSEDED')]
    if (not payments or not accepted or len(pending) > 1
            or len(handovers) != len(accepted) + len(pending) + len(inactive)
            or any(row.previous_id or not row.accepted_at for row in accepted)
            or (pending and (not _pending_allowed(pending[0].payment_id, pending_payment_id) or pending[0].previous_id))):
        return False
    main_refund = order.refund_records.first()
    first = min(accepted, key=lambda row: row.accepted_at)
    if main_refund and (main_refund.created_at <= first.accepted_at
                        or any(row.created_at == main_refund.created_at for row in refunds)):
        return False
    extra = [row for row in accepted if row.pk != first.pk] + pending
    revision = None
    invalidated_retry = False
    if (len(accepted) == 1 and not pending and inactive and not accepted[0].previous_id
            and all(row.status == 'SUPERSEDED' and row.payment_id == first.payment_id for row in inactive)
            and [row.version for row in sorted(inactive + accepted, key=lambda item: item.version)]
                == list(range(1, first.version + 1))):
        from .models import PointsHandoverRefundInvalidation
        from .points_handover_refund_invalidation import capture, digest
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            records = [PointsHandoverRefundInvalidation.objects.select_related(
                'handover__payment__intent', 'refund').get(handover=row) for row in inactive]
            invalidated_retry = bool(records and all(item.refund.created_at < first.created_at
                and item.evidence_hash == digest(item.snapshot) and capture(item.handover, item.refund) == item.snapshot
                for item in records))
        except (PointsHandoverRefundInvalidation.DoesNotExist, DjangoValidationError, ValidationError,
                TypeError, ValueError):
            invalidated_retry = False
    independent_premain = False
    if len(payments) >= 2 and not inactive and len(accepted) + len(pending) <= len(payments):
        from .points_transfer_pending_handover_events import is_unaffected
        active = accepted + pending
        starts = [item.created_at for item in active]
        done = [item.accepted_at for item in accepted]
        independent_premain = bool(all(starts) and all(done) and len(set(done)) == len(done)
            and len({item.payment_id for item in active}) == len(active)
            and all(any(item.previous_segment_id == handover.source_segment_id
                        and item.created_at < handover.created_at for item in transfers) for handover in active)
            and all(not (item.created_at <= row.created_at <= item.accepted_at) or is_unaffected(row, item)
                    for row in refunds for item in accepted)
            and all(row.created_at < item.created_at or (row.created_at > item.created_at
                    and is_unaffected(row, item)) for row in refunds for item in pending)
            and (not main_refund or (not pending and main_refund.created_at > max(done))))
    if len(accepted) > 2 and not independent_premain:
        return False
    if (extra or inactive) and not independent_premain and not invalidated_retry:
        from .models import PointsPostRefundTransferRevision
        revision = PointsPostRefundTransferRevision.objects.filter(order=order).order_by('-pk').first()
        liability = revision.snapshot.get('pending_handover', []) if revision and isinstance(revision.snapshot, dict) else []
        if (not main_refund or len(extra) > 1 or not revision or any(
                row.payment_id == first.payment_id or row.created_at <= revision.created_at
                or [row.payment_id, row.source_segment_id, row.source_basis_before] not in liability
                or row.target_segment_id != revision.transfer.next_segment_id
                or row.previous_id or (row.status == 'ACCEPTED' and row.accepted_at <= revision.created_at)
                for row in extra + inactive)):
            return False
        if extra and [row.version for row in sorted(inactive + extra, key=lambda row: row.version)] != list(
                range(1, extra[0].version + 1)):
            return False
    pre_handover = False
    if len(payments) == 1 and len(accepted) == 1 and not pending and not inactive:
        handover = accepted[0]
        first_transfer = next((item for item in transfers if item.previous_segment_id == handover.source_segment_id), None)
        earlier = [row for row in refunds if row.created_at < handover.accepted_at]
        pre_handover = bool(first_transfer and earlier and all(
            row.created_at != first_transfer.created_at and row.created_at < handover.created_at for row in earlier))
    return (len({row.created_at for row in refunds}) == len(refunds)
            and all(all(refund.payment_id != handover.payment_id
                        or refund.created_at > handover.accepted_at
                        or (pre_handover and refund.created_at < handover.created_at)
                        or (independent_premain and refund.created_at < handover.created_at)
                        or (invalidated_retry and refund.created_at < handover.created_at)
                        or (revision and handover in extra
                            and refund.created_at < revision.transfer.created_at)
                        for handover in accepted) for refund in refunds))


def single_post_refund_handover(order, *, pending_payment_id=None):
    """Recognize only the single payment frozen by a post-refund transfer."""
    from .models import OrderAddonPayment, OrderPrestartHandoff, PointsPostRefundTransferRevision
    payments = list(OrderAddonPayment.objects.filter(intent__order=order))
    revision = PointsPostRefundTransferRevision.objects.filter(order=order).order_by('-pk').first()
    if (len(payments) != 1 or order.refund_records.count() != 1
            or OrderPrestartHandoff.objects.filter(order=order).exists() or not revision
            or not isinstance(revision.snapshot, dict)):
        return False
    payment = payments[0]
    liability = revision.snapshot.get('pending_handover', [])
    rows = list(payment.handovers.order_by('version'))
    accepted = next((row for row in rows if row.status == 'ACCEPTED'), None)
    if (len(liability) != 1 or liability[0][0] != payment.pk
            or [row.version for row in rows] != list(range(1, len(rows) + 1))
            or any(row.previous_id or row.created_at <= revision.created_at
                   or [payment.pk, row.source_segment_id, row.source_basis_before] != liability[0]
                   or row.target_segment_id != revision.transfer.next_segment_id
                   or row.status not in ('PENDING', 'ACCEPTED', 'REJECTED', 'SUPERSEDED') for row in rows)
            or sum(row.status == 'ACCEPTED' for row in rows) > 1
            or sum(row.status == 'PENDING' for row in rows) > 1
            or any(row.status == 'PENDING' and not _pending_allowed(payment.pk, pending_payment_id) for row in rows)
            or any(row.status in ('PENDING', 'ACCEPTED') for row in rows[:-1])
            or any(item.created_at >= revision.transfer.created_at
                   and (not accepted or not accepted.accepted_at or item.created_at <= accepted.accepted_at)
                   for item in payment.refund_records.all())):
        return False
    return True


def post_refund_handover_history(order, *, pending_payment_id=None, pending_intent_id=None):
    """Recognize per-payment liabilities in one frozen transfer version."""
    from .models import OrderAddonPayment, OrderPrestartHandoff, PointsPostRefundTransferRevision
    payments = list(OrderAddonPayment.objects.filter(intent__order=order).order_by('pk'))
    revisions = list(PointsPostRefundTransferRevision.objects.filter(order=order).order_by('pk'))
    revision = revisions[-1] if revisions else None
    refund_count = order.refund_records.count()
    unpaid = set(order.addon_intents.exclude(pk__in=[payment.intent_id for payment in payments])
                 .values_list('pk', flat=True))
    # Unpaid intents are not funding events.  They must not invalidate the
    # frozen liabilities of payments that have actually settled.  A caller
    # about to pay still has to identify an unpaid intent of this order.
    if (not payments or (pending_intent_id is not None and pending_intent_id not in unpaid)
            or refund_count not in (1, 2)
            or not revision or len(revisions) > 4 or not isinstance(revision.snapshot, dict)):
        return False
    second_at = None
    if refund_count == 2:
        from .models import (PointsNegotiationPosting, OrderAddonRefund, OrderAddonHandover,
                             OrderAddonFundingReleaseRun, PointsHandoverRefundInvalidation)
        from .points_followup_handover_invalidation import digest as invalidation_digest
        postings = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order)
                        .order_by('created_at', 'pk'))
        later_refunds = list(OrderAddonRefund.objects.filter(order=order,
            created_at__gte=postings[-1].created_at)) if len(postings) == 2 else []
        later_payments = [payment for payment in payments if len(postings) == 2
                          and payment.buyer_transaction.created_at >= postings[-1].created_at]
        if (len(postings) != 2 or revision.created_at >= postings[-1].created_at
                or any(payment.buyer_transaction.created_at <= postings[-1].created_at
                    or not OrderAddonFundingReleaseRun.objects.filter(payment=payment,
                        points_followup_snapshot__kind='TRANSFER_FOLLOWUP_V1').exists()
                    for payment in later_payments)
                or len({item.created_at for item in later_refunds}) != len(later_refunds)
                or any(item.created_at <= postings[-1].created_at or not OrderAddonFundingReleaseRun.objects.filter(
                    refund=item, points_followup_snapshot__kind='TRANSFER_FOLLOWUP_V1').exists()
                    for item in later_refunds)):
            return False
        second_at = postings[-1].created_at
        straddled = list(OrderAddonHandover.objects.filter(payment__intent__order=order, status='ACCEPTED',
            created_at__lte=second_at, accepted_at__gte=second_at))
        if straddled:
            from .models import OrderPointsTransferFollowupFundingRevision
            from .points_transfer_followup_revision import _digest as followup_digest
            followup = OrderPointsTransferFollowupFundingRevision.objects.filter(posting=postings[1]).first()
            if (not followup or not isinstance(followup.pending_handover_snapshot, list)
                    or followup.evidence_hash != followup_digest(postings[1], followup.first_posting_id,
                        followup.prior_manifest, followup.allocations, followup.releases, followup.funding,
                        followup.revision, followup.prior_required_units, followup.pending_handover_snapshot)
                    or any(row.created_at == second_at or row.accepted_at == second_at
                        or not any(isinstance(item, dict) and item.get('handover_id') == row.pk
                            and item.get('payment_id') == row.payment_id
                            for item in followup.pending_handover_snapshot) for row in straddled)):
                return False
        followup_handovers = list(OrderAddonHandover.objects.filter(payment__intent__order=order,
            created_at__gte=second_at).select_related('payment__intent', 'source_segment__attribution',
                                                     'target_segment__attribution'))
        pending_ids = [row.payment_id for row in followup_handovers if row.status == 'PENDING']
        if len(pending_ids) != len(set(pending_ids)):
            return False
        if any(row.created_at <= second_at or row.version < 1
               or (row.version == 1 and row.previous_id)
               or (row.previous_id and (row.previous.payment_id != row.payment_id
                   or row.previous.version >= row.version or row.previous.status != 'ACCEPTED'
                   or not row.previous.accepted_at or row.previous.accepted_at >= row.created_at))
               or row.status not in ('PENDING', 'ACCEPTED', 'REJECTED', 'SUPERSEDED')
               or (row.status == 'REJECTED' and (row.accepted_at or
                   OrderAddonFundingReleaseRun.objects.filter(handover=row).exists()))
               or (row.status == 'SUPERSEDED' and (row.accepted_at or not any(
                   isinstance(item.snapshot, dict)
                   and item.snapshot.get('kind') == ('VOLUNTARY_INVALIDATION_V1' if row.kind == 'VOLUNTARY'
                                                      else 'TRANSFER_FOLLOWUP_INVALIDATION_V1')
                   and item.evidence_hash == invalidation_digest(item.snapshot)
                   and item.refund_id == item.snapshot.get('refund_id')
                   for item in PointsHandoverRefundInvalidation.objects.filter(handover=row))))
               or (row.status == 'PENDING' and not _pending_allowed(row.payment_id, pending_payment_id))
               or (row.status == 'ACCEPTED' and (not row.accepted_at or not OrderAddonFundingReleaseRun.objects.filter(
                   handover=row, points_followup_snapshot__kind='TRANSFER_FOLLOWUP_V1').exists()))
               for row in followup_handovers):
            return False
    if any(row.prior_id != (revisions[index - 1].pk if index else None)
           or (index and (row.created_at <= revisions[index - 1].created_at
                          or row.transfer.created_at <= revisions[index - 1].created_at))
           for index, row in enumerate(revisions)):
        return False
    handoff = OrderPrestartHandoff.objects.filter(order=order).order_by('-pk').first()
    if handoff:
        from .models import OrderOriginalShareTransfer
        transfers = list(OrderOriginalShareTransfer.objects.filter(
            previous_segment__attribution__order=order).select_related('previous_segment').order_by('pk'))
        if (any(not payment.buyer_transaction.created_at or payment.buyer_transaction.created_at <= handoff.created_at
                for payment in payments) or any(event.created_at <= handoff.created_at for event in transfers)):
            return False
        counts = _prestart_slot_counts(order, handoff)
        for event in transfers:
            slot = event.previous_segment.slot
            counts[slot] = counts.get(slot, 0) + 1
        if any(value > 2 for value in counts.values()):
            return False
    liabilities_by_revision = {}
    for version in revisions:
        liabilities = version.snapshot.get('pending_handover', []) if isinstance(version.snapshot, dict) else None
        if (not isinstance(liabilities, list) or any(not isinstance(row, list) or len(row) != 3
                or any(type(value) is not int or value <= 0 for value in row) for row in liabilities)
                or len({row[0] for row in liabilities}) != len(liabilities)):
            return False
        by_payment = {row[0]: row for row in liabilities}
        if not set(by_payment).issubset({payment.pk for payment in payments}):
            return False
        liabilities_by_revision[version.pk] = by_payment
    if not any(liabilities_by_revision.values()):
        return False
    for payment in payments:
        all_rows = list(payment.handovers.order_by('version'))
        if second_at and any(row.created_at >= second_at for row in all_rows) and (
                [row.version for row in all_rows] != list(range(1, len(all_rows) + 1))):
            return False
        rows = [row for row in all_rows if not second_at or row.created_at < second_at]
        if [row.version for row in rows] != list(range(1, len(rows) + 1)):
            return False
        if any(version.transfer.created_at <= row.created_at <= version.created_at
               for row in rows for version in revisions):
            return False
        for index, version in enumerate(revisions):
            next_version = revisions[index + 1] if index + 1 < len(revisions) else None
            attempts = [row for row in rows if row.created_at > version.created_at
                        and (not next_version or row.created_at < next_version.transfer.created_at)]
            liability = liabilities_by_revision[version.pk].get(payment.pk)
            if not liability:
                if attempts:
                    return False
                continue
            earlier = [row for row in rows if row.status == 'ACCEPTED'
                       and row.accepted_at and row.accepted_at < version.transfer.created_at]
            previous_id = earlier[-1].pk if earlier else None
            accepted = [row for row in attempts if row.status == 'ACCEPTED']
            pending = [row for row in attempts if row.status == 'PENDING']
            from django.utils import timezone
            from .points_transfer_addon_liability import hint_at
            boundary = accepted[0].created_at if len(accepted) == 1 else (
                next_version.transfer.created_at if next_version else timezone.now())
            liability_at_boundary = hint_at(version, payment, boundary)
            if (len(accepted) > 1 or len(pending) > 1
                    or liability_at_boundary is None
                    or any(row.status not in ('PENDING', 'ACCEPTED', 'REJECTED', 'SUPERSEDED')
                           or row.previous_id != previous_id
                           or [payment.pk, row.source_segment_id, row.source_basis_before] !=
                              hint_at(version, payment, row.created_at)
                           or row.target_segment_id != version.transfer.next_segment_id for row in attempts)
                    or any(row.status in ('PENDING', 'ACCEPTED') for row in attempts[:-1])
                    or any(not _pending_allowed(row.payment_id, pending_payment_id) for row in pending)
                    or (next_version and not accepted and liability_at_boundary[2] != 0
                        and any(later.transfer.previous_segment.slot == version.transfer.previous_segment.slot
                                for later in revisions[index + 1:]))
                    or (accepted and payment.refund_records.filter(created_at__gte=accepted[0].created_at,
                        created_at__lte=accepted[0].accepted_at).exists())):
                return False
    return True


def _verify_prestart_history(order, transfers, *, pending_payment_id=None, pending_intent_id=None):
    """Keep service-before-start handoffs separate from earned service time."""
    from .models import OrderPrestartHandoff
    handoff = OrderPrestartHandoff.objects.filter(order=order).order_by('-pk').first()
    if not handoff:
        return False
    from .models import OrderAddonPayment
    payments = list(OrderAddonPayment.objects.filter(intent__order=order).select_related('buyer_transaction'))
    if payments:
        post_history = post_refund_handover_history(order, pending_payment_id=pending_payment_id,
                                                     pending_intent_id=pending_intent_id)
        existing_handover = first_handover_refund(order, transfers, pending_payment_id=pending_payment_id)
        if (any(not payment.buyer_transaction.created_at
                or payment.buyer_transaction.created_at <= handoff.created_at for payment in payments)
                or (len(payments) == 1 and ((len(transfers) > 1
                                             and not (post_history or existing_handover))
                                             or (payments[0].refund_records.exists()
                                                 and not (post_history or existing_handover))))
                or (len(payments) > 1 and ((order.refund_records.exists()
                    and not (post_history or existing_handover))
                    or any((payment.refund_records.exists() and not (independent_refund_after_handovers(order, transfers)
                                                                       or post_history or existing_handover))
                        or (transfers and payment.buyer_transaction.created_at >= transfers[0].created_at
                            and not post_history)
                        for payment in payments)))):
            raise ValidationError('服务前改派后的加购转单资金尚未接通')
    if transfers and handoff.created_at > transfers[0].created_at:
        raise ValidationError('服务前改派与服务中转单时序异常')
    counts = _prestart_slot_counts(order, handoff)
    for event in transfers:
        slot = event.previous_segment.slot
        counts[slot] = counts.get(slot, 0) + 1
    if any(value > 2 for value in counts.values()):
        raise ValidationError('同一席位改派和转单次数超过上限')
    return True


@contextmanager
def addon_payment_read(order):
    """Temporarily verify transfer facts within this order's locked add-on payment."""
    from .points_business_access import permits as business_permits, _bind
    if not any(business_permits(order, phase=phase, kind='addon') for phase in ('payment', 'refund')):
        yield
        return
    with _bind(phase='transfer', kind='transfer', order_id=order.pk,
               account_id=order.customer_account_id, service_id=order.service_id):
        yield


def _postrefund_transfer_addon_scope(order):
    """Identify actual payment versions for the first-refund/transfer chain.

    Full event, allocation and receipt verification still runs under the order
    and funding locks before the capability is granted.
    """
    from .models import (OrderAddonPayment, OrderAddonHandover, OrderAddonFundingReleaseRun,
                         PointsNegotiationPosting, PointsPostRefundTransferRevision)
    postings = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order)
                    .order_by('created_at', 'pk')[:3])
    if len(postings) not in (1, 2) or not isinstance(postings[0].participants, list):
        return False
    first = postings[0]
    payments = list(OrderAddonPayment.objects.filter(intent__order=order).select_related('buyer_transaction'))
    versions = list(PointsPostRefundTransferRevision.objects.filter(order=order)
                    .select_related('transfer').order_by('pk'))
    if (not payments or not versions or versions[0].transfer.created_at <= first.created_at
            or OrderAddonHandover.objects.filter(payment__intent__order=order, status='PENDING').exists()):
        return False
    for payment in payments:
        paid_at = payment.buyer_transaction.created_at
        if not paid_at or paid_at == first.created_at:
            return False
        if paid_at > first.created_at:
            preceding = [version for version in versions if version.created_at < paid_at]
            if any(version.created_at == paid_at or version.transfer.created_at == paid_at for version in versions):
                return False
            run = OrderAddonFundingReleaseRun.objects.filter(payment=payment).first()
            snapshot = run.refunded_main_snapshot if run else None
            if (not first.termination_processed_at or paid_at <= first.termination_processed_at
                    or not run or not isinstance(run.points_payment_snapshot, dict)
                    or not isinstance(snapshot, dict) or snapshot.get('posting_id') != first.pk
                    or snapshot.get('transfer_revision_id') != (preceding[-1].pk if preceding else None)
                    or (not preceding and (snapshot.get('transfer_origin_posting_id')
                        or run.created_at >= versions[0].transfer.created_at))):
                return False
    if len(postings) == 2:
        second_at = postings[1].created_at
        if (versions[-1].created_at >= second_at
                or any(payment.buyer_transaction.created_at >= second_at for payment in payments)
                or order.addon_refunds.filter(created_at__gte=second_at).exists()
                or OrderAddonHandover.objects.filter(payment__intent__order=order,
                    created_at__gte=second_at).exists()):
            return False
    return True


def _followup_transfer_handover_scope(order, *, pending_payment_id=None):
    """Recognize this transfer's actual paid add-ons and subsequent events."""
    from .models import (OrderAddonPayment, OrderAddonHandover, OrderAddonFundingReleaseRun,
                         PointsFollowupTransferRevision)
    version = PointsFollowupTransferRevision.objects.filter(order=order).first()
    if not version or not isinstance(version.snapshot, dict):
        return False
    frozen = version.snapshot.get('pending_handover', [])
    history = version.snapshot.get('addon_history')
    payments = list(OrderAddonPayment.objects.filter(intent__order=order).select_related('buyer_transaction'))
    if not isinstance(frozen, list) or not isinstance(history, list) or not payments:
        return False
    old = [item for item in payments if item.buyer_transaction.created_at
           and item.buyer_transaction.created_at < version.transfer.created_at]
    if (any(not item.buyer_transaction.created_at
            or item.buyer_transaction.created_at == version.transfer.created_at for item in payments)
            or {item.get('payment_id') for item in history if isinstance(item, dict)} != {item.pk for item in old}
            or len(history) != len(old)):
        return False
    by_id = {item.pk: item for item in old}
    if (len({row[0] for row in frozen if isinstance(row, list) and len(row) == 3}) != len(frozen)
            or any(not isinstance(row, list) or len(row) != 3 or row[0] not in by_id
                   or row[1] != version.transfer.previous_segment_id or type(row[2]) is not int or row[2] <= 0
                   for row in frozen)):
        return False
    for payment in payments:
        if payment.handovers.filter(status='ACCEPTED', accepted_at__isnull=True).exists():
            return False
        if payment.buyer_transaction.created_at > version.transfer.created_at and not OrderAddonFundingReleaseRun.objects.filter(
                payment=payment, points_followup_snapshot__kind='TRANSFER_FOLLOWUP_V1',
                points_followup_snapshot__post_followup_transfer_id=version.pk).exists():
            return False
        for refund in payment.refund_records.filter(created_at__gte=version.transfer.created_at):
            if (refund.created_at == version.transfer.created_at or not OrderAddonFundingReleaseRun.objects.filter(
                    refund=refund, points_followup_snapshot__kind='TRANSFER_FOLLOWUP_V1',
                    points_followup_snapshot__post_followup_transfer_id=version.pk).exists()):
                return False
        for handover in payment.handovers.filter(status='ACCEPTED', accepted_at__gte=version.transfer.created_at):
            if (handover.accepted_at == version.transfer.created_at or not OrderAddonFundingReleaseRun.objects.filter(
                    handover=handover, points_followup_snapshot__kind='TRANSFER_FOLLOWUP_V1',
                    points_followup_snapshot__post_followup_transfer_id=version.pk).exists()):
                return False
    pending = list(OrderAddonHandover.objects.filter(payment__intent__order=order, status='PENDING'))
    return (len({item.payment_id for item in pending}) == len(pending)
            and all(_pending_allowed(item.payment_id, pending_payment_id)
                    and any(row[0] == item.payment_id for row in frozen) for item in pending))


def _supported_addon(order, transfers, *, verify=False, allow_handover=False, pending_payment_id=None,
                     pending_intent_id=None, allow_voluntary=False):
    """Admit verified add-on histories with explicit transfer boundaries."""
    from django.db.models import Q
    from .models import OrderAddonPayment
    payments = list(OrderAddonPayment.objects.filter(intent__order=order).select_related('buyer_transaction'))
    unpaid_ids = set(order.addon_intents.exclude(pk__in=[row.intent_id for row in payments]).values_list('pk', flat=True))
    if pending_intent_id and pending_intent_id not in unpaid_ids:
        return False
    intent_count = len(payments)
    if not payments:
        return True
    if _followup_transfer_handover_scope(order, pending_payment_id=pending_payment_id):
        if verify:
            from .addon_payment import verify_receipt
            for payment in payments:
                verify_receipt(payment)
        return True
    if not pending_payment_id and _postrefund_transfer_addon_scope(order):
        if verify:
            from .addon_payment import verify_receipt
            for payment in payments:
                verify_receipt(payment)
        return True
    if allow_voluntary and _mixed_followup_payments(order, pending_intent_id=pending_intent_id,
                                                    pending_payment_id=pending_payment_id):
        if verify:
            from .addon_payment import verify_receipt
            for payment in payments:
                verify_receipt(payment)
        return True
    from .models import OrderAddonHandover
    if not allow_voluntary and OrderAddonHandover.objects.filter(
            payment__intent__order=order, kind='VOLUNTARY').exists():
        # Subsequent refunds need their own per-event allocation replay.
        return False
    if allow_voluntary:
        from .models import OrderAddonHandover, OrderAddonFundingReleaseRun, PointsNegotiationPosting
        postings = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order)
                        .order_by('created_at', 'pk'))
        voluntary = list(OrderAddonHandover.objects.filter(payment__intent__order=order, kind='VOLUNTARY'))
        if voluntary:
            pending = [row for row in voluntary if row.status == 'PENDING']
            accepted = [row for row in voluntary if row.status == 'ACCEPTED']
            if (len(postings) not in (1, 2) or order.refund_records.count() != len(postings)
                    or len(payments) != intent_count
                    or any(not item.buyer_transaction.created_at
                           or (item.buyer_transaction.created_at >= postings[0].created_at
                               and not OrderAddonFundingReleaseRun.objects.filter(payment=item).filter(
                                   Q(points_payment_snapshot__isnull=False) |
                                   Q(points_followup_snapshot__isnull=False)).exists()) for item in payments)
                    or any(row.status not in ('PENDING', 'ACCEPTED', 'REJECTED', 'SUPERSEDED') for row in voluntary)
                    or len({row.payment_id for row in pending}) != len(pending)
                    or any(not _pending_allowed(row.payment_id, pending_payment_id) for row in pending)
                    or any(not row.accepted_at or not OrderAddonFundingReleaseRun.objects.filter(handover=row).filter(
                        Q(points_handover_snapshot__kind='POINTS_VOLUNTARY_HANDOVER_V1') |
                        Q(points_followup_snapshot__kind='TRANSFER_FOLLOWUP_V1',
                          points_followup_snapshot__addon_handover_id=row.pk)).exists()
                           for row in accepted)
                    or any(not OrderAddonFundingReleaseRun.objects.filter(refund=refund).filter(
                            Q(points_addon_snapshot__isnull=False) | Q(points_followup_snapshot__isnull=False)).exists()
                           for row in accepted for item in payments
                           for refund in item.refund_records.filter(created_at__gt=row.accepted_at))):
                return False
            if verify:
                from .addon_payment import verify_receipt
                for payment in payments:
                    verify_receipt(payment)
            return True
    zero_mixed = False
    if len(payments) == 1 and not payments[0].handovers.exists():
        from .models import PointsZeroFollowupTermination
        zero = PointsZeroFollowupTermination.objects.filter(order=order, terminated_at__isnull=False).first()
        snapshot = zero.funding_snapshot if zero else None
        addons = snapshot.get('addons') if isinstance(snapshot, dict) else None
        frozen = addons.get('payments') if isinstance(addons, dict) else None
        if (zero and isinstance(snapshot, dict) and snapshot.get('kind') == 'TRANSFER_ZERO_V1'
                and order.refund_records.count() == 1
                and isinstance(frozen, list) and len(frozen) == 1 and isinstance(frozen[0], dict)
                and frozen[0].get('payment_id') == payments[0].pk):
            first_at = zero.prior_posting.created_at
            zero_mixed = not (payments[0].refund_records.filter(created_at=first_at).exists()
                              or payments[0].refund_records.filter(created_at=zero.terminated_at).exists())
    post_history = post_refund_handover_history(order, pending_payment_id=pending_payment_id,
                                                 pending_intent_id=pending_intent_id)
    origin_history = _origin_addon_history(order, pending_payment_id=pending_payment_id,
                                          pending_intent_id=pending_intent_id, allow_voluntary=allow_voluntary)
    if len(payments) > 1:
        has_handover = any(payment.handovers.filter(status='ACCEPTED').exists() for payment in payments)
        from .models import PointsPostRefundTransferRevision
        initial = (order.refund_records.count() == 1
            and not PointsPostRefundTransferRevision.objects.filter(order=order).exists()
            and not any(payment.handovers.exists() for payment in payments))
        if ((order.refund_records.exists() and not initial and not post_history and not origin_history
                and not first_handover_refund(order, transfers, pending_payment_id=pending_payment_id))
                or (has_handover and any(payment.refund_records.exists() for payment in payments)
                    and not (post_history or origin_history or independent_refund_after_handovers(order, transfers)
                             or first_handover_refund(order, transfers, pending_payment_id=pending_payment_id)))
                or any(not payment.buyer_transaction.created_at
                       or (payment.handovers.filter(status='ACCEPTED').count() > 1 and not post_history)
                       or (payment.handovers.filter(status='PENDING').exists()
                           and (not allow_handover or not _pending_allowed(payment.pk, pending_payment_id)))
                       or any(payment.buyer_transaction.created_at == event.created_at for event in transfers)
                       for payment in payments)):
            return False
        if verify:
            from .addon_payment import verify_receipt
            for payment in payments:
                verify_receipt(payment)
        return True
    if (len(payments) != 1 or (intent_count != 1 and not post_history)
            or (payments[0].refund_records.count() > 1 and order.refund_records.exists()
                and payments[0].refund_records.filter(created_at__lt=order.refund_records.order_by('created_at').first().created_at).exists()
                and payments[0].refund_records.filter(created_at__gte=order.refund_records.order_by('created_at').first().created_at).exists()
                and not first_handover_refund(order, transfers)
                and not single_post_refund_handover(order, pending_payment_id=pending_payment_id)
                and not post_history and not origin_history and not zero_mixed)
            or (payments[0].refund_records.exists() and payments[0].handovers.filter(status='ACCEPTED').exists()
                and not first_handover_refund(order, transfers)
                and not single_post_refund_handover(order, pending_payment_id=pending_payment_id)
                and not post_history and not origin_history)
            or (not allow_handover and (payments[0].handovers.filter(status='PENDING').exists()
                                        or (payments[0].handovers.filter(status='ACCEPTED').count() > 1
                                            and not post_history)))
            or (allow_handover and payments[0].handovers.filter(status='ACCEPTED').count() > 1
                and not post_history)
            or not payments[0].buyer_transaction.created_at
            or any(payments[0].buyer_transaction.created_at == event.created_at for event in transfers)):
        return False
    if verify:
        from .addon_payment import verify_receipt
        verify_receipt(payments[0])
    return True


@contextmanager
def transfer_capture(order_id, *, addon_handover=False, pending_payment_id=None):
    """Hold the exact order and money mutex for one unallocated transfer.

    Outer endpoint authorization, transfer proposal/acceptance, payment
    evidence and all actual mutations remain the responsibility of their
    existing services. This scope is not valid for checkout or refund entry.
    """
    connection, store = transaction.get_connection(), current_store()
    if (getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is not True
            or connection.vendor != 'mysql' or not connection.in_atomic_block or not store
            or store.database_alias != connection.alias or type(order_id) is not int or order_id <= 0):
        raise ValidationError('积分转单正式资金范围尚未开放')
    from django.db.models import Q
    from .financial_lock import lock_order_money
    from .models import (Order, OrderOriginalShareTransfer, OrderPointsFundingAllocation, OrderPointsRedemption,
                         OrderPrestartHandoff, OrderSegmentSettlementManifest)
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if not order:
        raise ValidationError('积分转单订单不存在')
    lock_order_money()
    from users.models import PassRefundRepaymentReceipt
    from users.receipt_usage_guard import isolated_allocation_state_locked
    from django.core.exceptions import ValidationError as DjangoValidationError
    from .models import ReferralRefundFunding
    receipt_ids = set(OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order_id=order.pk) | Q(service_order_id=order.pk)).values_list('receipt_id', flat=True))
    receipt_ids.update(ReferralRefundFunding.objects.filter(order=order).values_list('receipt_id', flat=True))
    receipts = list(PassRefundRepaymentReceipt.objects.select_for_update().filter(pk__in=receipt_ids).order_by('pk'))
    if len(receipts) != len(receipt_ids):
        raise ValidationError('积分转单到账凭证缺失')
    try:
        for receipt in receipts:
            if receipt.usage_version is None:
                raise ValidationError('积分转单到账用途账本缺失')
            isolated_allocation_state_locked(receipt)
    except DjangoValidationError as exc:
        raise ValidationError('积分转单到账用途凭证异常') from exc
    redemption = OrderPointsRedemption.objects.select_for_update().filter(order=order).first()
    from .points_service_payment import MARKER
    if (not redemption or order.is_gift_snapshot or order.source_order_id
            or Order.objects.filter(source_order_id=order.pk).exists()
            or order.status != Order.Status.IN_SERVICE or order.payment_status != Order.PaymentStatus.PAID
            or order.refund_records.exists() or order.settled_at
            or order.providers.filter(settled_at__isnull=False).exists()
            or OrderSegmentSettlementManifest.objects.filter(order=order).exists()
            or not isinstance(order.funding_terms, dict)
            or order.funding_terms.get(MARKER) != {'receipt_id': redemption.pk}):
        raise ValidationError('订单不在首次无补资积分转单范围')
    transfers = list(OrderOriginalShareTransfer.objects.filter(
        previous_segment__attribution__order=order).select_related('previous_segment').order_by('pk'))
    _verify_prestart_history(order, transfers)
    if (addon_handover and not transfers) or not _supported_addon(order, transfers, verify=True,
            allow_handover=addon_handover, pending_payment_id=pending_payment_id):
        raise ValidationError('积分转单加购资金范围尚未接通')
    if not addon_handover:
        from .models import OrderAddonHandover
        if OrderAddonHandover.objects.filter(payment__intent__order=order, status='ACCEPTED').exists():
            raise ValidationError('加购接替后的再次转单资金尚待核对')
        from .models import OrderAddonPayment, OrderParticipationSegment
        payments = list(OrderAddonPayment.objects.filter(intent__order=order).select_related('intent'))
        if len(payments) > 1:
            from .addon_handover import current_responsibilities
            for payment in payments:
                _, responsibilities = current_responsibilities(payment)
                active_ids = [item['segment_id'] for item in responsibilities
                              if item['is_current'] and item['basis'] > 0]
                if OrderParticipationSegment.objects.filter(pk__in=active_ids, released_at__isnull=False).exists():
                    raise ValidationError('请先完成已转出陪玩的各项加购接替')
    counts = {}
    for item in transfers:
        slot = item.previous_segment.slot
        counts[slot] = counts.get(slot, 0) + 1
    if any(count > 2 for count in counts.values()):
        raise ValidationError('同一席位转单次数超过上限')
    from .points_business_access import _bind
    with _bind(phase='transfer', kind='transfer', order_id=order.pk,
               account_id=order.customer_account_id, service_id=order.service_id):
        if transfers:
            from .points_transfer_funding import plan_points_transfer
            from .points_funding_plan import PointsFundingPlanError
            try:
                plan_points_transfer(order.pk, preview_unstarted=True)
            except PointsFundingPlanError as exc:
                raise ValidationError('既有积分转单履约及资金凭证待核对') from exc
        yield order


@contextmanager
def after_transfer(order_id, *, phase, allow_addon_completion=False, pending_payment_id=None, pending_intent_id=None,
                   extra_receipt_id=None, allow_voluntary_history=False):
    """Prepare one independently authorized report, refund, or settlement.

    This only establishes tenant, order and receipt locks and a phase-scoped
    capability. The calling endpoint must still authorize the actor and run
    the existing financial and lifecycle verifiers before any write.
    """
    connection, store = transaction.get_connection(), current_store()
    if (phase not in ('report', 'refund', 'settlement')
            or getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is not True or connection.vendor != 'mysql'
            or not connection.in_atomic_block or not store or store.database_alias != connection.alias
            or type(order_id) is not int or order_id <= 0
            or (extra_receipt_id is not None and (type(extra_receipt_id) is not int or extra_receipt_id <= 0))):
        raise ValidationError('该积分转单业务阶段尚未开放')
    from django.db.models import Q
    from django.core.exceptions import ValidationError as DjangoValidationError
    from users.models import PassRefundRepaymentReceipt
    from users.receipt_usage_guard import isolated_allocation_state_locked
    from .financial_lock import lock_order_money
    from .models import (Order, OrderOriginalShareTransfer, OrderPointsFundingAllocation,
        OrderPointsTransferFundingRelease, OrderPointsTransferFundingRevision, OrderPrestartHandoff,
        OrderSegmentSettlementManifest, OrderPointsRedemption)
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if not order:
        raise ValidationError('积分转单订单不存在')
    if (order.refund_records.count() > 3 and
            (phase == 'settlement' or (phase == 'refund' and allow_addon_completion))):
        from .points_round_refund_scope import locked as round_scope
        with round_scope(order.pk, phase=phase) as locked_order:
            yield locked_order
        return
    lock_order_money()
    transfers = list(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order)
                     .select_related('previous_segment').order_by('pk'))
    counts = {}
    for item in transfers:
        slot = item.previous_segment.slot
        counts[slot] = counts.get(slot, 0) + 1
    allocations = list(OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order_id=order.pk) | Q(service_order_id=order.pk)).order_by('pk'))
    if (phase == 'settlement' or (phase == 'refund' and allow_addon_completion)) and order.refund_records.count() == 3:
        # Only a fully recorded third-round version can enter settlement; the
        # usual two-round capability below must not reinterpret it as round one.
        from .models import PointsRefundRoundEvidence
        from .points_business_access import _bind
        if (not transfers or any(count > 2 for count in counts.values())
                or not PointsRefundRoundEvidence.objects.filter(order=order).exists()):
            raise ValidationError('第三轮退款结算来源尚未接通')
        _verify_prestart_history(order, transfers)
        receipt_ids = {item.receipt_id for item in allocations}
        if extra_receipt_id is not None:
            receipt_ids.add(extra_receipt_id)
        receipts = list(PassRefundRepaymentReceipt.objects.select_for_update().filter(
            pk__in=receipt_ids).order_by('pk'))
        if len(receipts) != len(receipt_ids):
            raise ValidationError('第三轮退款补资到账不存在')
        with _bind(phase=phase, kind='transfer', order_id=order.pk,
                   account_id=order.customer_account_id, service_id=order.service_id):
            from .points_third_refund_settlement_plan import build_locked
            from .settlement import SettlementError
            # Allocation retries may be recovering the platform operation
            # after its tenant transaction committed. The writer verifies the
            # original request separately, before any new allocation.
            if extra_receipt_id is None:
                try:
                    has_addons = order.addon_intents.filter(payment__isnull=False).exists()
                    build_locked(order.pk, allow_finalized=bool(order.settled_at), funding_only=True,
                                 _allow_addons=has_addons)
                except SettlementError as exc:
                    raise ValidationError('第三轮退款结算事实待核对') from exc
            yield order
        return
    from .points_voluntary_handover_event import entry_matches
    voluntary_entry = entry_matches(order)
    voluntary_read = voluntary_entry or allow_voluntary_history or phase in ('report', 'settlement')
    addon_supported = _supported_addon(order, transfers, allow_handover=bool(pending_payment_id),
                                       pending_payment_id=pending_payment_id, pending_intent_id=pending_intent_id,
                                       allow_voluntary=voluntary_read)
    has_addon_event = (order.addon_intents.filter(payment__isnull=False).exists()
                       or pending_intent_id is not None)
    refund_count = order.refund_records.count()
    if refund_count == 2:
        from .models import PointsNegotiationPosting
        postings = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order)
                        .select_related('proposal__points_terms').order_by('points_refund__refund_id'))
        terms = getattr(postings[1].proposal, 'points_terms', None) if len(postings) == 2 else None
        from .points_transfer_followup_scope import supported_addon
        post_history = post_refund_handover_history(order, pending_intent_id=pending_intent_id)
        # The original transfer may have preceded the first refund. Its
        # subsequent add-on refund has a real origin-posting funding event,
        # not a fabricated post-refund transfer revision. Full event and
        # receipt verification runs under the bound scope below.
        origin_history = _origin_addon_history(order, pending_payment_id=pending_payment_id,
                                              pending_intent_id=pending_intent_id,
                                              allow_voluntary=voluntary_read)
        postsecond_only = len(postings) == 2 and not has_paid_addon_before(order, postings[1].created_at)
        mixed_payments = (voluntary_read and _mixed_followup_payments(
            order, pending_intent_id=pending_intent_id, pending_payment_id=pending_payment_id))
        post_history = (post_history or origin_history or postsecond_only or mixed_payments
                        or _postrefund_transfer_addon_scope(order)
                        or _followup_transfer_handover_scope(order, pending_payment_id=pending_payment_id))
        if (len(postings) != 2 or not terms or not (supported_addon(order, postings[0]) or post_history)
                or terms.prior_refund_id != postings[0].points_refund_id):
            raise ValidationError('积分转单追加退款范围尚未接通')
        if has_addon_event:
            from .models import OrderPointsTransferFollowupFundingRevision
            followup = OrderPointsTransferFollowupFundingRevision.objects.filter(posting=postings[1]).first()
            if not followup:
                raise ValidationError('积分转单加购追加退款资金版本待核对')
        from .models import PointsPostRefundTransferRevision
        from .points_transfer_followup_scope import verify as verify_segment_scope
        version = PointsPostRefundTransferRevision.objects.filter(order=order,
            created_at__lt=postings[1].created_at).order_by('-pk').first()
        if has_addon_event and not version and not origin_history and not postsecond_only and not mixed_payments:
            raise ValidationError('积分转单加购追加退款的履约范围尚未开放')
        verify_segment_scope(postings[0], postings[1], version)
    if (order.is_gift_snapshot or order.source_order_id or Order.objects.filter(source_order_id=order.pk).exists()
            or order.status not in (Order.Status.IN_SERVICE, Order.Status.COMPLETED)
            or not addon_supported or refund_count > 2 or not transfers
            or any(count > 2 for count in counts.values())
            or (phase == 'refund' and not order.refund_records.exists()
                and (order.settled_at or (order.providers.filter(settled_at__isnull=False).exists()
                    and not OrderSegmentSettlementManifest.objects.filter(order=order, finalized_at__isnull=True).exists())))):
        raise ValidationError('订单不在该积分转单资金范围')
    prestarted = _verify_prestart_history(order, transfers, pending_payment_id=pending_payment_id,
                                          pending_intent_id=pending_intent_id)
    ids = {item.receipt_id for item in allocations}
    from .models import ReferralRefundFunding
    ids.update(ReferralRefundFunding.objects.filter(order=order).values_list('receipt_id', flat=True))
    if extra_receipt_id is not None:
        ids.add(extra_receipt_id)
    receipts = list(PassRefundRepaymentReceipt.objects.select_for_update().filter(pk__in=ids).order_by('pk'))
    if len(receipts) != len(ids):
        raise ValidationError('积分转单到账凭证缺失')
    try:
        for receipt in receipts:
            if receipt.usage_version is None:
                raise ValidationError('积分转单到账用途账本缺失')
            isolated_allocation_state_locked(receipt)
    except DjangoValidationError as exc:
        raise ValidationError('积分转单到账用途凭证异常') from exc
    if not _supported_addon(order, transfers, verify=True, allow_handover=bool(pending_payment_id),
                            pending_payment_id=pending_payment_id, pending_intent_id=pending_intent_id,
                            allow_voluntary=voluntary_read):
        raise ValidationError('积分转单加购资金范围尚未接通')
    redemption = OrderPointsRedemption.objects.filter(order=order).first()
    from .points_service_payment import MARKER
    if (not redemption or not isinstance(order.funding_terms, dict)
            or order.funding_terms.get(MARKER) != {'receipt_id': redemption.pk}):
        raise ValidationError('积分转单原付款凭证缺失')
    from club_accounts.models import ClubAccount
    buyer = ClubAccount.objects.select_for_update().filter(pk=order.customer_account_id,
        account_type=ClubAccount.AccountType.BOSS, business_cycle=order.business_cycle).first()
    if not buyer:
        raise ValidationError('积分转单老板身份待核对')
    from .points_service_refund import _plan
    _plan(order, None, None, history_only=True)
    # Include historical transferees, not only the current assignee, and lock
    # all existing profiles and wallets before verifying any participant.
    from users.models import EscortProfile
    from wallet.models import Wallet
    assignments = order.providers
    if prestarted:
        from .models import OrderParticipationSegment
        involved = OrderParticipationSegment.objects.filter(attribution__order=order).filter(
            Q(started_at__isnull=False) | Q(released_at__isnull=True)).values('provider_account_id')
        assignments = assignments.filter(provider_account_id__in=involved)
    members = list(assignments.order_by('provider_account_id').values_list('provider_account_id', 'provider_id'))
    accounts = [row[0] for row in members]
    if not 1 <= len(accounts) <= 3 * order.required_provider_count or not all(accounts) or len(set(accounts)) != len(accounts):
        raise ValidationError('积分转单参与账号待核对')
    profiles = list(EscortProfile.objects.select_for_update().filter(account_id__in=accounts).order_by('account_id', 'pk'))
    wallets = list(Wallet.objects.select_for_update().filter(
        Q(account_id__in=accounts) | Q(user_id__in=[row[1] for row in members])).order_by('account_id', 'pk'))
    expected = dict(members)
    if ([(row.account_id, row.user_id) for row in profiles] != members
            or len({row.account_id for row in wallets}) != len(wallets)
            or any(row.account_id not in expected or row.user_id != expected[row.account_id] or not row.is_active
                   for row in wallets) or (len(members) > 1 and len(wallets) != len(members))):
        raise ValidationError('积分转单陪玩钱包或账号待核对')
    from .points_business_access import _bind
    with _bind(phase=phase, kind='transfer', order_id=order.pk,
               account_id=order.customer_account_id, service_id=order.service_id):
        if refund_count == 1:
            from .models import PointsTransferAddonLiabilityRevision, PointsHandoverRefundInvalidation
            from .points_transfer_addon_liability import verify_locked as verify_liability
            from .points_followup_handover_invalidation import verify as verify_invalidation
            witness_id = allocations[0].receipt_id if allocations else None
            for liability in PointsTransferAddonLiabilityRevision.objects.filter(order=order).select_related(
                    'payment__intent', 'refund', 'funding_run', 'transfer_revision', 'previous').order_by('pk'):
                verify_liability(liability, witness_receipt_id=witness_id)
                records = PointsHandoverRefundInvalidation.objects.filter(refund_id=liability.refund_id,
                    handover__payment_id=liability.payment_id,
                    snapshot__kind='TRANSFER_FIRST_INVALIDATION_V1').select_related(
                        'handover__payment__intent', 'refund__payment__intent')
                for record in records:
                    verify_invalidation(record, receipt_id=witness_id)
        if (phase in ('refund', 'report', 'settlement', 'funding') and not pending_payment_id
                and _postrefund_transfer_addon_scope(order)):
            from .models import PointsNegotiationPosting
            from .points_transfer_followup_scope import addon_chain_before
            from django.utils import timezone
            postings = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order)
                            .order_by('created_at', 'pk'))
            boundary = postings[1].created_at if len(postings) == 2 else timezone.now()
            pending_snapshot = []
            if len(postings) == 2:
                from .models import OrderPointsTransferFollowupFundingRevision
                followup_row = OrderPointsTransferFollowupFundingRevision.objects.filter(posting=postings[1]).first()
                if followup_row:
                    from .points_transfer_followup_pending import verify_snapshot
                    pending_snapshot = followup_row.pending_handover_snapshot
                    verify_snapshot(postings[1], pending_snapshot,
                                    receipt_id=allocations[0].receipt_id if allocations else None)
            with verification_scope(order):
                addon_chain_before(order, postings[0], boundary, pending_snapshot=pending_snapshot)
        if refund_count == 2 and mixed_payments:
            from .models import OrderAddonHandover, OrderPointsFundingAllocation, PointsHandoverRefundInvalidation
            from .points_transfer_followup_handover_scope import _verify_rejected, _verify_invalidation
            from django.utils import timezone
            witness = OrderPointsFundingAllocation.objects.filter(
                Q(redemption__order=order) | Q(service_order=order)).order_by('pk').first()
            attempts = OrderAddonHandover.objects.filter(payment__intent__order=order, kind='STANDARD',
                status__in=('REJECTED', 'SUPERSEDED')).select_related('payment__intent').order_by('payment_id', 'version')
            for attempt in attempts:
                following = attempt.payment.handovers.filter(version__gt=attempt.version).order_by('version').first()
                boundary = following.created_at if following else timezone.now()
                if attempt.status == 'REJECTED':
                    _verify_rejected(attempt.payment, attempt, before=boundary)
                else:
                    record = PointsHandoverRefundInvalidation.objects.filter(handover=attempt).select_related(
                        'handover__payment__intent', 'refund__payment__intent').first()
                    if not record or record.created_at >= boundary:
                        raise ValidationError('加购接替失效的退款事件或重发时序异常')
                    _verify_invalidation(record, postings[1].created_at,
                                         receipt_id=witness.receipt_id if witness else None)
        if voluntary_read:
            from .models import OrderAddonHandover, PointsNegotiationPosting
            from .points_transfer_followup_handover_scope import _verify_rejected
            from django.utils import timezone
            rejected = OrderAddonHandover.objects.filter(payment__intent__order=order,
                kind='VOLUNTARY', status='REJECTED').select_related('payment__intent').order_by('payment_id', 'version')
            for attempt in rejected:
                following = attempt.payment.handovers.filter(version__gt=attempt.version).order_by('version').first()
                _verify_rejected(attempt.payment, attempt, before=following.created_at if following else timezone.now())
            from .models import PointsHandoverRefundInvalidation, OrderPointsFundingAllocation
            from .points_followup_handover_invalidation import verify as verify_invalidation
            superseded = OrderAddonHandover.objects.filter(payment__intent__order=order,
                kind='VOLUNTARY', status='SUPERSEDED').order_by('payment_id', 'version')
            witness = OrderPointsFundingAllocation.objects.filter(
                Q(redemption__order=order) | Q(service_order=order)).order_by('pk').first()
            for attempt in superseded:
                record = PointsHandoverRefundInvalidation.objects.filter(handover=attempt).select_related(
                    'handover__payment__intent', 'refund__payment__intent').first()
                if not record:
                    raise ValidationError('自愿接替的失效退款凭证缺失')
                with verification_scope(order):
                    if refund_count == 2:
                        from .points_transfer_followup_handover_scope import _verify_invalidation
                        _verify_invalidation(record, postings[1].created_at,
                                             receipt_id=witness.receipt_id if witness else None)
                    else:
                        verify_invalidation(record, receipt_id=witness.receipt_id if witness else None)
            if OrderAddonHandover.objects.filter(payment__intent__order=order, kind='VOLUNTARY', status='ACCEPTED').exists():
                from .points_transfer_addon_events import later_runs
                first = PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order).order_by(
                    'created_at', 'pk').first()
                if not first:
                    raise ValidationError('自愿接替缺少主单退款凭证')
                with verification_scope(order):
                    later_runs(order, first)
        if refund_count == 1 and _origin_addon_history(order, pending_payment_id=pending_payment_id,
                                                       pending_intent_id=pending_intent_id,
                                                       allow_voluntary=voluntary_read):
            from django.utils import timezone
            from .models import PointsNegotiationPosting
            from .points_transfer_followup_scope import addon_chain_before
            first = PointsNegotiationPosting.objects.get(points_refund__redemption__order=order)
            with verification_scope(order):
                addon_chain_before(order, first, timezone.now())
        if refund_count == 2 and has_addon_event and (post_history or origin_history):
            from .points_transfer_followup_scope import addon_chain_before
            if not postsecond_only:
                from .points_transfer_followup_pending import verify_snapshot
                verify_snapshot(postings[1], followup.pending_handover_snapshot,
                                receipt_id=allocations[0].receipt_id if allocations else None)
                with verification_scope(order):
                    addon_chain_before(order, postings[0], postings[1].created_at,
                                       pending_snapshot=followup.pending_handover_snapshot)
            from .points_transfer_followup_addon_version import latest_run_locked
            latest_run_locked(order, followup)
        if phase in ('report', 'settlement') and order.refund_records.exists():
            with verification_scope(order):
                if refund_count == 2:
                    from .points_transfer_followup_termination import verify_closed_or_settled_locked as verify_followup
                    verify_followup(postings[-1])
                    yield order
                    return
                from .points_transfer_refund_continuation import verify_locked
                from .points_transfer_refund_release import verify_release_locked
                from .models import OrderPointsFundingReleaseEvidence
                posting = verify_locked(order)
                evidence = OrderPointsFundingReleaseEvidence.objects.filter(posting=posting).first()
                if not evidence:
                    raise ValidationError('积分转单退款释放凭证缺失')
                verify_release_locked(evidence)
        yield order


@contextmanager
def post_followup_addon_payment(order_id, intent_id):
    """Bind a new add-on intent after a verified main refund under the order locks."""
    from .models import (OrderAddonIntent, OrderAddonPayment, OrderAddonHandover, OrderProvider,
                         PointsNegotiationPosting)
    if type(intent_id) is not int or intent_id <= 0:
        raise ValidationError('加购申请参数无效')
    existing = OrderAddonPayment.objects.filter(intent_id=intent_id, intent__order_id=order_id).exists()
    from .models import PointsFollowupTransferRevision
    version_hint = PointsFollowupTransferRevision.objects.filter(order_id=order_id).exists()
    pending_hint = (list(OrderAddonHandover.objects.filter(payment__intent__order_id=order_id, status='PENDING')
                         .values_list('payment_id', flat=True)) if version_hint else [])
    if len(pending_hint) != len(set(pending_hint)):
        raise ValidationError('同一加购存在多个待确认方案')
    with after_transfer(order_id, phase='refund', pending_intent_id=None if existing else intent_id,
                        pending_payment_id=frozenset(pending_hint) if pending_hint else None,
                        allow_voluntary_history=True) as order:
        from .models import PointsFollowupTransferRevision
        post_transfer = PointsFollowupTransferRevision.objects.filter(order=order).first()
        if post_transfer:
            locked_pending = list(OrderAddonHandover.objects.filter(payment__intent__order=order, status='PENDING')
                                  .values_list('payment_id', flat=True))
            if len(locked_pending) != len(pending_hint) or set(locked_pending) != set(pending_hint):
                raise ValidationError('待确认接替已变化，请重新核对')
            from .points_followup_transfer_revision import verify_locked, verify_pending_or_completed_locked
            verify_locked(post_transfer)
            verify_pending_or_completed_locked(post_transfer, allow_unproposed=True)
        intent = (OrderAddonIntent.objects.select_for_update().select_related('segment__attribution')
                  .filter(pk=intent_id, order=order).first())
        if (not intent or order.refund_records.count() not in (1, 2) or order.status != order.Status.IN_SERVICE
                or order.settled_at
                or intent.segment.attribution.order_id != order.pk
                or intent.segment.provider_account_id != intent.provider_id
                or not intent.segment.started_at or intent.segment.released_at is not None
                or not OrderProvider.objects.select_for_update().filter(order=order,
                    provider_account_id=intent.provider_id, released_at__isnull=True, settled_at__isnull=True).exists()):
            raise ValidationError('加购付款须属于当前合法履约席位')
        postings = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order)
                        .order_by('points_refund__refund_id'))
        if len(postings) != order.refund_records.count():
            raise ValidationError('转单后续退款凭证缺失')
        for posting in postings:
            selected = set(posting.proposal.affected_segment_ids or [])
            if (intent.provider_id in posting.proposal.affected_accounts
                    and (not selected or intent.segment_id in selected)):
                raise ValidationError('已终止履约段不能承接新加购')
        if order.providers.filter(settled_at__isnull=False).exists():
            from .models import OrderSegmentSettlementManifest
            from .points_transfer_refund_evidence import _prior_settlement, _verify_prior_settlement
            from .segment_addon_settlement_revision import verify_revisions
            manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
            snapshot = _prior_settlement(order)
            if not manifest or manifest.finalized_at or not snapshot:
                raise ValidationError('加购付款前已付清单待核对')
            verify_revisions(manifest)
            _verify_prior_settlement(order, snapshot)
        from .points_business_access import _bind
        from contextlib import nullcontext
        if len(postings) == 2:
            from .points_transfer_followup_addon_version import preflight_locked
            funding_scope = preflight_locked(order, intent_id=intent.pk, allow_paid=True)
        else:
            from .points_transfer_refunded_funding import plan_refunded_transfer
            from .points_funding_plan import PointsFundingPlanError
            try:
                plan_refunded_transfer(order.pk)
            except PointsFundingPlanError as exc:
                raise ValidationError('加购付款前主单资金凭证待核对') from exc
            funding_scope = nullcontext()
        with funding_scope:
            with _bind(phase='payment', kind='transfer_addon', order_id=order.pk,
                       account_id=order.customer_account_id, service_id=order.service_id):
                yield order


@contextmanager
def post_third_addon_refund(order_id, payment_id, *, replay_proposal_id=None):
    """Bind one actual payment's refund to its third-round funding chain."""
    from .models import OrderAddonPayment, OrderAddonRefund, PointsThirdAddonFundingRevision
    from .points_business_access import _bind
    from .points_third_addon_refund import preflight_locked
    with after_transfer(order_id, phase='refund', allow_addon_completion=True) as order:
        payment = OrderAddonPayment.objects.select_for_update().filter(pk=payment_id, intent__order=order).first()
        if not payment:
            raise ValidationError('第三轮后加购付款不存在')
        existing = (OrderAddonRefund.objects.select_for_update().filter(
            order=order, payment=payment, proposal_id=replay_proposal_id).first()
            if replay_proposal_id else None)
        if existing:
            from .points_third_addon_receipt_usage import verify_run_locked
            version = PointsThirdAddonFundingRevision.objects.select_related('run').filter(
                order=order, refund=existing).first()
            if not version:
                raise ValidationError('第三轮后原加购退款资金凭证缺失')
            verify_run_locked(version.run)
            with _bind(phase='refund', kind='transfer_addon', order_id=order.pk,
                       account_id=order.customer_account_id, service_id=order.service_id):
                yield order
            return
        with preflight_locked(order, payment):
            with _bind(phase='refund', kind='transfer_addon', order_id=order.pk,
                       account_id=order.customer_account_id, service_id=order.service_id):
                yield order


@contextmanager
def post_third_addon_payment(order_id, intent_id):
    """Bind a real third-refund payment without changing its refund version."""
    from .models import OrderAddonIntent, OrderAddonPayment, PointsThirdAddonFundingRevision
    from .points_business_access import _bind
    from .points_third_addon_payment import preflight_locked
    with after_transfer(order_id, phase='refund', allow_addon_completion=True) as order:
        intent = (OrderAddonIntent.objects.select_for_update().select_related('segment__attribution')
                  .filter(pk=intent_id, order=order).first())
        if not intent:
            raise ValidationError('第三轮后加购申请不存在')
        payment = OrderAddonPayment.objects.select_for_update().filter(intent=intent).first()
        if payment:
            from .points_third_addon_receipt_usage import verify_run_locked
            revision = PointsThirdAddonFundingRevision.objects.select_related('run').filter(
                order=order, payment=payment).first()
            if not revision:
                raise ValidationError('第三轮后原加购付款资金凭证缺失')
            verify_run_locked(revision.run)
            with _bind(phase='payment', kind='transfer_addon', order_id=order.pk,
                       account_id=order.customer_account_id, service_id=order.service_id):
                yield order
            return
        with preflight_locked(order, intent):
            with _bind(phase='payment', kind='transfer_addon', order_id=order.pk,
                       account_id=order.customer_account_id, service_id=order.service_id):
                yield order


@contextmanager
def post_third_addon_handover(order_id, payment_id, *, handover_id=None):
    """Pin a voluntary handover to this paid add-on and its third-round funds."""
    from .models import (OrderAddonPayment, OrderAddonHandover, PointsThirdAddonFundingRevision,
                         OrderParticipationSegment)
    from .points_business_access import _bind
    from .points_third_addon_responsibilities import current
    from .points_third_addon_receipt_usage import verify_run_locked
    with after_transfer(order_id, phase='refund', allow_addon_completion=True) as order:
        payment = OrderAddonPayment.objects.select_for_update().filter(pk=payment_id, intent__order=order).first()
        if not payment:
            raise ValidationError('第三轮后接替付款不存在')
        proposal = (OrderAddonHandover.objects.select_for_update().filter(pk=handover_id, payment=payment,
            kind='VOLUNTARY').first() if handover_id else None)
        if handover_id and not proposal:
            raise ValidationError('接替方案与该付款不一致')
        if proposal and proposal.status == 'ACCEPTED':
            revision = PointsThirdAddonFundingRevision.objects.select_related('run').filter(
                order=order, handover=proposal).first()
            if not revision:
                raise ValidationError('原接替资金版本缺失')
            verify_run_locked(revision.run)
        elif proposal and proposal.status == 'REJECTED':
            if (not proposal.decisions.filter(accepted=False, decided_at__isnull=False).exists()
                    or PointsThirdAddonFundingRevision.objects.filter(order=order, handover=proposal).exists()):
                raise ValidationError('原拒绝决定或资金状态异常')
        else:
            if (order.status != order.Status.IN_SERVICE or order.settled_at
                    or order.providers.filter(settled_at__isnull=False).exists()):
                raise ValidationError('第三轮后接替须在本人仍履约时办理')
            _, shares = current(payment, lock=True)
            active = [row for row in shares if row['is_current']]
            if (len(active) != 1 or not order.providers.filter(provider_account_id=active[0]['provider_id'],
                    released_at__isnull=True, settled_at__isnull=True).exists()
                    or not OrderParticipationSegment.objects.filter(pk=active[0]['segment_id'],
                        attribution__order=order, provider_account_id=active[0]['provider_id'],
                        started_at__isnull=False, released_at__isnull=True).exists()):
                raise ValidationError('加购当前负责人不在履约')
            if proposal and (proposal.status != 'PENDING' or proposal.source_segment_id != active[0]['segment_id']):
                raise ValidationError('接替原责任已变化')
        with _bind(phase='handover', kind='transfer_addon', order_id=order.pk,
                   account_id=order.customer_account_id, service_id=order.service_id):
            yield order


@contextmanager
def post_third_standard_handover(order_id, payment_id, *, handover_id=None, target_account_id=None):
    """Bind a pending add-on to the actual third-round service transfer."""
    from .models import (OrderAddonPayment, OrderAddonHandover, PointsThirdAddonFundingRevision,
                         PointsThirdTransferRevision, OrderParticipationSegment)
    from .points_business_access import _bind
    from .points_third_addon_responsibilities import current
    from .points_third_addon_receipt_usage import verify_run_locked, verify_third_transfer_run_locked
    with after_transfer(order_id, phase='refund', allow_addon_completion=True) as order:
        payment = OrderAddonPayment.objects.select_for_update().filter(pk=payment_id, intent__order=order).first()
        proposal = (OrderAddonHandover.objects.select_for_update().filter(pk=handover_id, payment=payment,
            kind='STANDARD').select_related('target_segment').first() if handover_id and payment else None)
        if not payment or (handover_id and not proposal):
            raise ValidationError('转单待接替付款或方案不存在')
        if proposal and proposal.status == 'ACCEPTED':
            revision = PointsThirdAddonFundingRevision.objects.select_related('run').filter(
                order=order, handover=proposal).first()
            if not revision:
                raise ValidationError('原接替资金版本缺失')
            verify_run_locked(revision.run)
        elif proposal and proposal.status == 'REJECTED':
            if (not proposal.decisions.filter(accepted=False, decided_at__isnull=False).exists()
                    or PointsThirdAddonFundingRevision.objects.filter(order=order, handover=proposal).exists()):
                raise ValidationError('原拒绝决定或资金状态异常')
        elif proposal and proposal.status == 'SUPERSEDED':
            from .models import PointsThirdHandoverTargetInvalidation, PointsHandoverRefundInvalidation
            target_event = PointsThirdHandoverTargetInvalidation.objects.filter(handover=proposal).select_related(
                'handover__payment__intent', 'original_revision', 'transfer__previous_segment__attribution',
                'posting__proposal__case').first()
            if target_event:
                from .points_third_target_invalidation import verify
                verify(target_event)
            else:
                refund_event = PointsHandoverRefundInvalidation.objects.filter(handover=proposal).select_related(
                    'handover__payment__intent', 'refund__payment__intent').first()
                if (not refund_event or not isinstance(refund_event.snapshot, dict)
                        or refund_event.snapshot.get('kind') != 'POINTS_THIRD_TRANSFER_INVALIDATION_V1'):
                    raise ValidationError('原接替方案失效事实待核对')
                from .points_third_handover_invalidation import capture, digest
                frozen = capture(proposal, refund_event.refund)
                if refund_event.snapshot != frozen or refund_event.evidence_hash != digest(frozen):
                    raise ValidationError('原退款导致的接替失效事实不一致')
        else:
            if (order.status != order.Status.IN_SERVICE or order.settled_at
                    or order.providers.filter(settled_at__isnull=False).exists()):
                raise ValidationError('转单待接替须在继续履约时办理')
            _, shares = current(payment, lock=True)
            active = [row for row in shares if row['is_current'] and row['basis'] > 0]
            if len(active) != 1:
                raise ValidationError('转出加购责任不唯一')
            source = active[0]
            target = proposal.target_segment.provider_account_id if proposal else target_account_id
            candidates = list(PointsThirdTransferRevision.objects.select_for_update().filter(order=order,
                transfer__previous_segment_id=source['segment_id']).select_related(
                    'transfer__next_segment').order_by('-created_at', '-pk'))
            version = next((item for item in candidates if any(
                len(row) == 3 and row[0] == payment.pk and row[1] == source['segment_id']
                and 0 < source['basis'] <= row[2] for row in item.snapshot.get('pending_handover', []))), None)
            if not version:
                raise ValidationError('该付款没有对应的真实转出待交责任')
            verify_third_transfer_run_locked(version)
            if OrderAddonHandover.objects.filter(payment=payment, status='ACCEPTED',
                    source_segment_id=source['segment_id'], created_at__gt=version.transfer.created_at).exists():
                raise ValidationError('该次转单待交责任已确认接替')
            targets = list(OrderParticipationSegment.objects.select_for_update().filter(
                attribution__order=order, provider_account_id=target, started_at__isnull=False,
                released_at__isnull=True).order_by('pk'))
            if len(targets) != 1 or not order.providers.filter(provider_account_id=target,
                    released_at__isnull=True, settled_at__isnull=True).exists():
                raise ValidationError('接替目标不在当前履约段')
            if (proposal and (proposal.status != 'PENDING' or proposal.source_segment_id != source['segment_id']
                             or proposal.target_segment_id != targets[0].pk)):
                raise ValidationError('接替方案与转单待交责任不一致')
            from .models import PointsThirdHandoverTargetInvalidation, PointsHandoverRefundInvalidation
            from .points_third_target_invalidation import verify as verify_target_invalidation
            for old in payment.handovers.filter(status='SUPERSEDED', kind='STANDARD',
                    created_at__gt=version.transfer.created_at).order_by('version'):
                invalidation = PointsThirdHandoverTargetInvalidation.objects.filter(handover=old).select_related(
                    'handover__payment__intent', 'original_revision', 'transfer__previous_segment__attribution',
                    'posting__proposal__case').first()
                if invalidation:
                    verify_target_invalidation(invalidation)
                    continue
                refund_invalidation = PointsHandoverRefundInvalidation.objects.filter(handover=old).select_related(
                    'handover__payment__intent', 'refund__payment__intent').first()
                if (not refund_invalidation or not isinstance(refund_invalidation.snapshot, dict)
                        or refund_invalidation.snapshot.get('kind') != 'POINTS_THIRD_TRANSFER_INVALIDATION_V1'):
                    raise ValidationError('原接替方案失效事实待核对')
                from .points_third_handover_invalidation import capture as capture_refund_invalidation, digest
                frozen = capture_refund_invalidation(old, refund_invalidation.refund)
                if refund_invalidation.snapshot != frozen or refund_invalidation.evidence_hash != digest(frozen):
                    raise ValidationError('原退款导致的接替失效事实不一致')
        with _bind(phase='handover', kind='transfer_addon', order_id=order.pk,
                   account_id=order.customer_account_id, service_id=order.service_id):
            yield order


@contextmanager
def voluntary_handover(order_id, payment_id, *, handover_id=None, target_account_id=None):
    """Lock an existing points order for a separately identified voluntary split."""
    from django.db.models import Q
    from .models import OrderAddonHandover, OrderAddonPayment
    from .points_business_access import _bind
    from .points_voluntary_handover_event import entry
    pending_hint = list(OrderAddonHandover.objects.filter(payment__intent__order_id=order_id,
        status='PENDING').values_list('payment_id', flat=True))
    allowed_pending = frozenset([payment_id, *pending_hint])
    from .models import Order
    replay_hint = bool(handover_id and OrderAddonHandover.objects.filter(pk=handover_id,
        payment_id=payment_id, payment__intent__order_id=order_id, kind='VOLUNTARY', status='ACCEPTED').exists())
    settled_hint = bool(replay_hint and Order.objects.filter(pk=order_id, settled_at__isnull=False).exists())
    with entry(order_id, payment_id):
        with after_transfer(order_id, phase='settlement' if settled_hint else 'refund',
                            pending_payment_id=allowed_pending) as order:
            payment = OrderAddonPayment.objects.select_for_update().filter(pk=payment_id, intent__order=order).first()
            pending = list(OrderAddonHandover.objects.filter(payment__intent__order=order, status='PENDING'))
            replay = bool(payment and handover_id and order.refund_records.count() == 2
                and OrderAddonHandover.objects.filter(pk=handover_id, payment=payment,
                    kind='VOLUNTARY', status='ACCEPTED').exists())
            if (not payment or (order.status != order.Status.IN_SERVICE and not
                    (replay and order.status == order.Status.COMPLETED))
                    or order.refund_records.count() not in (1, 2)
                    or (order.settled_at and not (replay and settled_hint))
                    or len({row.payment_id for row in pending}) != len(pending)
                    or set(row.payment_id for row in pending) != set(pending_hint)
                    or any(row.kind != 'VOLUNTARY' for row in pending)):
                raise ValidationError('自愿接替的订单或待确认方案范围待核对')
            if order.refund_records.count() == 2:
                from .models import OrderSegmentSettlementManifest
                if (not (replay and settled_hint) and (order.providers.filter(settled_at__isnull=False).exists()
                        or OrderSegmentSettlementManifest.objects.filter(order=order).exists())):
                    raise ValidationError('已结算人员的后续自愿接替尚待核对')
                handover = (OrderAddonHandover.objects.select_related('target_segment').filter(
                    pk=handover_id, payment=payment, kind='VOLUNTARY').first() if handover_id else None)
                target = handover.target_segment.provider_account_id if handover else target_account_id
                if handover and handover.status == 'ACCEPTED':
                    from .points_transfer_followup_handover_scope import verify_event
                    from .addon_funding_release import verify_run
                    from .models import OrderPointsFundingAllocation
                    witness = OrderPointsFundingAllocation.objects.filter(
                        Q(redemption__order=order) | Q(service_order=order)).order_by('pk').first()
                    run = getattr(handover, 'funding_release_run', None)
                    if not run:
                        raise ValidationError('自愿接替资金版本缺失')
                    verify_event(payment, handover, receipt_id=witness.receipt_id if witness else None)
                    verify_run(run)
                    with _bind(phase='handover', kind='transfer_addon', order_id=order.pk,
                               account_id=order.customer_account_id, service_id=order.service_id):
                        yield order
                    return
                from .points_transfer_followup_handover_scope import source_locked
                source_locked(order, payment, target, pending_handover_id=handover.pk if handover else None,
                              require_active_source=True)
                with _bind(phase='handover', kind='transfer_addon', order_id=order.pk,
                           account_id=order.customer_account_id, service_id=order.service_id):
                    if handover:
                        from .points_transfer_followup_addon_version import preflight_locked
                        with preflight_locked(order, handover_id=handover.pk):
                            yield order
                    else:
                        yield order
                return
            if order.providers.filter(settled_at__isnull=False).exists():
                from .models import OrderSegmentSettlementManifest
                from .points_transfer_refund_evidence import _prior_settlement, _verify_prior_settlement
                from .segment_addon_settlement_revision import verify_revisions
                manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
                snapshot = _prior_settlement(order)
                if not manifest or manifest.finalized_at or not snapshot:
                    raise ValidationError('自愿接替前已付清单待核对')
                verify_revisions(manifest)
                _verify_prior_settlement(order, snapshot)
            from .points_transfer_refunded_funding import plan_refunded_transfer
            from .points_funding_evidence import verify_order_funding
            from .points_funding_plan import PointsFundingPlanError
            try:
                plan_refunded_transfer(order.pk)
                verify_order_funding(order.pk)
            except PointsFundingPlanError as exc:
                raise ValidationError('自愿接替前的订单和到账凭证待核对') from exc
            with _bind(phase='handover', kind='transfer_addon', order_id=order.pk,
                       account_id=order.customer_account_id, service_id=order.service_id):
                yield order


@contextmanager
def post_refund_addon_handover(order_id, payment_id, *, replay_handover_id=None, target_account_id=None):
    """Bind one add-on liability frozen by the latest real transfer."""
    if type(payment_id) is not int or payment_id <= 0:
        raise ValidationError('加购接替付款参数无效')
    from .models import PointsPostRefundTransferRevision, OrderAddonHandover
    from .models import Order
    hint = Order.objects.filter(pk=order_id).first()
    allowed_pending = payment_id
    if hint and hint.refund_records.count() in (1, 2):
        allowed_pending = frozenset([payment_id, *OrderAddonHandover.objects.filter(
            payment__intent__order_id=order_id, status='PENDING').values_list('payment_id', flat=True)])
    with after_transfer(order_id, phase='refund', pending_payment_id=allowed_pending,
                        allow_voluntary_history=bool(hint and hint.refund_records.count() == 2)) as order:
        if order.refund_records.count() == 2:
            from .models import OrderAddonPayment
            payment = OrderAddonPayment.objects.select_for_update().filter(pk=payment_id, intent__order=order).first()
            if not payment:
                raise ValidationError('加购付款不存在')
            handover = (OrderAddonHandover.objects.filter(pk=replay_handover_id, payment=payment)
                        .select_related('target_segment').first() if replay_handover_id else None)
            target = handover.target_segment.provider_account_id if handover else target_account_id
            from .points_business_access import _bind
            if handover and handover.status == 'ACCEPTED':
                from .points_transfer_followup_handover_scope import verify_event
                from .addon_funding_release import verify_run
                from .models import OrderPointsFundingAllocation
                run = getattr(handover, 'funding_release_run', None)
                if not run:
                    raise ValidationError('加购接替资金版本缺失')
                from django.db.models import Q
                witness = OrderPointsFundingAllocation.objects.filter(
                    Q(redemption__order=order) | Q(service_order=order)).order_by('pk').first()
                verify_event(payment, handover, receipt_id=witness.receipt_id if witness else None)
                verify_run(run)
                with _bind(phase='transfer', kind='transfer', order_id=order.pk,
                           account_id=order.customer_account_id, service_id=order.service_id):
                    yield order
                return
            from .points_transfer_followup_handover_scope import source_locked, refunded_reproposal_scope
            source = source_locked(order, payment, target, pending_handover_id=handover.pk if handover else None)
            with _bind(phase='transfer', kind='transfer', order_id=order.pk,
                       account_id=order.customer_account_id, service_id=order.service_id):
                with refunded_reproposal_scope(order, payment, source, handover=handover):
                    if handover:
                        from .points_transfer_followup_addon_version import preflight_locked
                        with preflight_locked(order, handover_id=handover.pk):
                            yield order
                    else:
                        yield order
            return
        if (order.status != order.Status.IN_SERVICE or order.refund_records.count() != 1 or order.settled_at
                or order.providers.filter(settled_at__isnull=False).exists()):
            raise ValidationError('主单退款后的加购接替范围尚未开放')
        from .points_post_refund_transfer import verify_locked, unresolved_pending_locked
        from .models import OrderAddonPayment
        from .addon_handover import current_responsibilities
        from .points_transfer_addon_liability import frozen_transfer_for_handover
        payment = OrderAddonPayment.objects.select_for_update().filter(pk=payment_id, intent__order=order).first()
        handover = (OrderAddonHandover.objects.filter(pk=replay_handover_id, payment=payment).first()
                    if replay_handover_id and payment else None)
        if not payment or (replay_handover_id and not handover):
            raise ValidationError('加购付款或接替方案不存在')
        versions = list(PointsPostRefundTransferRevision.objects.filter(order=order)
                        .select_related('transfer__previous_segment', 'transfer__next_segment').order_by('pk'))
        unresolved_by_payment = {}
        for version in versions:
            verify_locked(version)
            for item in unresolved_pending_locked(version):
                if item[0] in unresolved_by_payment:
                    raise ValidationError('同一付款存在多个待交转单责任')
                unresolved_by_payment[item[0]] = (version, item)
        if handover:
            revision = frozen_transfer_for_handover(order, payment_id, handover.source_segment_id,
                                                     handover.target_segment_id, handover.created_at)
        else:
            revision = unresolved_by_payment.get(payment_id, (None, None))[0]
        if not revision:
            raise ValidationError('加购接替缺少真实转单资金版本')
        if target_account_id is not None and target_account_id != revision.transfer.next_segment.provider_account_id:
            raise ValidationError('加购接替人须为该次转入陪玩')
        pending_rows = list(OrderAddonHandover.objects.select_for_update().filter(
            payment__intent__order=order, status='PENDING').select_related(
                'payment__intent', 'source_segment', 'target_segment').order_by('payment_id', 'pk'))
        if len({row.payment_id for row in pending_rows}) != len(pending_rows):
            raise ValidationError('同一付款存在多个待确认接替')
        for row in pending_rows:
            owner = frozen_transfer_for_handover(order, row.payment_id, row.source_segment_id,
                                                  row.target_segment_id, row.created_at)
            _, shares = current_responsibilities(row.payment, lock=True)
            active = [part for part in shares if part['is_current']]
            if (row.kind != 'STANDARD' or row.accepted_at or len(active) != 1
                    or not owner or row.payment_id not in unresolved_by_payment
                    or unresolved_by_payment[row.payment_id][0].pk != owner.pk
                    or unresolved_by_payment[row.payment_id][1] !=
                        [row.payment_id, row.source_segment_id, row.source_basis_before]
                    or active[0]['segment_id'] != row.source_segment_id
                    or active[0]['basis'] != row.source_basis_before
                    or row.target_segment_id != owner.transfer.next_segment_id):
                raise ValidationError('同单待接替付款责任已变化')
        if replay_handover_id:
            from .points_transfer_addon_liability import effective_at
            liability = effective_at(revision, payment, handover.created_at)
            if (not handover or not liability or handover.source_segment_id != liability[1]
                    or handover.source_basis_before != liability[2]
                    or handover.target_segment_id != revision.transfer.next_segment_id
                    or handover.created_at <= revision.created_at):
                raise ValidationError('加购接替方案与转单负债不一致')
        if payment_id not in unresolved_by_payment:
            if not (replay_handover_id and OrderAddonHandover.objects.filter(
                    pk=replay_handover_id, payment_id=payment_id, status='ACCEPTED',
                    source_segment_id=revision.transfer.previous_segment_id,
                    target_segment_id=revision.transfer.next_segment_id).exists()):
                raise ValidationError('该加购接替已完成')
        from .points_business_access import _bind
        from .points_transfer_addon_liability import first_handover_pending_scope
        with first_handover_pending_scope(order, unresolved_by_payment):
            with _bind(phase='transfer', kind='transfer', order_id=order.pk,
                       account_id=order.customer_account_id, service_id=order.service_id):
                yield order


@contextmanager
def third_refund_transfer(order_id):
    """Lock a third-round transfer until its actual funding version is written."""
    from .models import PointsThirdTransferRevision
    from .points_business_access import _bind
    from .points_third_addon_receipt_usage import verify_third_transfer_run_locked
    with after_transfer(order_id, phase='refund', allow_addon_completion=True) as order:
        if (order.status != order.Status.IN_SERVICE or order.settled_at
                or order.providers.filter(settled_at__isnull=False).exists()):
            raise ValidationError('第三轮后转单须在未结算履约期间办理')
        prior = list(PointsThirdTransferRevision.objects.select_for_update().filter(order=order)
                     .order_by('created_at', 'pk').values_list('pk', flat=True))
        with _bind(phase='transfer', kind='transfer', order_id=order.pk,
                   account_id=order.customer_account_id, service_id=order.service_id):
            yield order
        versions = list(PointsThirdTransferRevision.objects.select_for_update().filter(order=order)
                        .order_by('created_at', 'pk'))
        if [version.pk for version in versions[:-1]] != prior or len(versions) != len(prior) + 1:
            raise ValidationError('第三轮后转单资金版本缺失')
        verify_third_transfer_run_locked(versions[-1])


@contextmanager
def post_refund_transfer(order_id):
    """Prevalidate a one-refund points order before an independent new transfer."""
    from .models import OrderOriginalShareTransfer
    if not OrderOriginalShareTransfer.objects.filter(
            previous_segment__attribution__order_id=order_id).exists():
        with _first_transfer_after_refund(order_id) as order:
            yield order
        return
    from .models import OrderAddonHandover, PointsPostRefundTransferRevision
    hints = list(OrderAddonHandover.objects.filter(payment__intent__order_id=order_id,
        status='PENDING').values_list('payment_id', flat=True))
    if len(set(hints)) != len(hints):
        raise ValidationError('同一付款存在多个待确认接替')
    liability_hints = {item[0] for snapshot in PointsPostRefundTransferRevision.objects.filter(
        order_id=order_id).values_list('snapshot', flat=True) if isinstance(snapshot, dict)
        for item in snapshot.get('pending_handover', [])
        if isinstance(item, list) and len(item) == 3 and type(item[0]) is int}
    from .models import Order
    hint_order = Order.objects.filter(pk=order_id).first()
    hint_scope = (frozenset(set(hints) | liability_hints)
                  if hint_order and hint_order.refund_records.count() == 1 else frozenset())
    with after_transfer(order_id, phase='refund', pending_payment_id=hint_scope or None) as order:
        if (order.status != order.Status.IN_SERVICE or order.settled_at
                or order.providers.filter(settled_at__isnull=False).exists()):
            raise ValidationError('该积分退款后转单资金范围尚未开放')
        if order.refund_records.count() == 2:
            from .models import OrderAddonPayment, PointsFollowupTransferRevision
            if PointsFollowupTransferRevision.objects.filter(order=order).exists():
                raise ValidationError('二次退款后再次转单尚未接通')
            from .points_transfer_followup_funding import plan_locked
            from .points_funding_plan import PointsFundingPlanError
            try:
                plan_locked(order.pk, allow_in_service=True)
            except PointsFundingPlanError as exc:
                raise ValidationError('二次退款后的转单原资金待核对') from exc
            from .points_business_access import _bind
            with _bind(phase='transfer', kind='transfer', order_id=order.pk,
                       account_id=order.customer_account_id, service_id=order.service_id):
                yield order
                if not PointsFollowupTransferRevision.objects.filter(order=order).exists():
                    raise ValidationError('二次退款后转单资金版本缺失')
            return
        if order.refund_records.count() != 1:
            raise ValidationError('该积分退款后转单资金范围尚未开放')
        from .models import PointsPostRefundTransferRevision
        from .points_post_refund_transfer import verify_locked, unresolved_pending_locked
        from .points_transfer_addon_liability import (first_handover_pending_scope,
                                                       frozen_transfer_for_handover)
        from .addon_handover import current_responsibilities
        versions = list(PointsPostRefundTransferRevision.objects.filter(order=order).order_by('pk'))
        unresolved = {}
        for version in versions:
            verify_locked(version)
            for item in unresolved_pending_locked(version):
                if item[0] in unresolved:
                    raise ValidationError('同一付款存在多个待交转单责任')
                unresolved[item[0]] = (version, item)
        pending_rows = list(OrderAddonHandover.objects.select_for_update().filter(
            payment__intent__order=order, status='PENDING').select_related(
                'payment__intent', 'source_segment', 'target_segment').order_by('payment_id', 'pk'))
        if {row.payment_id for row in pending_rows} != set(hints):
            raise ValidationError('待确认接替已变化，请重试')
        for row in pending_rows:
            version = frozen_transfer_for_handover(order, row.payment_id, row.source_segment_id,
                                                    row.target_segment_id, row.created_at)
            _, shares = current_responsibilities(row.payment, lock=True)
            active = [item for item in shares if item['is_current']]
            if (row.kind != 'STANDARD' or row.accepted_at or not version or len(active) != 1
                    or row.payment_id not in unresolved or unresolved[row.payment_id][0].pk != version.pk
                    or unresolved[row.payment_id][1] != [row.payment_id, row.source_segment_id, row.source_basis_before]
                    or active[0]['segment_id'] != row.source_segment_id
                    or active[0]['basis'] != row.source_basis_before):
                raise ValidationError('待确认接替的转单责任已变化')
        from .points_transfer_refunded_funding import plan_refunded_transfer
        from .points_funding_plan import PointsFundingPlanError
        with first_handover_pending_scope(order, unresolved):
            try:
                plan_refunded_transfer(order.pk)
            except PointsFundingPlanError as exc:
                raise ValidationError('积分退款后转单原资金凭证待核对') from exc
            from .points_business_access import _bind
            with _bind(phase='transfer', kind='transfer', order_id=order.pk,
                       account_id=order.customer_account_id, service_id=order.service_id):
                yield order
                updated = list(PointsPostRefundTransferRevision.objects.filter(order=order).order_by('pk'))
                if (len(updated) != len(versions) + 1
                        or [row.pk for row in updated[:-1]] != [row.pk for row in versions]
                        or updated[-1].prior_id != (versions[-1].pk if versions else None)):
                    raise ValidationError('退款后转单未建立连续资金版本')
                verify_locked(updated[-1])


@contextmanager
def _first_transfer_after_refund(order_id):
    """Freeze any remaining paid add-on responsibility at the first transfer."""
    from .points_business_access import service_order, _bind
    from .models import (Order, OrderAddonPayment, OrderOriginalShareTransfer,
                         OrderPrestartHandoff, PointsNegotiationPosting, PointsPostRefundTransferRevision)
    from .points_refunded_funding import plan_refunded_order
    from .points_funding_plan import PointsFundingPlanError
    locked = Order.objects.select_for_update().filter(pk=order_id).first()
    if not locked:
        raise ValidationError('积分订单不存在')
    has_paid_addon = OrderAddonPayment.objects.filter(intent__order=locked).exists()
    with service_order(order_id, phase='refund', addon=has_paid_addon) as order:
        payments = list(OrderAddonPayment.objects.filter(intent__order=order).select_related(
            'intent__segment', 'buyer_transaction'))
        postings = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order))
        if (order.status != order.Status.IN_SERVICE or order.refund_records.count() != 1
                or order.settled_at or order.providers.filter(settled_at__isnull=False).exists()
                or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
                or OrderPrestartHandoff.objects.filter(order=order).exists()
                or PointsPostRefundTransferRevision.objects.filter(order=order).exists()
                or len(postings) != 1
                or not isinstance(postings[0].participants, list)
                or any(not payment.buyer_transaction.created_at
                       or payment.buyer_transaction.created_at == postings[0].created_at
                       for payment in payments)):
            raise ValidationError('主单退款后首次转单的加购资金范围尚未开放')
        try:
            plan_refunded_order(order.pk)
            from .models import OrderPointsFundingAllocation
            from .points_first_transfer_addon_history import verify as verify_addon_history
            from django.db.models import Q
            from django.utils import timezone
            witness = OrderPointsFundingAllocation.objects.filter(
                Q(redemption__order=order) | Q(service_order=order)).order_by('pk').first()
            if payments:
                verify_addon_history(order, postings[0], receipt_id=witness.receipt_id if witness else None,
                                     before=timezone.now())
        except PointsFundingPlanError as exc:
            raise ValidationError('首次转单前的主单与加购资金凭证待核对') from exc
        from .addon_handover import current_responsibilities
        remaining = {}
        for payment in payments:
            _, shares = current_responsibilities(payment)
            active = [row for row in shares if row['is_current'] and row['basis'] > 0]
            if len(active) > 1:
                raise ValidationError('首次转单前加购负责人不唯一')
            if active:
                remaining[payment.pk] = active[0]
        with _bind(phase='transfer', kind='transfer', order_id=order.pk,
                   account_id=order.customer_account_id, service_id=order.service_id):
            yield order
            versions = list(PointsPostRefundTransferRevision.objects.filter(order=order).order_by('pk'))
            if len(versions) != 1:
                raise ValidationError('首次转单未建立唯一资金版本')
            version = versions[0]
            pending = version.snapshot.get('pending_handover', []) if isinstance(version.snapshot, dict) else None
            affected = {payment.pk for payment in payments if payment.pk in remaining
                        and remaining[payment.pk]['segment_id'] == version.transfer.previous_segment_id}
            if (version.prior_id or not isinstance(pending, list)
                    or any(not isinstance(row, list) or len(row) != 3 for row in pending)
                    or len(pending) != len(affected) or {row[0] for row in pending} != affected
                    or any(row[1] != version.transfer.previous_segment_id
                           or row[2] != remaining[row[0]]['basis'] for row in pending)):
                raise ValidationError('首次转单未冻结原加购责任')
            with verification_scope(order):
                from .points_post_refund_transfer import verify_locked
                verify_locked(version)


@contextmanager
def verification_scope(order):
    """Internal read-only verification using already acquired stage locks."""
    if permits(order, phase='refund'):
        yield
        return
    if not any(permits(order, phase=phase) for phase in ('transfer', 'report', 'settlement')):
        raise ValidationError('积分转单核对上下文无效')
    from .points_business_access import _bind
    with _bind(phase='refund', kind='transfer', order_id=order.pk,
               account_id=order.customer_account_id, service_id=order.service_id):
        yield
