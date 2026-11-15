"""Transactional funding evidence for a transfer after two points refunds.

The public transfer capability remains closed until receipt replay and final
settlement consume this version.  Unsupported cases fail atomically.
"""
from dataclasses import asdict
import hashlib
import json

from rest_framework.exceptions import ValidationError

from stores.db import transaction


def _digest(order_id, transfer_id, first_id, second_id, followup_id, snapshot):
    payload = [order_id, transfer_id, first_id, second_id, followup_id, snapshot]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def record_locked(order, transfer, *, allow_pending=False, _existing=None):
    """Write a version only inside the real transfer's order-money transaction."""
    from .points_transfer_refund_access import permits
    from .models import (OrderAddonPayment, OrderAddonFundingRelease, OrderPointsFundingRelease, OrderPointsTransferFundingRelease,
        PointsPostRefundTransferRelease, OrderPointsTransferFollowupFundingRevision,
        PointsFollowupTransferRevision, PointsFollowupTransferRelease)
    if (not transaction.get_connection().in_atomic_block or not permits(order, phase='refund')
            or transfer.previous_segment.attribution.order_id != order.pk
            or (not _existing and order.refund_records.count() != 2)
            or (_existing and order.refund_records.filter(
                pk__lte=_existing.second_posting.points_refund.refund_id).count() != 2)
            or (not _existing and (order.settled_at or order.providers.filter(settled_at__isnull=False).exists()))):
        raise ValidationError('二次退款后转单资金范围尚未开放')
    followup = (OrderPointsTransferFollowupFundingRevision.objects.select_for_update()
        .select_related('first_posting__points_refund', 'posting__points_refund', 'posting__proposal')
        .filter(posting__points_refund__redemption__order=order).first())
    versions = PointsFollowupTransferRevision.objects.filter(order=order)
    if (not followup or followup.created_at >= transfer.created_at
            or versions.exclude(pk=_existing.pk if _existing else None).exists()):
        raise ValidationError('二次退款后转单前版或事件顺序待核对')
    from .points_transfer_followup_revision import _verify_followup_revision_facts_locked
    previous_funding, _ = _verify_followup_revision_facts_locked(followup)
    from .points_transfer_followup_addon_version import latest_run_locked
    latest = latest_run_locked(order, followup, before=transfer.created_at)
    if latest:
        from .points_negotiation_preview import RefundedFunding
        previous_funding = RefundedFunding(**latest.points_followup_snapshot['funding'])
    first, second = followup.first_posting, followup.posting
    if (not first.termination_processed_at or not second.termination_processed_at
            or first.termination_processed_at >= transfer.created_at
            or second.termination_processed_at >= transfer.created_at
            or not second.termination_in_service or second.completed_by_negotiation
            or second.created_at >= transfer.created_at):
        raise ValidationError('两次退款及当前履约状态待核对')
    from .points_transfer_refund_evidence import _facts, _reconstruct_from_verified_facts
    current_facts = _facts(order)
    if _existing:
        from .points_transfer_refund_evidence import _matches_history
        facts = _existing.snapshot.get('facts') if isinstance(_existing.snapshot, dict) else None
        if not isinstance(facts, dict) or not _matches_history(current_facts, order, facts, _existing.created_at):
            raise ValidationError('二次退款后转单历史履约事实异常')
    else:
        facts = current_facts
    transfers = facts.get('transfers', [])
    if not transfers or transfers[-1]['id'] != transfer.pk:
        raise ValidationError('转入履约事件不是当前最新版本')
    segments = {item['id']: item for item in facts['segments']}
    counts = {}
    for event in transfers:
        source = segments.get(event['previous_segment_id'])
        if not source:
            raise ValidationError('转单席位来源缺失')
        slot = source['slot']
        counts[slot] = counts.get(slot, 0) + 1
    from .models import OrderPrestartHandoff
    prestart = OrderPrestartHandoff.objects.filter(order=order).order_by('-pk').first()
    if prestart:
        from .points_transfer_refund_access import _prestart_slot_counts
        prior_counts = _prestart_slot_counts(order, prestart)
        counts = {slot: count + prior_counts.get(slot, 0) for slot, count in counts.items()} | {
            slot: count for slot, count in prior_counts.items() if slot not in counts}
    if any(count > 2 for count in counts.values()):
        raise ValidationError('同一席位改派和转单次数超过上限')
    history = _reconstruct_from_verified_facts(order.pk, facts,
        refund_id=first.points_refund.refund_id, through_transfer_id=transfer.pk)
    incomes = {line[0]: line[4] for line in history.segment_shares}
    deductions = []
    for posting in (first, second):
        source = posting.participants
        source_segments = (source.get('facts', {}).get('segments', []) if isinstance(source, dict)
                           else source if isinstance(source, list) else [])
        selected = set(posting.proposal.affected_segment_ids or [])
        closed = selected or {(row.get('id') if isinstance(source, dict) else row.get('segment_id'))
            for row in source_segments if row.get('started_at')
            and row.get('account_id') in posting.proposal.affected_accounts}
        if transfer.previous_segment_id in closed:
            raise ValidationError('已确认终止的履约段不能再次转单')
        for share in posting.shares.order_by('pk'):
            parts = list(share.segment_deductions.order_by('segment_id').values_list('segment_id', 'amount'))
            if not parts and share.pending_deduction:
                ids = [(row.get('id') if isinstance(source, dict) else row.get('segment_id'))
                       for row in source_segments if row.get('account_id') == share.account_id
                       and row.get('started_at') and (not selected or
                           (row.get('id') if isinstance(source, dict) else row.get('segment_id')) in selected)]
                if len(ids) != 1:
                    raise ValidationError('退款承担缺少唯一履约段')
                parts = [(ids[0], share.pending_deduction)]
            if sum(amount for _, amount in parts) != share.pending_deduction:
                raise ValidationError('退款逐段承担合计异常')
            for segment_id, amount in parts:
                if (segment_id not in incomes or segments[segment_id]['account_id'] != share.account_id
                        or amount > incomes[segment_id]):
                    raise ValidationError('退款逐段承担超过真实收益')
                incomes[segment_id] -= amount
                deductions.append([posting.pk, share.pk, segment_id, amount])
    from .points_funding_evidence import verified_order_allocations_locked
    allocations = [item for item in verified_order_allocations_locked(order)
                   if item.created_at <= transfer.created_at]
    addon_payments = list(OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__lt=transfer.created_at).select_related('intent', 'buyer_transaction'))
    if OrderAddonPayment.objects.filter(intent__order=order,
            buyer_transaction__created_at=transfer.created_at).exists():
        raise ValidationError('加购付款与转单时点无法区分')
    rows = []
    for allocation in allocations:
        main = list(OrderPointsFundingRelease.objects.filter(allocation=allocation,
                    created_at__lt=transfer.created_at).order_by('pk').values_list('pk', 'amount'))
        transferred = list(OrderPointsTransferFundingRelease.objects.filter(allocation=allocation,
                    created_at__lt=transfer.created_at).order_by('pk').values_list('pk', 'amount'))
        post = list(PointsPostRefundTransferRelease.objects.filter(allocation=allocation,
                    revision__created_at__lt=transfer.created_at).order_by('pk').values_list('pk', 'amount'))
        addon = list(OrderAddonFundingRelease.objects.filter(allocation=allocation,
            run__created_at__lt=transfer.created_at).order_by('pk').values_list('pk', 'amount'))
        used = sum(amount for _, amount in main + transferred + post + addon)
        if used > allocation.amount:
            raise ValidationError('已有释放超过本单到账分配')
        entry = [allocation.pk, allocation.receipt_id, allocation.amount, used,
                 [list(item) for item in main], [list(item) for item in transferred],
                 [list(item) for item in post]]
        if addon_payments or addon:
            entry.append([list(item) for item in addon])
        rows.append(entry)
    net = sum(item[2] - item[3] for item in rows)
    proofs = [first.points_refund, second.points_refund]
    shares = [*first.shares.all(), *second.shares.all()]
    from .points_negotiation_preview import funding_after_refund
    original = history.funding
    projected = funding_after_refund(principal_units=original.principal_units, gift_units=original.gift_units,
        compensation_units=original.compensation_units,
        returned_prepaid_units=sum(item.prepaid_units for item in proofs),
        returned_gift_units=sum(item.gift_units for item in proofs),
        returned_compensation_units=sum(item.compensation_units for item in proofs),
        provider_income_units=original.provider_income_units,
        pending_deduction_units=sum(item.pending_deduction for item in shares),
        provider_external_units=sum(item.wallet_deduction + item.deposit_deduction for item in shares),
        inviter_rate=facts['inviter_commission_rate'] if facts['inviter_id'] or facts['inviter_account_id'] else 0,
        allocated_units=net,
        rounding_reserved_units=first.rounding_to_store_units + second.rounding_to_store_units)
    addon_history, pending_handover = None, []
    if addon_payments:
        from .models import OrderAddonHandover
        from .addon_handover import current_responsibilities
        from .points_transfer_followup_scope import combine_addon
        pending = [(item.account_id, item.pending_deduction,
                    set(item.posting.proposal.affected_segment_ids or []),
                    list(item.segment_deductions.values_list('segment_id', 'amount')) or None)
                   for item in shares]
        projected, addon_history = combine_addon(order, projected, transfer.created_at,
            history.segment_shares, facts['segments'], pending)
        for payment in addon_payments:
            if OrderAddonHandover.objects.filter(payment=payment, created_at__lt=transfer.created_at,
                    status='PENDING').exists() or OrderAddonHandover.objects.filter(payment=payment,
                    created_at__lt=transfer.created_at, accepted_at__gte=transfer.created_at).exists():
                raise ValidationError('转单时加购接替尚待确认')
            _, responsibility = current_responsibilities(payment, before=transfer.created_at)
            active = [item for item in responsibility if item['is_current'] and item['basis'] > 0]
            if len(active) > 1:
                raise ValidationError('转出加购当前责任不唯一')
            if active and active[0]['segment_id'] == transfer.previous_segment_id:
                if not allow_pending:
                    raise ValidationError('转出加购须先完成三方接替')
                pending_handover.append([payment.pk, active[0]['segment_id'], active[0]['basis']])
    addon_income = sum(item['provider_units'] for item in addon_history or [])
    if sum(incomes.values()) + addon_income != projected.provider_income_units or net > previous_funding.required_units:
        raise ValidationError('二次退款后转单收益或原补资异常')
    from .points_funding_allocation import plan_release
    parts = plan_release(allocations=[(item[0], item[2], item[3]) for item in rows],
                         amount=projected.release_units)
    snapshot = {'kind': 'POINTS_FOLLOWUP_TRANSFER_V1', 'facts': facts,
        'previous_funding': asdict(previous_funding), 'funding': asdict(projected),
        'segment_income': [list(item) for item in sorted(incomes.items())],
        'deductions': deductions, 'allocations': rows,
        'releases': [[part.allocation_id, part.amount] for part in parts]}
    if addon_payments:
        snapshot['addon_history'] = addon_history
        snapshot['pending_handover'] = sorted(pending_handover)
    digest = _digest(order.pk, transfer.pk, first.pk, second.pk, followup.pk, snapshot)
    if _existing:
        if (_existing.order_id != order.pk or _existing.transfer_id != transfer.pk
                or _existing.first_posting_id != first.pk or _existing.second_posting_id != second.pk
                or _existing.followup_revision_id != followup.pk or _existing.snapshot != snapshot
                or _existing.evidence_hash != digest
                or list(_existing.releases.order_by('-allocation_id').values_list('allocation_id', 'amount'))
                    != [(part.allocation_id, part.amount) for part in parts]):
            raise ValidationError('二次退款后转单资金来源或版本异常')
        return _existing
    version = PointsFollowupTransferRevision.objects.create(order=order, transfer=transfer,
        first_posting=first, second_posting=second, followup_revision=followup,
        snapshot=snapshot, evidence_hash=digest)
    PointsFollowupTransferRelease.objects.bulk_create([
        PointsFollowupTransferRelease(revision=version, allocation_id=part.allocation_id, amount=part.amount)
        for part in parts])
    from users.receipt_usage_sources import record_followup_transfer_release_locked, verify_allocations_locked
    touched = set()
    for release in version.releases.select_related('allocation__receipt', 'allocation__redemption',
            'revision__order', 'revision__transfer', 'revision__first_posting',
            'revision__second_posting', 'revision__followup_revision').order_by('allocation__receipt_id', 'pk'):
        record_followup_transfer_release_locked(release)
        touched.add(release.allocation.receipt_id)
    for receipt_id in sorted(touched):
        verify_allocations_locked(receipt_id)
    return version


def verify_locked(version):
    """Re-read this version while its order and funding locks are held."""
    from .models import PointsFollowupTransferRevision
    version = (PointsFollowupTransferRevision.objects.select_for_update()
        .select_related('order', 'transfer__previous_segment__attribution').get(pk=version.pk))
    return record_locked(version.order, version.transfer, allow_pending=bool(version.snapshot.get('pending_handover')),
                         _existing=version)


def verify_settlement_locked(version):
    """Require unchanged actual funding before using the new segment plan."""
    from .models import (OrderPointsFundingAllocation, OrderPointsFundingRelease, OrderAddonFundingRelease,
        OrderPointsTransferFundingRelease, PointsPostRefundTransferRelease)
    from django.db.models import Q
    version = verify_locked(version)
    allocations = list(OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order=version.order) | Q(service_order=version.order)).order_by('pk'))
    rows = version.snapshot['allocations']
    by_id = {item.pk: item for item in allocations}
    if any(row[0] not in by_id for row in rows):
        raise ValidationError('转单时的到账分配缺失')
    original_ids = {row[0] for row in rows}
    extras = [item for item in allocations if item.pk not in original_ids]
    if any(item.created_at <= version.created_at for item in extras):
        raise ValidationError('转单后新增到账的时间待核对')
    for item in extras:
        if (OrderPointsFundingRelease.objects.filter(allocation=item).exists()
                or OrderPointsTransferFundingRelease.objects.filter(allocation=item).exists()
                or PointsPostRefundTransferRelease.objects.filter(allocation=item).exists()):
            raise ValidationError('转单后新增到账的既有释放尚未接入')
    for row in rows:
        item = by_id[row[0]]
        main = list(OrderPointsFundingRelease.objects.filter(allocation=item).order_by('pk').values_list('pk', 'amount'))
        transferred = list(OrderPointsTransferFundingRelease.objects.filter(allocation=item).order_by('pk').values_list('pk', 'amount'))
        post = list(PointsPostRefundTransferRelease.objects.filter(allocation=item).order_by('pk').values_list('pk', 'amount'))
        addon = list(OrderAddonFundingRelease.objects.filter(allocation=item,
            run__created_at__lt=version.transfer.created_at).order_by('pk').values_list('pk', 'amount'))
        if (item.receipt_id != row[1] or item.amount != row[2]
                or [list(value) for value in main] != row[4]
                or [list(value) for value in transferred] != row[5]
                or [list(value) for value in post] != row[6]
                or [list(value) for value in addon] != (row[7] if len(row) == 8 else [])):
            raise ValidationError('转单后实际资金释放变化尚未接入结算')
    return version


def available_after_transfer_locked(version):
    """Include later actual allocations without rewriting the transfer snapshot."""
    from django.db.models import Q
    from .models import OrderPointsFundingAllocation
    version = verify_settlement_locked(version)
    rows = version.snapshot['allocations']
    original_ids = {row[0] for row in rows}
    later = OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order=version.order) | Q(service_order=version.order)).exclude(pk__in=original_ids)
    available = (sum(row[2] - row[3] for row in rows)
                 - sum(row[1] for row in version.snapshot['releases'])
                 + sum(later.values_list('amount', flat=True)))
    if available < 0 or available > version.snapshot['funding']['required_units']:
        raise ValidationError('转单后到账净额与实际资金缺口不一致')
    return available


def unresolved_pending_locked(version):
    """Replay actual refunds before deciding whether a frozen liability remains."""
    from .models import OrderAddonPayment
    from .addon_handover import current_responsibilities, verify_handover
    from .points_transfer_followup_addon_version import latest_run_locked
    if version.snapshot.get('pending_handover'):
        # Verifies every real post-refund payment, refund and accepted handover
        # before their current responsibility can change the frozen liability.
        latest_run_locked(version.order, version.followup_revision)
    unresolved = []
    for payment_id, source_segment_id, original_basis in version.snapshot.get('pending_handover', []):
        payment = OrderAddonPayment.objects.filter(pk=payment_id, intent__order=version.order).first()
        if not payment:
            raise ValidationError('转单待接替加购付款缺失')
        accepted = list(payment.handovers.filter(status='ACCEPTED', created_at__gt=version.created_at,
            source_segment_id=source_segment_id, target_segment_id=version.transfer.next_segment_id).order_by('version'))
        if len(accepted) > 1:
            raise ValidationError('同一冻结责任存在多次接替')
        if accepted:
            before = current_responsibilities(payment, before=accepted[0].created_at)[1]
            source = [item for item in before if item['is_current'] and item['segment_id'] == source_segment_id]
            if (len(source) != 1 or not 0 < source[0]['basis'] <= original_basis
                    or accepted[0].source_basis_before != source[0]['basis']):
                raise ValidationError('接替的退款后剩余责任不一致')
            verify_handover(payment, accepted[0])
            continue
        _, current = current_responsibilities(payment)
        active = [item for item in current if item['is_current'] and item['basis'] > 0]
        if not active:
            if (not payment.refund_records.filter(created_at__gt=version.created_at).exists()
                    or sum(payment.refund_records.values_list('amount', flat=True)) != payment.intent.amount):
                raise ValidationError('消失的待接替责任缺少真实全额退款')
            continue
        if (len(active) != 1 or active[0]['segment_id'] != source_segment_id
                or not 0 < active[0]['basis'] <= original_basis):
            raise ValidationError('退款后的待接替责任异常')
        unresolved.append([payment_id, source_segment_id, active[0]['basis']])
    return unresolved


def verify_pending_or_completed_locked(version, *, allow_unproposed=False):
    """Recheck every unresolved frozen liability against its live proposal."""
    from .models import OrderAddonHandover
    from .points_transfer_followup_handover_scope import _verify_rejected
    unresolved = unresolved_pending_locked(version)
    pending = list(OrderAddonHandover.objects.select_for_update().filter(
        payment__intent__order=version.order, status='PENDING'))
    if (len({item.payment_id for item in pending}) != len(pending)
            or any([item.payment_id, item.source_segment_id, item.source_basis_before] not in unresolved
                   or item.target_segment_id != version.transfer.next_segment_id for item in pending)):
        raise ValidationError('待确认接替不属于本次转单责任')
    for payment_id, segment_id, basis in unresolved:
        proposals = list(OrderAddonHandover.objects.select_for_update().filter(
            payment_id=payment_id, payment__intent__order=version.order, status='PENDING',
            source_segment_id=segment_id, target_segment_id=version.transfer.next_segment_id,
            source_basis_before=basis))
        if not proposals and allow_unproposed:
            continue
        if len(proposals) != 1:
            raise ValidationError('转出加购缺少对应待确认方案')
        _verify_rejected(proposals[0].payment, proposals[0], pending=True)
    return unresolved
