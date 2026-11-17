"""Points transfer versions after an agreed refund; no implicit authorization."""
import hashlib
import json
from dataclasses import asdict

from django.db.models import Q
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .models import (OrderPointsFundingAllocation, OrderPointsFundingRelease, OrderPointsTransferFundingRelease,
                     PointsNegotiationPosting, PointsPostRefundTransferRevision, PointsPostRefundTransferRelease)


def _digest(order_id, transfer_id, prior_id, snapshot):
    payload = [order_id, transfer_id, prior_id, snapshot]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _addon_responsibility_at(addon, boundary):
    """Rebuild the current add-on responsibility at a transfer event time."""
    from .addon_handover import current_responsibilities
    _, shares = current_responsibilities(addon, before=boundary)
    current = [row for row in shares if row['is_current'] and row['basis'] > 0]
    if len(current) > 1:
        raise ValidationError('转单时加购负责人不唯一')
    return (current[0]['segment_id'], current[0]['basis']) if current else (None, 0)


def capture_locked(order, transfer, *, frozen_facts=None, before_revision_id=None, allow_pending=False):
    """Rebuild the actual retained segments and all already posted funding uses."""
    from .points_transfer_refund_access import permits
    if (not transaction.get_connection().in_atomic_block or not permits(order, phase='refund')
            or transfer.previous_segment.attribution.order_id != order.pk
            or not 1 <= order.refund_records.count() <= 2):
        raise ValidationError('该积分退款后转单资金范围尚未开放')
    posting = PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order).first()
    if not posting or not isinstance(posting.participants, (dict, list)):
        raise ValidationError('积分转单退款凭证缺失')
    if order.refund_records.count() == 2:
        later = PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order).exclude(pk=posting.pk).first()
        terms = getattr(later.proposal, 'points_terms', None) if later else None
        if (not before_revision_id or not later or not terms or terms.prior_refund_id != posting.points_refund_id
                or later.created_at <= transfer.created_at):
            raise ValidationError('退款后转单后续退款历史待核对')
    from .points_negotiation_termination import verify_termination_locked
    verify_termination_locked(posting, allow_verified_later_final=order.refund_records.count() == 2)
    closed = set(posting.proposal.affected_segment_ids or [])
    if not closed:
        if isinstance(posting.participants, dict):
            closed = {item['id'] for item in posting.participants['facts']['segments']
                      if item['started_at'] and item['account_id'] in posting.proposal.affected_accounts}
        else:
            closed = {item['segment_id'] for item in posting.participants
                      if item['started_at'] and item['account_id'] in posting.proposal.affected_accounts}
    if transfer.previous_segment_id in closed:
        raise ValidationError('已确认终止的履约段不能再次转单')
    from .points_transfer_refund_evidence import _facts, _reconstruct_from_verified_facts
    current = _facts(order)
    facts = frozen_facts if frozen_facts is not None else current
    if frozen_facts is not None:
        from .points_transfer_refund_evidence import _matches_history
        boundary = PointsPostRefundTransferRevision.objects.filter(pk=before_revision_id).values_list('created_at', flat=True).first()
        if not boundary or not _matches_history(current, order, facts, boundary):
            raise ValidationError('退款后转单历史履约凭证异常')
    if transfer.pk not in {event['id'] for event in facts['transfers']}:
        raise ValidationError('退款后转单履约事实缺失')
    from .models import OrderPrestartHandoff
    from .points_transfer_refund_access import _prestart_slot_counts
    prestart = OrderPrestartHandoff.objects.filter(order=order).order_by('-pk').first()
    counts = _prestart_slot_counts(order, prestart) if prestart else {}
    segments = {item['id']: item for item in facts['segments']}
    for event in facts['transfers']:
        source = segments.get(event['previous_segment_id'])
        if not source:
            raise ValidationError('转单席位来源缺失')
        counts[source['slot']] = counts.get(source['slot'], 0) + 1
    if any(value > 2 for value in counts.values()):
        raise ValidationError('同一席位改派和转单次数超过上限')
    history = _reconstruct_from_verified_facts(order.pk, facts,
        refund_id=posting.points_refund.refund_id, through_transfer_id=transfer.pk)
    income = {line[0]: line[4] for line in history.segment_shares}
    selected = posting.proposal.affected_segment_ids
    for share in posting.shares.all():
        participant_rows = (posting.participants['facts']['segments'] if isinstance(posting.participants, dict)
                            else posting.participants)
        ids = [(item['id'] if isinstance(posting.participants, dict) else item['segment_id'])
               for item in participant_rows if item['account_id'] == share.account_id and item['started_at']
               and (not selected or (item['id'] if isinstance(posting.participants, dict)
                                     else item['segment_id']) in selected)]
        if len(ids) != 1 or ids[0] not in income or share.pending_deduction > income[ids[0]]:
            raise ValidationError('积分退款承担无法唯一归属履约段')
        income[ids[0]] -= share.pending_deduction
    from .points_negotiation_preview import funding_after_refund
    allocations = list(OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order=order) | Q(service_order=order), created_at__lte=transfer.created_at).order_by('pk'))
    from .points_funding_release import _prior_addon_releases
    prior_addon = _prior_addon_releases(allocations, transfer.created_at)
    addon_released = {}
    for _, allocation_id, _, amount in prior_addon:
        addon_released[allocation_id] = addon_released.get(allocation_id, 0) + amount
    rows = []
    for allocation in allocations:
        prior_main = sum(OrderPointsFundingRelease.objects.filter(allocation=allocation,
            posting__created_at__lt=transfer.created_at).values_list('amount', flat=True))
        prior_transfer = sum(OrderPointsTransferFundingRelease.objects.filter(allocation=allocation,
            revision__created_at__lt=transfer.created_at).values_list('amount', flat=True))
        prior_query = PointsPostRefundTransferRelease.objects.filter(allocation=allocation)
        if before_revision_id is not None:
            prior_query = prior_query.filter(revision_id__lt=before_revision_id)
        prior_post = sum(prior_query.values_list('amount', flat=True))
        used = prior_main + prior_transfer + prior_post + addon_released.get(allocation.pk, 0)
        if used > allocation.amount:
            raise ValidationError('积分转单已释放金额超过原分配')
        rows.append([allocation.pk, allocation.receipt_id, allocation.amount, used])
    original, proof = history.funding, posting.points_refund
    shares = list(posting.shares.all())
    result = funding_after_refund(principal_units=original.principal_units, gift_units=original.gift_units,
        compensation_units=original.compensation_units, returned_prepaid_units=proof.prepaid_units,
        returned_gift_units=proof.gift_units, returned_compensation_units=proof.compensation_units,
        provider_income_units=original.provider_income_units,
        pending_deduction_units=sum(item.pending_deduction for item in shares),
        provider_external_units=sum(item.wallet_deduction + item.deposit_deduction for item in shares),
        inviter_rate=facts['inviter_commission_rate'] if facts['inviter_id'] or facts['inviter_account_id'] else 0,
        allocated_units=sum(item[2] - item[3] for item in rows),
        rounding_reserved_units=posting.rounding_to_store_units)
    if sum(income.values()) != result.provider_income_units:
        raise ValidationError('积分退款后的逐段收益不守恒')
    from .models import OrderAddonPayment
    addons = list(OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__lt=transfer.created_at))
    addon_history = None
    pending_handover = []
    if addons:
        from .addon_handover import verify_handover
        from .points_addon_receipt_usage import payment_facts, facts as addon_refund_facts
        witness = allocations[0].receipt_id if allocations else None
        if isinstance(posting.participants, list):
            from .points_first_transfer_addon_history import verify as verify_addon_history
            first_version = (PointsPostRefundTransferRevision.objects.filter(order=order,
                created_at__lt=transfer.created_at).order_by('created_at', 'pk').first())
            if first_version:
                from .points_transfer_addon_events import later_runs
                verify_addon_history(order, posting, receipt_id=witness,
                                     before=first_version.transfer.created_at)
                later_runs(order, posting, before=transfer.created_at)
            else:
                verify_addon_history(order, posting, receipt_id=witness, before=transfer.created_at)
        for addon in addons:
            from .models import OrderAddonHandover
            from django.db.models import Q
            unfinished = OrderAddonHandover.objects.filter(payment=addon, created_at__lte=transfer.created_at).filter(
                Q(status='PENDING') | Q(status='ACCEPTED', accepted_at__gt=transfer.created_at))
            if unfinished.filter(Q(source_segment__slot=transfer.previous_segment.slot)
                                 | Q(target_segment__slot=transfer.previous_segment.slot)).exists():
                raise ValidationError('转单时加购接替尚待确认')
            responsible, remaining = _addon_responsibility_at(addon, transfer.created_at)
            if responsible == transfer.previous_segment_id:
                if not allow_pending:
                    raise ValidationError('转出陪玩仍负责加购，请先完成独立接替')
                previous = addon.handovers.filter(status='ACCEPTED', accepted_at__lt=transfer.created_at).order_by('-version').first()
                if previous and previous.target_segment_id != responsible:
                    raise ValidationError('加购再次转出来源待核对')
                if previous and previous.previous_id:
                    from .models import OrderAddonFundingReleaseRun
                    if not OrderAddonFundingReleaseRun.objects.filter(handover=previous,
                            points_handover_snapshot__kind='SAME_TEAM_V1').exists():
                        raise ValidationError('加购再次转出缺少同单资金前版')
                pending_handover.append([addon.pk, responsible, remaining])
            payment_facts(addon, read_receipt_id=witness)
            for refund in addon.refund_records.filter(created_at__lt=transfer.created_at).order_by('created_at', 'pk'):
                addon_refund_facts(refund, read_receipt_id=witness, _event_history=False)
            if addon.refund_records.filter(created_at=transfer.created_at).exists():
                raise ValidationError('加购退款与转单时序无法区分')
            for handover in addon.handovers.filter(status='ACCEPTED', accepted_at__lt=transfer.created_at):
                verify_handover(addon, handover, as_of=transfer.created_at, receipt_id=witness)
        from collections import defaultdict
        from .addon_funding_history import combined_at
        from .refunded_addon_funding import combine_refunded_history
        retained = defaultdict(int)
        for line in history.segment_shares:
            retained[line[1]] += income[line[0]]
        combined = combined_at(order, result, transfer.created_at, segment_shares=[
            (line[0], line[1], line[2], line[3], income[line[0]]) for line in history.segment_shares])
        result = combine_refunded_history(result, combined, main_retained_by_account=retained)
        addon_history = combined['addon_history']
    from .points_funding_allocation import plan_release
    parts = plan_release(allocations=[(item[0], item[2], item[3]) for item in rows], amount=result.release_units)
    result = {'posting_id': posting.pk, 'refund_id': proof.refund_id, 'facts': facts,
            'closed_segments': sorted(closed), 'segment_income': [list(item) for item in sorted(income.items())],
            'allocations': rows, 'funding': asdict(result), 'addon_history': addon_history,
            'releases': [[item.allocation_id, item.amount] for item in parts]}
    if pending_handover:
        result['pending_handover'] = sorted(pending_handover)
    return result


def verify_locked(revision):
    if revision.evidence_hash != _digest(revision.order_id, revision.transfer_id, revision.prior_id, revision.snapshot):
        raise ValidationError('退款后积分转单资金摘要异常')
    previous = PointsPostRefundTransferRevision.objects.filter(order=revision.order, pk__lt=revision.pk).order_by('-pk').first()
    if revision.prior_id != (previous.pk if previous else None):
        raise ValidationError('退款后积分转单资金前驱异常')
    actual = capture_locked(revision.order, revision.transfer, frozen_facts=revision.snapshot.get('facts'),
                            before_revision_id=revision.pk,
                            allow_pending=bool(revision.snapshot.get('pending_handover')))
    rows = list(revision.releases.order_by('-allocation_id').values_list('allocation_id', 'amount'))
    if actual != revision.snapshot or rows != [tuple(item) for item in revision.snapshot['releases']]:
        raise ValidationError('退款后积分转单资金版本不一致')
    return revision


def unresolved_pending_locked(revision):
    """Resolve only the exact add-on liabilities frozen by this transfer."""
    from .addon_handover import verify_handover
    from .models import OrderAddonPayment
    pending = revision.snapshot.get('pending_handover', [])
    if not isinstance(pending, list) or any(not isinstance(row, list) or len(row) != 3
            or any(type(value) is not int or value <= 0 for value in row) for row in pending):
        raise ValidationError('转单待接替负债格式异常')
    if len({row[0] for row in pending}) != len(pending):
        raise ValidationError('转单待接替加购重复')
    unresolved = []
    for payment_id, segment_id, amount in pending:
        payment = OrderAddonPayment.objects.filter(pk=payment_id, intent__order=revision.order).first()
        if not payment or segment_id != revision.transfer.previous_segment_id:
            raise ValidationError('转单待接替加购归属异常')
        handovers = list(payment.handovers.filter(status='ACCEPTED', source_segment_id=segment_id,
            target_segment_id=revision.transfer.next_segment_id, accepted_at__gt=revision.transfer.created_at)
            .order_by('accepted_at', 'pk'))
        from django.utils import timezone
        from .points_transfer_addon_liability import effective_at
        effective = effective_at(revision, payment,
                                 handovers[0].created_at if handovers else timezone.now())
        if not handovers:
            if effective[2]:
                unresolved.append(effective)
            continue
        if (len(handovers) != 1 or handovers[0].source_basis_before != effective[2]
                or handovers[0].created_at <= revision.created_at
                or handovers[0].accepted_at <= handovers[0].created_at):
            raise ValidationError('转单后加购接替份额异常')
        first_later_refund = payment.refund_records.filter(created_at__gt=handovers[0].accepted_at)\
            .order_by('created_at', 'pk').first()
        from django.db.models import Q
        from .models import OrderPointsFundingAllocation
        witness = OrderPointsFundingAllocation.objects.filter(
            Q(redemption__order=payment.intent.order) | Q(service_order=payment.intent.order)
        ).order_by('pk').first()
        verify_handover(payment, handovers[0],
            as_of=first_later_refund.created_at if first_later_refund else None,
            receipt_id=witness.receipt_id if witness else None)
        run = getattr(handovers[0], 'funding_release_run', None)
        funding_version = PointsPostRefundTransferRevision.objects.filter(order=revision.order,
            created_at__lt=handovers[0].accepted_at).order_by('-created_at', '-pk').first()
        followup_valid = False
        if run and isinstance(run.points_followup_snapshot, dict):
            from .models import OrderPointsTransferFollowupFundingRevision
            from .points_transfer_followup_pending import verify_snapshot
            followup = OrderPointsTransferFollowupFundingRevision.objects.filter(
                pk=run.points_followup_snapshot.get('followup_revision_id'),
                posting__points_refund__redemption__order=revision.order).select_related(
                    'posting__proposal__case__order', 'posting__points_refund__refund').first()
            if (followup and followup.posting.created_at < handovers[0].accepted_at
                    and run.points_followup_snapshot.get('kind') == 'TRANSFER_FOLLOWUP_V1'
                    and any(isinstance(item, dict) and item.get('handover_id') == handovers[0].pk
                            and item.get('payment_id') == payment.pk and item.get('transfer_revision_id') == revision.pk
                            for item in followup.pending_handover_snapshot)):
                verify_snapshot(followup.posting, followup.pending_handover_snapshot,
                                receipt_id=witness.receipt_id if witness else None)
                followup_valid = True
        main_valid = bool(run and isinstance(run.refunded_main_snapshot, dict) and funding_version
                          and run.refunded_main_snapshot.get('transfer_revision_id') == funding_version.pk)
        if not (main_valid or followup_valid):
            raise ValidationError('转单后加购接替资金版本缺失')
        from .addon_funding_release import verify_run
        verify_run(run)
    return unresolved


def record_locked(order, transfer, *, allow_pending=False):
    """Record the version and its exact release rows in the caller's transaction."""
    if order.refund_records.count() == 2:
        from .points_followup_transfer_revision import record_locked as record_followup_transfer
        return record_followup_transfer(order, transfer, allow_pending=allow_pending)
    existing = PointsPostRefundTransferRevision.objects.filter(transfer=transfer).first()
    if existing:
        raise ValidationError('退款后转单资金版本已存在，请使用原操作凭证核对')
    snapshot = capture_locked(order, transfer, allow_pending=allow_pending)
    prior = PointsPostRefundTransferRevision.objects.filter(order=order).order_by('-pk').first()
    if prior:
        from .points_transfer_addon_liability import first_refund_pending_allowed
        for version in PointsPostRefundTransferRevision.objects.filter(order=order).order_by('pk'):
            verify_locked(version)
            unresolved = unresolved_pending_locked(version)
            if (version.created_at >= transfer.created_at
                    or (unresolved and (version.transfer.previous_segment.slot == transfer.previous_segment.slot
                        or not all(first_refund_pending_allowed(order, item[0]) for item in unresolved)))):
                raise ValidationError('此前转单的加购责任尚未完成接替')
    if snapshot.get('pending_handover'):
        from .models import OrderOriginalShareTransfer
        from .points_transfer_refund_access import first_handover_refund
        transfers = list(OrderOriginalShareTransfer.objects.filter(
            previous_segment__attribution__order=order).order_by('pk'))
        if not prior and not first_handover_refund(order, transfers):
            from .models import OrderAddonPayment, OrderPrestartHandoff, OrderAddonFundingReleaseRun
            payments = list(OrderAddonPayment.objects.filter(intent__order=order))
            accepted = [row for payment in payments for row in payment.handovers.filter(status='ACCEPTED')]
            team_history = bool(accepted and all(OrderAddonFundingReleaseRun.objects.filter(handover=row,
                points_handover_snapshot__kind='SAME_TEAM_V1').exists() for row in accepted))
            # Rejected and refund-invalidated attempts have no transferred
            # responsibility; the pre-transfer history verifier checks their
            # real decisions and refund facts. Only accepted history needs a
            # matching same-team funding event here.
            if (not payments
                    or (accepted and not team_history)
                    or not {row[0] for row in snapshot['pending_handover']}.issubset(
                        {payment.pk for payment in payments})
                    or OrderPrestartHandoff.objects.filter(order=order).exists()):
                raise ValidationError('待接替加购缺少可核验的既有接替来源')
    # Preserve the actual shortfall at this event. Subsequent verified receipt
    # allocations are counted by verify_order_funding before settlement.
    row = PointsPostRefundTransferRevision.objects.create(order=order, transfer=transfer, prior=prior,
        snapshot=snapshot, evidence_hash=_digest(order.pk, transfer.pk, prior.pk if prior else None, snapshot))
    PointsPostRefundTransferRelease.objects.bulk_create([
        PointsPostRefundTransferRelease(revision=row, allocation_id=allocation_id, amount=amount)
        for allocation_id, amount in snapshot['releases']])
    allocations = list(OrderPointsFundingAllocation.objects.filter(
        pk__in=[item[0] for item in snapshot['allocations']], receipt__usage_version__isnull=False)
        .select_related('receipt', 'redemption').order_by('receipt_id', 'pk'))
    if allocations:
        from types import SimpleNamespace
        from .points_post_refund_transfer_receipt_usage import facts
        from users.receipt_usage_sources import record_post_refund_transfer_release_locked, verify_allocations_locked
        facts(SimpleNamespace(revision=row, allocation=allocations[0], pk=0, amount=0))
        touched = set()
        for release in row.releases.select_related('allocation__receipt', 'allocation__redemption',
                                                    'revision__order', 'revision__transfer').order_by('allocation__receipt_id', 'pk'):
            if release.allocation.receipt.usage_version is not None:
                record_post_refund_transfer_release_locked(release)
                touched.add(release.allocation.receipt_id)
        for receipt_id in sorted(touched):
            verify_allocations_locked(receipt_id)
    return row
