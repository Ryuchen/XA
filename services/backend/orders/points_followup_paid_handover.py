"""Paid-ledger evidence for a real confirmed follow-up handover."""
import hashlib
import json

from rest_framework.exceptions import ValidationError
from stores.db import transaction


def _digest(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def capture_before_locked(order, handover):
    """Capture credited people under the exact handover's preflight and locks."""
    from .models import OrderSegmentPersonSettlement, OrderSegmentSettlementManifest
    from .points_transfer_followup_addon_version import handover_preflight_active
    from .points_transfer_refund_evidence import _prior_settlement, _verify_prior_settlement
    from .segment_addon_settlement_revision import verify_revisions
    if not transaction.get_connection().in_atomic_block or not handover_preflight_active(order, handover):
        raise ValidationError('已付接替凭证须绑定本次接替事务')
    if not OrderSegmentPersonSettlement.objects.filter(manifest__order=order).exists():
        return None
    paid = _prior_settlement(order)
    if not paid:
        raise ValidationError('已付接替缺少真实人员及流水清单')
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().get(pk=paid['manifest_id'], order=order)
    if manifest.finalized_at or order.settled_at:
        raise ValidationError('整单已结算，不能变增加购责任')
    verify_revisions(manifest)
    _verify_prior_settlement(order, paid)
    affected = {handover.source_segment.provider_account_id, handover.target_segment.provider_account_id}
    if affected.intersection(row['account_id'] for row in paid['people']):
        raise ValidationError('已付陪玩不能变更加购责任')
    return paid


def record_locked(order, handover, paid):
    """Persist the paid snapshot in the same transaction as confirmation and release."""
    if paid is None:
        return None
    from .models import OrderAddonFundingReleaseRun, OrderSegmentSettlementManifest, PointsPaidHandoverEvidence
    from .points_transfer_followup_addon_version import handover_preflight_active
    from .points_transfer_refund_evidence import _verify_prior_settlement
    from .addon_funding_release import verify_run
    if (not transaction.get_connection().in_atomic_block or not handover_preflight_active(order, handover)
            or handover.status != 'ACCEPTED' or not handover.accepted_at):
        raise ValidationError('已付接替确认与原事务不一致')
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().get(pk=paid['manifest_id'], order=order)
    run = OrderAddonFundingReleaseRun.objects.select_for_update().filter(handover=handover).first()
    data = run.points_followup_snapshot if run else None
    if (not run or run.created_at < handover.accepted_at or not isinstance(data, dict)
            or data.get('kind') != 'TRANSFER_FOLLOWUP_V1' or data.get('addon_handover_id') != handover.pk):
        raise ValidationError('已付接替缺少真实资金事件')
    _verify_prior_settlement(order, paid)
    verify_run(run, _skip_usage=True)
    snapshot = {'version': 1, 'order_id': order.pk, 'payment_id': handover.payment_id,
                'handover_id': handover.pk, 'accepted_at': handover.accepted_at.isoformat(),
                'funding_run_id': run.pk, 'followup_hash': run.points_followup_hash, 'paid': paid}
    evidence, created = PointsPaidHandoverEvidence.objects.get_or_create(handover=handover,
        defaults={'manifest': manifest, 'funding_run': run, 'snapshot': snapshot, 'evidence_hash': _digest(snapshot)})
    if not created and (evidence.manifest_id != manifest.pk or evidence.funding_run_id != run.pk
                        or evidence.snapshot != snapshot or evidence.evidence_hash != _digest(snapshot)):
        raise ValidationError('已付接替凭证与原确认不一致')
    return evidence


def verify_current_read(run, *, receipt_id=None):
    """Verify the event and original paid ledgers without re-entering its release."""
    from .models import PointsPaidHandoverEvidence
    from .points_transfer_followup_handover_scope import verify_event
    from .points_voluntary_paid_evidence import verify_historical_paid
    evidence = PointsPaidHandoverEvidence.objects.filter(funding_run=run).select_related(
        'handover__payment__intent__order', 'manifest').first()
    if not evidence:
        return None
    handover, snapshot = evidence.handover, evidence.snapshot
    if (not isinstance(snapshot, dict) or evidence.evidence_hash != _digest(snapshot)
            or run.handover_id != handover.pk or handover.status != 'ACCEPTED' or not handover.accepted_at
            or evidence.manifest.order_id != handover.payment.intent.order_id
            or snapshot.get('version') != 1 or snapshot.get('order_id') != evidence.manifest.order_id
            or snapshot.get('payment_id') != handover.payment_id or snapshot.get('handover_id') != handover.pk
            or snapshot.get('accepted_at') != handover.accepted_at.isoformat()
            or snapshot.get('funding_run_id') != run.pk or snapshot.get('followup_hash') != run.points_followup_hash
            or run.created_at < handover.accepted_at):
        raise ValidationError('已付接替资金事件凭证异常')
    verify_event(handover.payment, handover, receipt_id=receipt_id)
    paid = verify_historical_paid(handover.payment.intent.order, snapshot.get('paid'),
                                  handover.accepted_at, evidence.manifest_id)
    affected = {handover.source_segment.provider_account_id, handover.target_segment.provider_account_id}
    if affected.intersection(row['account_id'] for row in paid['people']):
        raise ValidationError('已付陪玩的加购责任被变更')
    return paid


def _check_revision(evidence, before, after, *, receipt_id=None):
    from .points_transfer_paid_addons import unchanged
    from .points_voluntary_paid_evidence import _verify_mixed_people
    paid = verify_current_read(evidence.funding_run, receipt_id=receipt_id)
    if not paid or paid['plan'] != before:
        raise ValidationError('已付接替未绑定原结算清单')
    stable = ('deal_amount', 'segments', 'fixed_fee_shortfall', 'buyer_account_id', 'inviter_account_id',
              'inviter_id', 'inviter_rate', 'deposit_percent', 'aftersale_hours', 'freeze_hours', 'completed_at')
    old, new = before.get('points_funding', {}), after.get('points_funding', {})
    old_addons, new_addons = old.get('addons'), new.get('addons')
    if (any(before.get(key) != after.get(key) for key in stable)
            or not isinstance(old_addons, list) or not isinstance(new_addons, list)
            or old.get('addon_refund_ids', []) != new.get('addon_refund_ids', [])):
        raise ValidationError('接替清单改变了主单或退款事实')
    payment_id = evidence.handover.payment_id
    if ([row for row in old_addons if row.get('payment_id') != payment_id]
            != [row for row in new_addons if row.get('payment_id') != payment_id]
            or not any(row.get('payment_id') == payment_id for row in old_addons)
            or not any(row.get('payment_id') == payment_id for row in new_addons)):
        raise ValidationError('接替清单改变了其他付款')
    unchanged(paid, new_addons)
    _verify_mixed_people(before, after, paid)
    return paid


def transition_locked(order, manifest, plan, terms, evidence):
    from .models import PointsPaidHandoverSettlementRevision
    from .segment_settlement import _hash, _validate_records
    from .settlement import SettlementError
    if (not transaction.get_connection().in_atomic_block or manifest.order_id != order.pk
            or manifest.finalized_at or order.settled_at or evidence.manifest_id != manifest.pk
            or manifest.plan_hash != _hash(manifest.plan)
            or PointsPaidHandoverSettlementRevision.objects.filter(evidence=evidence).exists()):
        raise SettlementError('已付接替清单上下文异常')
    before = manifest.plan
    try:
        paid = _check_revision(evidence, before, terms)
        _validate_records(order, manifest, plan, snapshot_ids=[item['id'] for item in paid['people']],
                          paid_addon_snapshot=paid, paid_addon_cutoff=evidence.handover.accepted_at)
    except ValidationError as exc:
        raise SettlementError('已付接替清单与流水不一致') from exc
    PointsPaidHandoverSettlementRevision.objects.create(manifest=manifest, evidence=evidence,
        before_plan=before, before_plan_hash=manifest.plan_hash,
        after_plan=terms, after_plan_hash=_hash(terms))
    manifest.plan, manifest.plan_hash = terms, _hash(terms)
    manifest.save(update_fields=['plan', 'plan_hash'])
    return _validate_records(order, manifest, plan)


def _derive_in_service_plan(order, run, before):
    from copy import deepcopy
    from .addon_handover import current_responsibilities
    from .models import OrderAddonPayment, OrderAddonRefund, OrderPointsFundingAllocation
    snapshot = run.points_followup_snapshot
    if (not isinstance(snapshot, dict) or not isinstance(snapshot.get('funding'), dict)
            or not isinstance(snapshot.get('addon_history'), list) or not isinstance(run.allocation_snapshot, list)):
        raise ValidationError('履约中资金事件异常')
    after = deepcopy(before)
    funding = dict(snapshot['funding'])
    calculation = {key: value for key, value in funding.items()
                   if key not in ('allocated_units', 'release_units', 'remaining_units')}
    calculation['additional_store_funding_units'] = funding['required_units']
    addons, refunds = [], []
    keys = ('paid_units', 'principal_units', 'gift_units', 'compensation_units',
            'provider_units', 'inviter_units', 'recovery_units')
    totals = {key: 0 for key in keys}
    for item in snapshot['addon_history']:
        if not isinstance(item, dict) or any(type(item.get(key)) is not int for key in keys):
            raise ValidationError('接替后的加购资金事实异常')
        payment = OrderAddonPayment.objects.select_related('intent').filter(pk=item.get('payment_id'), intent__order=order).first()
        if not payment:
            raise ValidationError('接替清单缺少真实付款')
        current, shares = current_responsibilities(payment, before=run.created_at)
        if ((current.pk if current else None) != item.get('handover_id')
                or sum(row['basis'] for row in shares) != item['paid_units']
                or sum(row['income'] for row in shares) != item['provider_units']):
            raise ValidationError('接替清单与实际剩余责任不一致')
        refunds.extend(item.get('refund_ids', []))
        for row in shares:
            addons.append({'payment_id': payment.pk, 'segment_id': row['segment_id'],
                           'provider_account_id': row['provider_id'], 'provider_income_units': row['income']})
        for key in keys:
            totals[key] += item[key]
    merged = {}
    for row in addons:
        key = (row['payment_id'], row['segment_id'], row['provider_account_id'])
        if key not in merged:
            merged[key] = dict(row)
        else:
            merged[key]['provider_income_units'] += row['provider_income_units']
    addons = list(merged.values())
    if len(refunds) != len(set(refunds)) or OrderAddonRefund.objects.filter(order=order, pk__in=refunds).count() != len(refunds):
        raise ValidationError('接替清单的退款来源异常')
    releases = {row.allocation_id: row.amount for row in run.releases.all()}
    allocations = []
    for row in run.allocation_snapshot:
        if not isinstance(row, list) or len(row) != 6:
            raise ValidationError('接替清单的到账版本异常')
        allocation = OrderPointsFundingAllocation.objects.filter(pk=row[0], redemption__order=order).first()
        if not allocation or allocation.amount != row[1]:
            raise ValidationError('接替清单的到账不属于本单')
        used = row[2] + sum(part[1] for group in row[3:] for part in group) + releases.get(row[0], 0)
        if used > row[1]:
            raise ValidationError('接替清单释放金额超额')
        allocations.append({'id': row[0], 'receipt_id': allocation.receipt_id, 'amount': row[1] - used})
    terms = after.get('points_funding')
    if not isinstance(terms, dict):
        raise ValidationError('接替前清单缺少积分资金事实')
    from .models import OrderParticipationSegment
    try:
        old_deductions = dict(terms.get('main_segment_deductions', []))
        new_deductions = dict(snapshot.get('main_segment_deductions', []))
        if any(type(key) is not int or type(value) is not int or value < old_deductions.get(key, 0)
               for key, value in new_deductions.items()) or any(key not in new_deductions for key in old_deductions):
            raise ValidationError('主单分段扣减历史异常')
        people = {row['account_id']: row for row in after['people']}
        for segment_id, amount in new_deductions.items():
            delta = amount - old_deductions.get(segment_id, 0)
            if delta:
                segment = OrderParticipationSegment.objects.get(pk=segment_id, attribution__order=order)
                person = people[segment.provider_account_id]
                if segment_id not in person['segment_ids'] or person['income'] < delta:
                    raise ValidationError('退款主单段收益不足')
                person['income'] -= delta
    except (TypeError, ValueError, KeyError, OrderParticipationSegment.DoesNotExist) as exc:
        raise ValidationError('主单分段扣减凭证异常') from exc
    terms.update(calculation=calculation, allocations=allocations, addons=addons, addon_totals=totals,
                 addon_refund_ids=refunds, main_segment_deductions=snapshot.get('main_segment_deductions', []))
    after['store_income'], after['inviter_income'] = funding['store_income_units'], funding['inviter_income_units']
    incoming = funding['net_principal_units'] + funding['planned_provider_external_units'] + sum(row['amount'] for row in allocations)
    outgoing = (sum(row['income'] for row in after['people']) + sum(row['provider_income_units'] for row in addons)
                + after['store_income'] + after['inviter_income'])
    if incoming + funding['remaining_units'] != outgoing:
        raise ValidationError('接替清单资金与待补金额不守恒')
    return after


def transition_in_service_locked(order, evidence):
    """Advance the paid manifest from actual handover facts without paying anyone."""
    from copy import deepcopy
    from .addon_handover import current_responsibilities
    from .models import (OrderAddonPayment, OrderAddonRefund, OrderPointsFundingAllocation,
                         OrderSegmentSettlementManifest, PointsPaidHandoverSettlementRevision)
    from .segment_settlement import _hash
    if not transaction.get_connection().in_atomic_block or order.status != order.Status.IN_SERVICE:
        raise ValidationError('履约中接替清单须在原确认事务内更新')
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().get(pk=evidence.manifest_id, order=order)
    run, handover = evidence.funding_run, evidence.handover
    snapshot = run.points_followup_snapshot
    if (manifest.finalized_at or order.settled_at or manifest.plan_hash != _hash(manifest.plan)
            or not isinstance(snapshot, dict) or not isinstance(snapshot.get('funding'), dict)
            or not isinstance(snapshot.get('addon_history'), list) or not isinstance(run.allocation_snapshot, list)
            or PointsPaidHandoverSettlementRevision.objects.filter(evidence=evidence).exists()):
        raise ValidationError('履约中接替资金或清单版本异常')
    before, after = manifest.plan, deepcopy(manifest.plan)
    after = _derive_in_service_plan(order, run, before)
    _check_revision(evidence, before, after)
    PointsPaidHandoverSettlementRevision.objects.create(manifest=manifest, evidence=evidence,
        before_plan=before, before_plan_hash=manifest.plan_hash, after_plan=after, after_plan_hash=_hash(after))
    manifest.plan, manifest.plan_hash = after, _hash(after)
    manifest.save(update_fields=['plan', 'plan_hash'])


def transition_refund_in_service_locked(order, evidence):
    """Advance the paid manifest for the actual refund without posting earnings."""
    from .models import OrderSegmentSettlementManifest, OrderSegmentAddonSettlementRevision
    from .points_voluntary_paid_evidence import verify_current_read, _verify_mixed_people
    from .points_transfer_paid_addons import unchanged
    from .points_transfer_followup_addon_version import refund_preflight_active
    from .segment_settlement import _hash
    run, refund = evidence.funding_run, evidence.refund
    if (not transaction.get_connection().in_atomic_block or order.status != order.Status.IN_SERVICE
            or not refund_preflight_active(order, refund.payment)):
        raise ValidationError('履约中退款清单须绑定真实退款事务')
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().get(pk=evidence.manifest_id, order=order)
    if (manifest.finalized_at or order.settled_at or manifest.plan_hash != _hash(manifest.plan)
            or run.refund_id != refund.pk):
        raise ValidationError('履约中退款资金或清单版本异常')
    before = manifest.plan
    paid = verify_current_read(run)
    if not paid or paid['plan'] != before:
        raise ValidationError('退款未绑定当前已付清单')
    after = _derive_in_service_plan(order, run, before)
    old_ids = before.get('points_funding', {}).get('addon_refund_ids', [])
    new_ids = after.get('points_funding', {}).get('addon_refund_ids', [])
    if (not isinstance(old_ids, list) or not isinstance(new_ids, list)
            or len(set(new_ids)) != len(new_ids) or not set(old_ids).issubset(new_ids)
            or set(new_ids) - set(old_ids) != {refund.pk}):
        raise ValidationError('履约中退款清单与实际事件不一致')
    unchanged(paid, after['points_funding']['addons'])
    _verify_mixed_people(before, after, paid)
    OrderSegmentAddonSettlementRevision.objects.create(manifest=manifest,
        version=manifest.addon_revisions.count() + 1, refund_ids=[refund.pk],
        before_plan=before, before_plan_hash=manifest.plan_hash, after_plan=after, after_plan_hash=_hash(after))
    manifest.plan, manifest.plan_hash = after, _hash(after)
    manifest.save(update_fields=['plan', 'plan_hash'])


def transition_payment_in_service_locked(order, evidence):
    """Advance the paid manifest for a committed payment, without paying anyone."""
    from .models import OrderSegmentSettlementManifest, PointsVoluntaryPaidPaymentSettlementRevision
    from .points_business_access import permits
    from .points_voluntary_paid_evidence import verify_payment_current_read, _verify_mixed_people
    from .points_transfer_paid_addons import unchanged
    from .segment_settlement import _hash
    run, payment = evidence.funding_run, evidence.payment
    if (not transaction.get_connection().in_atomic_block or order.status != order.Status.IN_SERVICE
            or not permits(order, phase='payment', kind='transfer') or payment.intent.order_id != order.pk):
        raise ValidationError('履约中付款清单须绑定真实付款事务')
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().get(pk=evidence.manifest_id, order=order)
    if (manifest.finalized_at or order.settled_at or manifest.plan_hash != _hash(manifest.plan)
            or run.payment_id != payment.pk or PointsVoluntaryPaidPaymentSettlementRevision.objects.filter(
                evidence=evidence).exists()):
        raise ValidationError('履约中付款资金或清单版本异常')
    before = manifest.plan
    paid = verify_payment_current_read(run)
    if not paid or paid['plan'] != before:
        raise ValidationError('付款未绑定当前已付清单')
    after = _derive_in_service_plan(order, run, before)
    old, new = before.get('points_funding', {}), after.get('points_funding', {})
    old_rows, new_rows = old.get('addons'), new.get('addons')
    if (not isinstance(old_rows, list) or not isinstance(new_rows, list)
            or old.get('addon_refund_ids', []) != new.get('addon_refund_ids', [])
            or any(row.get('payment_id') == payment.pk for row in old_rows)
            or not any(row.get('payment_id') == payment.pk for row in new_rows)
            or [row for row in old_rows] != [row for row in new_rows if row.get('payment_id') != payment.pk]):
        raise ValidationError('履约中付款清单与真实付款不一致')
    unchanged(paid, new_rows)
    _verify_mixed_people(before, after, paid)
    PointsVoluntaryPaidPaymentSettlementRevision.objects.create(manifest=manifest, evidence=evidence,
        payment_ids=[payment.pk], refund_ids=[], before_plan=before, before_plan_hash=manifest.plan_hash,
        after_plan=after, after_plan_hash=_hash(after))
    manifest.plan, manifest.plan_hash = after, _hash(after)
    manifest.save(update_fields=['plan', 'plan_hash'])


def verify_revisions(manifest):
    from .segment_settlement import _hash
    from .settlement import SettlementError
    for revision in manifest.paid_handover_revisions.select_for_update().select_related(
            'evidence__funding_run__handover__payment__intent__order').order_by('created_at', 'pk'):
        if (revision.evidence.manifest_id != manifest.pk or revision.before_plan_hash != _hash(revision.before_plan)
                or revision.after_plan_hash != _hash(revision.after_plan)
                or revision.created_at < revision.evidence.funding_run.created_at):
            raise SettlementError('已付接替清单版本异常')
        try:
            _check_revision(revision.evidence, revision.before_plan, revision.after_plan)
        except ValidationError as exc:
            raise SettlementError('已付接替清单凭证异常') from exc
    for revision in manifest.paid_completion_revisions.select_for_update().select_related('completion_log').order_by('pk'):
        if (revision.before_plan_hash != _hash(revision.before_plan)
                or revision.after_plan_hash != _hash(revision.after_plan)
                or revision.paid_hash != _digest(revision.paid_snapshot)):
            raise SettlementError('订单完成清单版本摘要异常')
        try:
            _check_completion(manifest.order, revision.completion_log, revision.before_plan,
                              revision.after_plan, revision.paid_snapshot, revision.created_at)
        except ValidationError as exc:
            raise SettlementError('订单完成清单来源异常') from exc


def _check_completion(order, log, before, after, paid, at):
    from django.utils import timezone
    from .models import OrderStatusLog
    from .points_voluntary_paid_evidence import verify_historical_paid, _verify_mixed_people
    from .points_transfer_paid_addons import unchanged
    if (not isinstance(before, dict) or not isinstance(after, dict) or not isinstance(paid, dict)
            or order.status != order.Status.COMPLETED or not order.completed_at or not at
            or log.order_id != order.pk or log.action != OrderStatusLog.Action.COMPLETE
            or log.from_status != order.Status.IN_SERVICE or log.to_status != order.Status.COMPLETED
            or not order.completed_at <= log.created_at <= at <= timezone.now()
            or before.get('completed_at') is not None
            or after.get('completed_at') != order.completed_at.isoformat()):
        raise ValidationError('订单实际完成事件异常')
    from copy import deepcopy
    original, completed = deepcopy(before), deepcopy(after)
    original.pop('completed_at', None)
    completed.pop('completed_at', None)
    old_funding, new_funding = original.get('points_funding'), completed.get('points_funding')
    if not isinstance(old_funding, dict) or not isinstance(new_funding, dict):
        raise ValidationError('订单完成前后缺少资金清单')
    old_allocations, new_allocations = old_funding.pop('allocations', None), new_funding.pop('allocations', None)
    if not isinstance(old_allocations, list) or not isinstance(new_allocations, list):
        raise ValidationError('订单完成前后到账清单异常')
    if original != completed or paid.get('plan') != before:
        raise ValidationError('订单完成事件改变了其他清单事实')
    from django.db.models import Q
    from .models import (OrderPointsFundingAllocation, OrderPointsFundingRelease,
                         OrderPointsTransferFundingRelease, PointsPostRefundTransferRelease,
                         OrderAddonFundingRelease)
    allocations = list(OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order=order) | Q(service_order=order), created_at__lt=at).order_by('pk'))
    if OrderPointsFundingAllocation.objects.filter(
            Q(redemption__order=order) | Q(service_order=order), created_at=at).exists():
        raise ValidationError('订单完成与到账时间无法区分')
    expected = []
    for row in allocations:
        used = sum(OrderPointsFundingRelease.objects.filter(allocation=row, created_at__lt=at).values_list('amount', flat=True))
        used += sum(OrderPointsTransferFundingRelease.objects.filter(allocation=row,
            revision__created_at__lt=at).values_list('amount', flat=True))
        used += sum(PointsPostRefundTransferRelease.objects.filter(allocation=row,
            revision__created_at__lt=at).values_list('amount', flat=True))
        used += sum(OrderAddonFundingRelease.objects.filter(allocation=row,
            run__created_at__lt=at).values_list('amount', flat=True))
        if used > row.amount:
            raise ValidationError('订单完成时到账释放超额')
        expected.append({'id': row.pk, 'receipt_id': row.receipt_id, 'amount': row.amount - used})
    if new_allocations != expected:
        raise ValidationError('订单完成时到账与真实分配或释放不一致')
    verified = verify_historical_paid(order, paid, at, paid.get('manifest_id'))
    unchanged(verified, after.get('points_funding', {}).get('addons', []))
    _verify_mixed_people(before, after, verified)
    return verified


def transition_completion_locked(order, manifest, plan, terms):
    """Record a real completion in the manifest; settlement still decides when to pay."""
    from django.utils import timezone
    from .models import OrderStatusLog, PointsPaidCompletionSettlementRevision
    from .points_transfer_refund_evidence import _prior_settlement, _verify_prior_settlement
    from .segment_settlement import _hash, _validate_records
    from .settlement import SettlementError
    logs = list(OrderStatusLog.objects.filter(order=order, action=OrderStatusLog.Action.COMPLETE,
        from_status=order.Status.IN_SERVICE, to_status=order.Status.COMPLETED).order_by('pk'))
    paid = _prior_settlement(order)
    if (not transaction.get_connection().in_atomic_block or manifest.finalized_at or order.settled_at
            or manifest.plan_hash != _hash(manifest.plan) or len(logs) != 1 or not paid
            or paid.get('manifest_id') != manifest.pk):
        raise SettlementError('订单完成前已付清单或状态记录缺失')
    _verify_prior_settlement(order, paid)
    before = manifest.plan
    try:
        verified = _check_completion(order, logs[0], before, terms, paid, timezone.now())
        _validate_records(order, manifest, plan, snapshot_ids=[item['id'] for item in verified['people']],
                          paid_addon_snapshot=verified, paid_addon_cutoff=timezone.now())
    except ValidationError as exc:
        raise SettlementError('订单完成清单或已付流水待核对') from exc
    PointsPaidCompletionSettlementRevision.objects.create(manifest=manifest, completion_log=logs[0],
        paid_snapshot=paid, paid_hash=_digest(paid), before_plan=before, before_plan_hash=manifest.plan_hash,
        after_plan=terms, after_plan_hash=_hash(terms))
    manifest.plan, manifest.plan_hash = terms, _hash(terms)
    manifest.save(update_fields=['plan', 'plan_hash'])
    return _validate_records(order, manifest, plan)
