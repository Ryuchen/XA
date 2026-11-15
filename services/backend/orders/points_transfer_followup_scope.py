"""Validate a later refund's exact participation against the closed episode."""
from rest_framework.exceptions import ValidationError


def _fully_refunded_liability(version, payment, boundary, *, receipt_id=None):
    """Prove a frozen liability ended through actual refunds, not a handover."""
    from .points_transfer_addon_liability import effective_at
    refunds = list(payment.refund_records.filter(created_at__lt=boundary).order_by('created_at', 'pk'))
    if (not refunds or payment.refund_records.filter(created_at=boundary).exists()
            or sum(row.amount for row in refunds) != payment.intent.amount):
        return False
    liability = effective_at(version, payment, boundary, witness_receipt_id=receipt_id)
    return liability == [payment.pk, version.transfer.previous_segment_id, 0]


def addon_chain_before(order, first, boundary, *, pending_snapshot=None):
    """Verify the frozen transfer and add-on funding events before a later refund."""
    if pending_snapshot is None:
        from .points_transfer_followup_pending import current_snapshot
        pending_snapshot = current_snapshot(order)
    from .models import OrderAddonPayment, PointsPostRefundTransferRevision
    from .points_post_refund_transfer import verify_locked, unresolved_pending_locked
    from .points_transfer_addon_events import later_runs
    if not boundary or first.created_at >= boundary:
        raise ValidationError('追加退款的加购事件边界异常')
    payments = list(OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__lt=boundary).select_related('buyer_transaction').order_by('pk'))
    if (not payments
            or any(not item.buyer_transaction.created_at for item in payments)
            or OrderAddonPayment.objects.filter(intent__order=order,
                buyer_transaction__created_at=boundary).exists()):
        raise ValidationError('追加退款的加购付款范围待核对')
    versions = list(PointsPostRefundTransferRevision.objects.filter(order=order,
        created_at__lt=boundary).order_by('pk'))
    if not versions:
        return addon_snapshot_from_posting(order, first, before=boundary)
    if len(versions) > 4:
        raise ValidationError('追加退款的转单资金版本待核对')
    for index, version in enumerate(versions):
        if (version.prior_id != (versions[index - 1].pk if index else None)
                or version.transfer.created_at >= boundary
                or (index and version.transfer.created_at <= versions[index - 1].created_at)):
            raise ValidationError('追加退款的转单资金版本顺序异常')
        verify_locked(version)
        allowed = {(item.get('transfer_revision_id'), item.get('payment_id'))
                   for item in (pending_snapshot or []) if isinstance(item, dict)}
        if any((version.pk, item[0]) not in allowed for item in unresolved_pending_locked(version)):
            raise ValidationError('追加退款前的加购接替尚未完成')
        for payment_id, source_id, _ in version.snapshot.get('pending_handover', []):
            payment = next((item for item in payments if item.pk == payment_id), None)
            if not payment or (not payment.handovers.filter(status='ACCEPTED',
                    source_segment_id=source_id, target_segment_id=version.transfer.next_segment_id,
                    accepted_at__lt=boundary).exists()
                    and not _fully_refunded_liability(version, payment, boundary)
                    and (version.pk, payment_id) not in allowed):
                raise ValidationError('追加退款前的接替时间待核对')
    history = versions[-1].snapshot.get('addon_history')
    for run in later_runs(order, first):
        event_at = (run.payment.buyer_transaction.created_at if run.payment_id else
                    run.refund.created_at if run.refund_id else run.handover.accepted_at)
        if not event_at or event_at == boundary:
            raise ValidationError('追加退款与加购事件时序无法区分')
        if event_at >= boundary:
            continue
        preceding = [version for version in versions if version.created_at < event_at]
        snapshot = run.refunded_main_snapshot
        if (not isinstance(snapshot, dict) or not isinstance(snapshot.get('addon_history'), list)
                or (preceding and snapshot.get('transfer_revision_id') != preceding[-1].pk)
                or (not preceding and snapshot.get('transfer_origin_posting_id') != first.pk
                    and not (isinstance(first.participants, list)
                             and run.created_at < versions[0].transfer.created_at
                             and snapshot.get('posting_id') == first.pk
                             and not snapshot.get('transfer_revision_id')))):
            raise ValidationError('追加退款前加购事件资金版本异常')
        if preceding and preceding[-1].pk == versions[-1].pk:
            history = snapshot['addon_history']
    if not isinstance(history, list) or len(history) != len(payments):
        raise ValidationError('追加退款前的加购资金历史不完整')
    by_id = {item.pk: item for item in payments}
    ids = [item.get('payment_id') for item in history if isinstance(item, dict)]
    if len(ids) != len(history) or len(set(ids)) != len(ids) or set(ids) != set(by_id):
        raise ValidationError('追加退款前的加购付款归属异常')
    for item in history:
        payment = by_id[item['payment_id']]
        refunds = list(payment.refund_records.filter(created_at__lt=boundary).order_by('created_at', 'pk'))
        if (payment.refund_records.filter(created_at=boundary).exists()
                or item.get('refund_ids') != [row.pk for row in refunds]):
            raise ValidationError('追加退款前的加购退款顺序异常')
        accepted = payment.handovers.filter(status='ACCEPTED', accepted_at__lt=boundary).order_by('-version').first()
        if item.get('handover_id') != (accepted.pk if accepted else None):
            raise ValidationError('追加退款前的加购责任版本异常')
    return history


def addon_chain_receipt_before(order, first, boundary, receipt_id, *, pending_snapshot=None):
    """Receipt-local version of the pre-followup add-on event chain."""
    from types import SimpleNamespace
    from django.db.models import Q
    from django.core.exceptions import ValidationError as DjangoValidationError
    from users.receipt_usage_ledger import _locked_receipt
    from users.receipt_usage_sources import _addon_release_facts, _require_current_unlocked_reads
    from .models import (OrderAddonPayment, OrderAddonRefund, OrderAddonHandover,
                         OrderAddonFundingReleaseRun, OrderPointsFundingAllocation, PointsPostRefundTransferRevision)
    from .points_post_refund_transfer_receipt_usage import facts as version_facts
    from .points_transfer_addon_zero_receipt import facts as zero_facts
    _locked_receipt(receipt_id)
    _require_current_unlocked_reads()
    witness = OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order=order) | Q(service_order=order), receipt_id=receipt_id).order_by('pk').first()
    versions = list(PointsPostRefundTransferRevision.objects.filter(order=order,
        created_at__lt=boundary).order_by('pk'))
    payments = list(OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__lt=boundary).select_related('buyer_transaction').order_by('pk'))
    if not witness or len(versions) > 4 or not payments:
        raise DjangoValidationError('追加退款的到账见证或加购版本缺失')
    if not versions:
        from .models import OrderOriginalShareTransfer
        if (OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
                created_at__gte=first.created_at, created_at__lte=boundary).exists()
                or any(not item.buyer_transaction.created_at for item in payments)):
            raise DjangoValidationError('首次退款后的加购或转单来源待核对')
    sources = []
    allowed = {(item.get('transfer_revision_id'), item.get('payment_id'))
               for item in (pending_snapshot or []) if isinstance(item, dict)}
    for index, version in enumerate(versions):
        if (version.prior_id != (versions[index - 1].pk if index else None)
                or (index and version.transfer.created_at <= versions[index - 1].created_at)):
            raise DjangoValidationError('追加退款的转单到账版本顺序异常')
        ids = [row[0] for row in version.snapshot.get('allocations', [])]
        allocation = (OrderPointsFundingAllocation.objects.filter(pk__in=ids).order_by('pk').first()
                      if ids else witness)
        if not allocation:
            raise DjangoValidationError('追加退款的转单到账分配缺失')
        sources.append(version_facts(SimpleNamespace(revision=version, allocation=allocation,
            pk=0, amount=0, read_receipt_id=receipt_id)))
        for payment_id, segment_id, _ in version.snapshot.get('pending_handover', []):
            payment = next((item for item in payments if item.pk == payment_id), None)
            accepted = OrderAddonHandover.objects.filter(payment_id=payment_id, status='ACCEPTED',
                    source_segment_id=segment_id, target_segment_id=version.transfer.next_segment_id,
                    accepted_at__gt=version.transfer.created_at, accepted_at__lt=boundary).exists()
            try:
                ended = bool(payment and not accepted and _fully_refunded_liability(
                    version, payment, boundary, receipt_id=receipt_id))
            except ValidationError as exc:
                raise DjangoValidationError('追加退款前的加购退款责任待核对') from exc
            if not payment or not (accepted or ended or (version.pk, payment_id) in allowed):
                raise DjangoValidationError('追加退款前的加购接替尚未完成')
    events = [(item.buyer_transaction.created_at, 'payment', item.pk) for item in payments
              if item.buyer_transaction.created_at > first.created_at]
    refunds = list(OrderAddonRefund.objects.filter(order=order, created_at__gt=first.created_at,
                                                   created_at__lt=boundary).order_by('created_at', 'pk'))
    handovers = list(OrderAddonHandover.objects.filter(payment__intent__order=order, status='ACCEPTED',
        accepted_at__gt=first.created_at, accepted_at__lt=boundary).order_by('accepted_at', 'pk'))
    events.extend((item.created_at, 'refund', item.pk) for item in refunds)
    events.extend((item.accepted_at, 'handover', item.pk) for item in handovers)
    if (any(not event[0] or event[0] >= boundary for event in events)
            or len({event[0] for event in events}) != len(events)):
        raise DjangoValidationError('追加退款前加购事件顺序异常')
    if versions:
        history = versions[-1].snapshot.get('addon_history')
    else:
        evidence = getattr(first, 'funding_release_evidence', None)
        history = evidence.addon_funding_snapshot if evidence else None
    for at, kind, pk in sorted(events):
        run = OrderAddonFundingReleaseRun.objects.filter(**{kind + '_id': pk}).first()
        preceding = [item for item in versions if item.created_at < at]
        snapshot = run.refunded_main_snapshot if run else None
        if (not run or not isinstance(snapshot, dict) or not isinstance(snapshot.get('addon_history'), list)
                or (preceding and snapshot.get('transfer_revision_id') != preceding[-1].pk)
                or (not preceding and snapshot.get('transfer_origin_posting_id') != first.pk
                    and not (versions and isinstance(first.participants, list)
                             and run.created_at < versions[0].transfer.created_at
                             and snapshot.get('posting_id') == first.pk
                             and not snapshot.get('transfer_revision_id')))):
            raise DjangoValidationError('追加退款前加购资金版本异常')
        releases = list(run.releases.select_related('allocation__redemption', 'run').order_by('pk'))
        if releases:
            sources.extend(_addon_release_facts(row, witness_receipt_id=receipt_id) for row in releases)
        else:
            sources.append(zero_facts(run, receipt_id=receipt_id))
        if (preceding and preceding[-1].pk == versions[-1].pk) or not versions:
            history = snapshot['addon_history']
    if not isinstance(history, list) or len(history) != len(payments):
        raise DjangoValidationError('追加退款的加购资金历史缺失')
    ids = [item.get('payment_id') for item in history if isinstance(item, dict)]
    if len(ids) != len(history) or len(set(ids)) != len(ids) or set(ids) != {item.pk for item in payments}:
        raise DjangoValidationError('追加退款加购付款归属异常')
    for item in history:
        payment = next(row for row in payments if row.pk == item['payment_id'])
        if (item.get('refund_ids') != list(payment.refund_records.filter(created_at__lt=boundary)
                .order_by('created_at', 'pk').values_list('pk', flat=True))):
            raise DjangoValidationError('追加退款加购退款顺序异常')
        active = payment.handovers.filter(status='ACCEPTED', accepted_at__lt=boundary).order_by('-version').first()
        if item.get('handover_id') != (active.pk if active else None):
            raise DjangoValidationError('追加退款加购责任版本异常')
    return history, sources


def addon_snapshot(order, first):
    """Select the immutable add-on facts valid after the first refund."""
    from .models import OrderAddonPayment, OrderAddonFundingReleaseRun, PointsPostRefundTransferRevision
    payments = list(OrderAddonPayment.objects.filter(intent__order=order).select_related('buyer_transaction'))
    if (len(payments) != 1 or payments[0].handovers.exists()
            or not payments[0].buyer_transaction.created_at
            or payments[0].buyer_transaction.created_at >= first.created_at):
        return None
    refunds = list(payments[0].refund_records.order_by('pk'))
    if (any(not refund.created_at or refund.created_at == first.created_at for refund in refunds)
            or any(left.created_at >= right.created_at for left, right in zip(refunds, refunds[1:]))):
        return None
    evidence = getattr(first, 'funding_release_evidence', None)
    snapshot = evidence.addon_funding_snapshot if evidence else None
    if refunds and refunds[0].created_at > first.created_at:
        version = PointsPostRefundTransferRevision.objects.filter(order=order, prior__isnull=True).first()
        if not version:
            return None
        for index, refund in enumerate(refunds):
            run = OrderAddonFundingReleaseRun.objects.filter(refund=refund).first()
            if (not run or not isinstance(run.refunded_main_snapshot, dict)
                    or run.refunded_main_snapshot.get('transfer_revision_id') != version.pk
                    or not run.refunded_main_hash or not run.points_addon_hash
                    or not version.created_at < refund.created_at <= run.created_at):
                return None
            snapshot = run.refunded_main_snapshot.get('addon_history')
            if (not isinstance(snapshot, list) or len(snapshot) != 1 or not isinstance(snapshot[0], dict)
                    or snapshot[0].get('refund_ids') != [item.pk for item in refunds[:index + 1]]):
                return None
    elif refunds and refunds[-1].created_at > first.created_at:
        return None
    if (not isinstance(snapshot, list) or len(snapshot) != 1 or not isinstance(snapshot[0], dict)
            or snapshot[0].get('payment_id') != payments[0].pk
            or snapshot[0].get('refund_ids') != [refund.pk for refund in refunds]
            or not isinstance(snapshot[0].get('main_deductions'), list)):
        return None
    return snapshot


def supported_addon(order, first):
    from .models import OrderAddonPayment
    return (not OrderAddonPayment.objects.filter(intent__order=order).exists()
            or addon_snapshot(order, first) is not None)


def addon_snapshot_from_posting(order, first, *, before=None):
    """Verify the add-on event chain when no transfer follows the main refund."""
    from .models import OrderAddonPayment, OrderOriginalShareTransfer, PointsPostRefundTransferRevision
    from .points_transfer_addon_events import later_runs
    versions = PointsPostRefundTransferRevision.objects.filter(order=order)
    transfers = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
                                                             created_at__gte=first.created_at)
    if before is not None:
        if transfers.filter(created_at=before).exists():
            raise ValidationError('转单与追加退款时序无法区分')
        versions = versions.filter(created_at__lt=before)
        transfers = transfers.filter(created_at__lt=before)
    if versions.exists() or transfers.exists():
        raise ValidationError('主单退款后的转单资金版本待核对')
    payment_query = OrderAddonPayment.objects.filter(intent__order=order).select_related('buyer_transaction')
    if before is not None:
        if payment_query.filter(buyer_transaction__created_at=before).exists():
            raise ValidationError('加购付款与追加退款时序无法区分')
        payment_query = payment_query.filter(buyer_transaction__created_at__lt=before)
    payments = list(payment_query)
    if not payments or any(not payment.buyer_transaction.created_at for payment in payments):
        raise ValidationError('主单退款后的加购付款范围待核对')
    evidence = getattr(first, 'funding_release_evidence', None)
    history = evidence.addon_funding_snapshot if evidence else None
    later = later_runs(order, first, before=before)
    for run in later:
        snapshot = run.refunded_main_snapshot
        if (not (run.payment_id or run.refund_id or run.handover_id) or not isinstance(snapshot, dict)
                or snapshot.get('transfer_origin_posting_id') != first.pk
                or snapshot.get('transfer_revision_id') or not isinstance(snapshot.get('addon_history'), list)):
            raise ValidationError('主单退款后的加购事件版本待核对')
        history = snapshot['addon_history']
    if not isinstance(history, list) or len(history) != len(payments):
        raise ValidationError('主单退款后的加购来源不完整')
    ids = [item.get('payment_id') for item in history if isinstance(item, dict)]
    if len(ids) != len(history) or len(set(ids)) != len(ids) or set(ids) != {item.pk for item in payments}:
        raise ValidationError('主单退款后的加购付款归属不一致')
    by_id = {item.pk: item for item in payments}
    for item in history:
        refunds = by_id[item['payment_id']].refund_records
        if before is not None:
            if refunds.filter(created_at=before).exists():
                raise ValidationError('加购退款与追加退款时序无法区分')
            refunds = refunds.filter(created_at__lt=before)
        refunds = list(refunds.order_by('created_at', 'pk'))
        if item.get('refund_ids') != [row.pk for row in refunds]:
            raise ValidationError('主单退款后的加购退款顺序不一致')
        handovers = by_id[item['payment_id']].handovers.filter(status='ACCEPTED')
        if before is not None:
            if handovers.filter(accepted_at=before).exists():
                raise ValidationError('加购接替与追加退款时序无法区分')
            handovers = handovers.filter(accepted_at__lt=before)
        latest = handovers.order_by('-version', '-pk').first()
        if item.get('handover_id') != (latest.pk if latest else None):
            raise ValidationError('主单退款后的加购接替责任不一致')
    return history


def combine_addon(order, main, boundary, lines, segment_facts, pending):
    """Rebuild the unchanged add-on after exact per-segment main deductions."""
    from collections import defaultdict
    from .addon_funding_history import combined_at
    from .refunded_addon_funding import combine_refunded_history
    from .points_funding_plan import PointsFundingPlanError
    income = {line[0]: line[4] for line in lines}
    retained = defaultdict(int)
    for line in lines:
        retained[line[1]] += line[4]
    by_id = {item['id']: item for item in segment_facts}
    for entry in pending:
        account, amount, selected = entry[:3]
        recorded = entry[3] if len(entry) == 4 else None
        retained[account] -= amount
        ids = [line[0] for line in lines if line[1] == account and by_id[line[0]]['started_at']
               and (not selected or line[0] in selected)]
        if recorded is not None:
            if (any(type(segment_id) is not int or type(units) is not int or units <= 0
                    for segment_id, units in recorded)
                    or len({segment_id for segment_id, _ in recorded}) != len(recorded)
                    or any(segment_id not in ids for segment_id, _ in recorded)
                    or sum(units for _, units in recorded) != amount):
                raise ValidationError('主单逐段扣减凭证异常')
            for segment_id, units in recorded:
                income[segment_id] -= units
        elif amount:
            if len(ids) != 1:
                raise ValidationError('主单扣减须明确唯一履约段')
            income[ids[0]] -= amount
    if any(value < 0 for value in retained.values()) or any(value < 0 for value in income.values()):
        raise ValidationError('加购与主单收益分摊异常')
    try:
        history = combined_at(order, main, boundary, segment_shares=[
            (line[0], line[1], line[2], line[3], income[line[0]]) for line in lines])
        return combine_refunded_history(main, history, main_retained_by_account=retained), history['addon_history']
    except PointsFundingPlanError as exc:
        raise ValidationError('转单追加退款加购资金待核对') from exc


def verify(first, second, version):
    overlap = set(first.proposal.affected_accounts) & set(second.proposal.affected_accounts)
    if not overlap:
        return
    selected = second.proposal.affected_segment_ids
    if (not isinstance(selected, list) or not selected or len(set(selected)) != len(selected)
            or any(type(item) is not int or item <= 0 for item in selected)):
        raise ValidationError('同一陪玩追加退款须明确新履约段')
    if version:
        if not isinstance(version.snapshot, dict):
            raise ValidationError('追加退款的履约版本异常')
        closed = version.snapshot.get('closed_segments')
        facts = version.snapshot.get('facts')
    else:
        participants = first.participants
        facts = participants.get('facts') if isinstance(participants, dict) else None
        if not isinstance(facts, dict) or not isinstance(facts.get('segments'), list):
            raise ValidationError('首次退款的履约事实缺失')
        closed = first.proposal.affected_segment_ids or [item.get('id') for item in facts['segments']
            if isinstance(item, dict) and item.get('started_at')
            and item.get('account_id') in first.proposal.affected_accounts]
    if not isinstance(closed, list) or not isinstance(facts, dict) or set(selected) & set(closed):
        raise ValidationError('已确认结束的履约段不能重开')
    segments = {item.get('id'): item for item in facts.get('segments', []) if isinstance(item, dict)}
    if (any(item not in segments or not segments[item].get('started_at') for item in selected)
            or {segments[item].get('account_id') for item in selected} != set(second.proposal.affected_accounts)):
        raise ValidationError('追加退款所选段与实际受影响陪玩不一致')
