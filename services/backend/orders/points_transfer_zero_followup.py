"""Evidence for terminating a transferred points service without a refund.

No refund or funding release is created here: the existing verified funding
version remains the source of the eventual settlement.
"""
from dataclasses import asdict

from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .models import Order, OrderOriginalShareTransfer, OrderParticipationSegment


def _addon_facts(order, prior, *, before=None):
    """Freeze verified actual add-on liabilities and their money events."""
    from .models import OrderAddonPayment
    from .addon_handover import current_responsibilities
    from .points_transfer_addon_events import later_runs
    payments = list(OrderAddonPayment.objects.filter(intent__order=order).select_related('intent').order_by('pk'))
    if len(payments) != order.addon_intents.count():
        raise ValidationError('零退款终止前存在未完成的加购付款')
    result = []
    for payment in payments:
        pending = payment.handovers.filter(status='PENDING')
        if before is not None:
            pending = pending.filter(created_at__lt=before)
        if pending.exists():
            raise ValidationError('零退款终止前存在待确认的加购接替')
        handover, parts = current_responsibilities(payment, lock=True, before=before)
        refunds = payment.refund_records.order_by('created_at', 'pk')
        if before is not None:
            refunds = refunds.filter(created_at__lt=before)
        result.append({'payment_id': payment.pk, 'handover_id': handover.pk if handover else None,
            'refund_ids': list(refunds.values_list('pk', flat=True)),
            'parts': [{'segment_id': item['segment_id'], 'provider_id': item['provider_id'],
                       'basis': item['basis'], 'income': item['income']} for item in parts]})
    return {'payments': result, 'run_ids': [row.pk for row in later_runs(order, prior, before=before)]}


def _paid_facts_locked(order, plan, *, affected_account_id):
    """Prove existing credits before considering a zero-termination revision.

    This does not revise a manifest or authorize another payout. In particular,
    the caller must not use these facts as a substitute for a version linking
    the old manifest to the termination's completed-order plan.
    """
    from .models import OrderSegmentSettlementManifest, OrderSegmentPersonSettlement
    from .points_transfer_refund_evidence import _prior_settlement
    from .segment_settlement import _hash, _validate_records
    from .settlement import SettlementError

    manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
    if not manifest or manifest.finalized_at or manifest.plan_hash != _hash(manifest.plan):
        raise ValidationError('已结算人员的结算清单缺失或已完成')
    snapshot = _prior_settlement(order)
    rows = list(OrderSegmentPersonSettlement.objects.select_for_update().filter(manifest=manifest).order_by('pk'))
    if (not snapshot or not rows or any(row.account_id == affected_account_id for row in rows)
            or {row.account_id for row in rows} != {item['account_id'] for item in snapshot['people']}):
        raise ValidationError('零退款受影响人员或已结算人员范围待核对')
    try:
        records = _validate_records(order, manifest, plan, snapshot_ids=[row.pk for row in rows],
            paid_addon_snapshot=snapshot if manifest.plan.get('points_funding', {}).get('addons') else None,
            paid_addon_cutoff=timezone.now())
    except SettlementError as exc:
        raise ValidationError('已结算人员的报单、收益或加购流水待核对') from exc
    expected_people = {row.account_id: row for row in plan.persons}
    saved_people = {row['account_id']: row for row in manifest.plan.get('people', [])}
    if (set(records) != {row.account_id for row in rows} or any(
            row.account_id not in expected_people or row.account_id not in saved_people
            or saved_people[row.account_id].get('income') != row.amount
            or saved_people[row.account_id].get('segment_ids') != row.segment_ids for row in rows)):
        raise ValidationError('已结算人员的实际份额与原结算计划不一致')
    return snapshot


def applies(order):
    from .points_service_payment import needs_integrated_processing
    return bool(needs_integrated_processing(order) and order.refund_records.count() == 1
                and OrderOriginalShareTransfer.objects.filter(
                    previous_segment__attribution__order=order).exists())


def verify_event_current_read(record, *, before):
    """Verify the zero event without taking locks in a receipt-local read."""
    from copy import copy
    from .negotiation_proposals import _digest, _snapshot_terms
    from .points_zero_followup import digest
    from .models import OrderStatusLog
    proposal, order, snapshot = record.proposal, record.order, record.funding_snapshot
    case = proposal.case
    episode = copy(case)
    episode.created_at, episode.client_request_id = proposal.case_opened_at, proposal.case_request_id
    decisions = list(proposal.decisions.values_list('account_id', 'role', 'accepted', 'decided_at'))
    expected = {(order.customer_account_id, 'BOSS'), (record.assignment.provider_account_id, 'PROVIDER')}
    if (not record.terminated_at or record.terminated_at >= before or record.created_at > record.terminated_at
            or not isinstance(snapshot, dict) or snapshot.get('kind') != 'TRANSFER_ZERO_V1'
            or case.order_id != order.pk or proposal.refund_units != 0 or proposal.status != 'ACCEPTED'
            or proposal.accepted_at != record.accepted_at or proposal.affected_accounts != [record.assignment.provider_account_id]
            or (proposal.affected_segment_ids is not None and proposal.affected_segment_ids != [record.segment_id])
            or proposal.terms_hash != _digest(episode, proposal.version, proposal.affected_accounts, 0,
                _snapshot_terms(proposal.provider_terms, proposal.affected_accounts), proposal.explanation,
                proposal.affected_segment_ids)
            or {(account, role) for account, role, _, _ in decisions} != expected or len(decisions) != len(expected)
            or any(accepted is not True or not decided_at or decided_at > proposal.accepted_at
                   for _, _, accepted, decided_at in decisions)
            or record.assignment.order_id != order.pk or record.segment.attribution.order_id != order.pk
            or record.segment.provider_account_id != record.assignment.provider_account_id
            or record.assignment.released_at != record.accepted_at or record.segment.released_at != record.accepted_at
            or order.completed_at != record.accepted_at or order.status != Order.Status.COMPLETED
            or record.evidence_hash != digest(proposal, record.prior_posting, record.assignment,
                record.segment, record.operator, record.request_id, snapshot)):
        raise ValidationError('零退款终止的历史事实不一致')
    logs = OrderStatusLog.objects.filter(order=order, action='COMPLETE', from_status='IN_SERVICE',
        to_status='COMPLETED', reason='双方确认积分零退款协商终止', operator_account_id=record.operator_id,
        created_at__gte=record.accepted_at, created_at__lte=record.terminated_at)
    if logs.count() != 1:
        raise ValidationError('零退款终止状态凭证不一致')
    entries = [item for item in case.history if isinstance(item, dict) and item.get('status') == 'RESOLVED'
        and item.get('created_at') == proposal.case_opened_at.isoformat()
        and item.get('client_request_id') == proposal.case_request_id and item.get('operator_id') == record.operator_id
        and item.get('affected_provider_account_ids') == proposal.affected_accounts
        and item.get('resolution') == proposal.explanation] if isinstance(case.history, list) else []
    from console.models import AdminAuditLog
    expected_audit = {'proposal_id': proposal.pk, 'zero_followup_id': record.pk, 'client_request_id': record.request_id}
    audits = [item for item in AdminAuditLog.objects.filter(
        path=f'/api/admin/aftersales/{case.pk}/negotiation-execution/', resource='aftersales', object_id=str(case.pk))
        if isinstance(item.request_body, dict) and item.request_body.get('zero_followup_id') == record.pk]
    if (len(entries) != 1 or len(audits) != 1 or audits[0].request_body != expected_audit
            or audits[0].operator_account_id != record.operator_id or audits[0].method != 'POST'
            or audits[0].status_code != 200):
        raise ValidationError('零退款终止的结案或审计凭证不一致')
    return record.pk


def verify_addons_at_termination_locked(record):
    """Rebuild liabilities at the actual zero-termination evidence boundary.

    Later refund events are excluded. This is a historical check only; it does
    not authorize a later refund or replace its funding release verification.
    """
    snapshot = record.funding_snapshot
    if (not transaction.get_connection().in_atomic_block or not record.created_at
            or not isinstance(snapshot, dict) or snapshot.get('kind') != 'TRANSFER_ZERO_V1'
            or record.prior_posting.points_refund.redemption.order_id != record.order_id):
        raise ValidationError('零退款终止的加购历史凭证缺失')
    if _addon_facts(record.order, record.prior_posting, before=record.created_at) != snapshot.get('addons'):
        raise ValidationError('零退款终止时的加购履约或资金历史不一致')
    return snapshot['addons']


def verify_funding_at_termination_locked(record):
    """Prove the funding version immediately preceding the termination."""
    from .models import PointsPostRefundTransferRevision
    from .points_transfer_addon_events import later_runs
    from .points_post_refund_transfer import verify_locked as verify_transfer
    from .points_transfer_refund_release import verify_release_locked
    if not transaction.get_connection().in_atomic_block or not record.created_at:
        raise ValidationError('零退款历史资金核验须在订单事务内进行')
    addons = verify_addons_at_termination_locked(record)
    prior = record.prior_posting
    versions = list(PointsPostRefundTransferRevision.objects.filter(order_id=record.order_id,
        created_at__lt=record.created_at).order_by('created_at', 'pk'))
    runs = later_runs(record.order, prior, before=record.created_at)
    for version in versions:
        verify_transfer(version)
    events = [(version.created_at, version.snapshot.get('funding')) for version in versions]
    events.extend((run.created_at, run.refunded_main_snapshot.get('funding')) for run in runs)
    if any(not at or not isinstance(funding, dict) for at, funding in events):
        raise ValidationError('零退款之前的资金事件缺失')
    if len({at for at, _ in events}) != len(events):
        raise ValidationError('零退款之前的资金版本时序无法区分')
    if events:
        funding = max(events, key=lambda item: item[0])[1]
    else:
        evidence = getattr(prior, 'funding_release_evidence', None)
        if not evidence:
            raise ValidationError('首次退款的资金释放凭证缺失')
        funding = asdict(verify_release_locked(evidence))
    if funding != record.funding_snapshot.get('funding'):
        raise ValidationError('零退款终止时的真实资金版本不一致')
    allocated = _allocated_at_termination_locked(record)
    if allocated != record.funding_snapshot.get('allocated_units'):
        raise ValidationError('零退款终止时的到账分配或释放记录不一致')
    return {'funding': funding, 'addons': addons, 'allocated_units': allocated}


def _allocated_at_termination_locked(record):
    """Read actual reservations less verified releases before the zero event."""
    from .models import (OrderAddonFundingRelease, OrderAddonFundingReleaseRun,
        OrderPointsFundingRelease, OrderPointsTransferFundingRelease, PointsPostRefundTransferRelease)
    from .points_funding_evidence import verified_order_allocations_locked
    from .addon_funding_release import verify_run
    from .points_transfer_refund_evidence import verify_transfer_revisions_after_refund_locked
    boundary, order = record.created_at, record.order
    allocations = verified_order_allocations_locked(order)
    verify_transfer_revisions_after_refund_locked(order.pk, record.prior_posting.participants,
        refund_id=record.prior_posting.points_refund.refund_id)
    runs = OrderAddonFundingReleaseRun.objects.filter(
        releases__allocation__in=allocations, created_at__lt=boundary).distinct().order_by('pk')
    for run in runs:
        verify_run(run)
    total = 0
    for allocation in allocations:
        if allocation.created_at == boundary:
            raise ValidationError('到账分配与零退款终止时序无法区分')
        if allocation.created_at > boundary:
            continue
        main = OrderPointsFundingRelease.objects.filter(allocation=allocation)
        transfer = OrderPointsTransferFundingRelease.objects.filter(allocation=allocation)
        post_transfer = PointsPostRefundTransferRelease.objects.filter(allocation=allocation)
        addon = OrderAddonFundingRelease.objects.filter(allocation=allocation)
        if (main.filter(created_at=boundary).exists() or transfer.filter(created_at=boundary).exists()
                or post_transfer.filter(revision__created_at=boundary).exists()
                or addon.filter(run__created_at=boundary).exists()):
            raise ValidationError('资金释放与零退款终止时序无法区分')
        released = (sum(main.filter(created_at__lt=boundary).values_list('amount', flat=True))
            + sum(transfer.filter(created_at__lt=boundary).values_list('amount', flat=True))
            + sum(post_transfer.filter(revision__created_at__lt=boundary).values_list('amount', flat=True))
            + sum(addon.filter(run__created_at__lt=boundary).values_list('amount', flat=True)))
        if released > allocation.amount:
            raise ValidationError('零退款前释放金额超过真实到账分配')
        total += allocation.amount - released
    return total


def prepare_locked(order, case):
    """Freeze the current financial version and exactly one active seat."""
    from .points_transfer_refund_access import permits
    from .points_transfer_refund_continuation import verify_locked
    from .points_transfer_refunded_plan import build_refunded_plan
    from .points_funding_evidence import verify_order_funding
    from .settlement import SettlementError

    if (not transaction.get_connection().in_atomic_block or not permits(order, phase='refund')
            or not applies(order) or order.status != Order.Status.IN_SERVICE
            or order.settled_at or order.consumption_confirmed_at
            or case.order_id != order.pk or case.status != 'OPEN'
            or case.account_id != order.customer_account_id or not case.created_at):
        raise ValidationError('转单零退款终止的订单或资金范围待核对')
    prior = verify_locked(order)
    if not prior.termination_in_service or prior.completed_by_negotiation or not prior.termination_processed_at:
        raise ValidationError('首次退款后的继续履约凭证待核对')
    if not prior.termination_processed_at <= case.created_at <= timezone.now():
        raise ValidationError('售后时间与首次退款顺序不一致')
    scope = case.affected_provider_account_ids
    assignments = list(order.providers.select_for_update().filter(released_at__isnull=True).order_by('pk'))
    if (len(assignments) != 1 or scope != [assignments[0].provider_account_id]
            or assignments[0].settled_at):
        raise ValidationError('仅支持终止唯一仍在服务的陪玩')
    assignment = assignments[0]
    segments = list(OrderParticipationSegment.objects.select_for_update().filter(
        attribution__order=order, provider_account_id=assignment.provider_account_id,
        released_at__isnull=True, started_at__isnull=False))
    if (len(segments) != 1 or segments[0].started_at > case.created_at
            or (case.affected_segment_ids is not None and case.affected_segment_ids != [segments[0].pk])):
        raise ValidationError('继续履约段待核对')
    try:
        plan, funding = build_refunded_plan(order, allow_in_service=True)
        allocation = verify_order_funding(order.pk, allow_finalized=True)
        addons = _addon_facts(order, prior)
    except (SettlementError, ValidationError) as exc:
        raise ValidationError('转单零退款的资金及分段收益待核对') from exc
    person = next((row for row in plan.persons if row.account_id == assignment.provider_account_id), None)
    if (not person or segments[0].pk not in person.segment_ids or not allocation.fully_funded
            or allocation.required != funding.required_units):
        raise ValidationError('继续履约的收益或资金版本不一致')
    from .models import OrderSegmentSettlementManifest
    paid_snapshot = None
    if (order.providers.filter(settled_at__isnull=False).exists()
            or OrderSegmentSettlementManifest.objects.filter(order=order).exists()):
        paid_snapshot = _paid_facts_locked(order, plan, affected_account_id=assignment.provider_account_id)
    snapshot = {'kind': 'TRANSFER_ZERO_V1', 'prior_posting_id': prior.pk,
                'assignment_id': assignment.pk, 'segment_id': segments[0].pk,
                'funding': asdict(funding), 'allocated_units': allocation.allocated,
                'income_units': person.income, 'payment_status': order.payment_status, 'addons': addons}
    if paid_snapshot:
        snapshot['paid_snapshot'] = paid_snapshot
    term = {'account_id': assignment.provider_account_id, 'original_income_units': person.income,
            'burden_units': 0, 'retained_units': person.income, 'additional_units': 0}
    return prior, assignment, segments[0], snapshot, term


def verify_record_locked(record, *, pending=False):
    from .points_zero_followup import digest, verify_proposal
    from .points_transfer_refund_continuation import verify_locked
    from .points_transfer_refunded_funding import plan_refunded_transfer
    from .points_funding_evidence import verify_order_funding
    from .models import OrderStatusLog
    from console.models import AdminAuditLog

    order, proposal, snapshot = record.order, record.proposal, record.funding_snapshot
    verify_proposal(proposal.case, proposal)
    if (not isinstance(snapshot, dict) or snapshot.get('kind') != 'TRANSFER_ZERO_V1'
            or not applies(order)
            or proposal.status != 'ACCEPTED' or proposal.accepted_at != record.accepted_at
            or proposal.affected_accounts != [record.assignment.provider_account_id]
            or (proposal.affected_segment_ids is not None and proposal.affected_segment_ids != [record.segment_id])
            or record.assignment.order_id != order.pk or record.segment.attribution.order_id != order.pk
            or record.segment.provider_account_id != record.assignment.provider_account_id
            or snapshot.get('assignment_id') != record.assignment_id or snapshot.get('segment_id') != record.segment_id
            or snapshot.get('prior_posting_id') != record.prior_posting_id
            or snapshot.get('payment_status') != order.payment_status
            or record.evidence_hash != digest(proposal, record.prior_posting, record.assignment,
                                               record.segment, record.operator, record.request_id, snapshot)):
        raise ValidationError('转单零退款终止凭证不一致')
    if verify_locked(order).pk != record.prior_posting_id:
        raise ValidationError('首次退款凭证已变化')
    from .models import OrderAddonRefund
    later_refunds = bool(record.terminated_at and OrderAddonRefund.objects.filter(
        order=order, created_at__gt=record.terminated_at).exists())
    if later_refunds:
        verify_funding_at_termination_locked(record)
    else:
        funding = plan_refunded_transfer(order.pk, allow_finalized=True)
        allocation = verify_order_funding(order.pk, allow_finalized=True)
        if (asdict(funding) != snapshot.get('funding') or allocation.allocated != snapshot.get('allocated_units')
                or _addon_facts(order, record.prior_posting) != snapshot.get('addons')):
            raise ValidationError('转单零退款资金或加购版本不一致')
    if (proposal.provider_terms != [{'account_id': record.assignment.provider_account_id,
                'original_income_units': snapshot.get('income_units'), 'burden_units': 0,
                'retained_units': snapshot.get('income_units'), 'additional_units': 0}]):
        raise ValidationError('转单零退款资金或收益版本不一致')
    if (record.segment.released_at != record.accepted_at or record.assignment.released_at != record.accepted_at
            or order.providers.filter(released_at__isnull=True).exists()):
        raise ValidationError('零退款终止席位已变化')
    if pending:
        if record.terminated_at is not None or proposal.case.status != 'OPEN' or order.status != Order.Status.IN_SERVICE:
            raise ValidationError('零退款终止状态已变化')
        return record
    logs = list(OrderStatusLog.objects.filter(order=order, reason='双方确认积分零退款协商终止',
        action='COMPLETE', from_status='IN_SERVICE', to_status='COMPLETED', operator_account_id=record.operator_id))
    entries = [row for row in proposal.case.history if isinstance(row, dict) and row.get('status') == 'RESOLVED'
        and row.get('created_at') == proposal.case_opened_at.isoformat()
        and row.get('client_request_id') == proposal.case_request_id and row.get('operator_id') == record.operator_id
        and row.get('affected_provider_account_ids') == proposal.affected_accounts
        and row.get('resolution') == proposal.explanation] if isinstance(proposal.case.history, list) else []
    expected = {'proposal_id': proposal.pk, 'zero_followup_id': record.pk, 'client_request_id': record.request_id}
    audits = [row for row in AdminAuditLog.objects.filter(path=f'/api/admin/aftersales/{proposal.case_id}/negotiation-execution/',
        resource='aftersales', object_id=str(proposal.case_id)) if isinstance(row.request_body, dict)
        and row.request_body.get('zero_followup_id') == record.pk]
    if (not record.terminated_at or order.status != Order.Status.COMPLETED or order.completed_at != record.accepted_at
            or len(logs) != 1 or len(entries) != 1 or len(audits) != 1 or audits[0].request_body != expected
            or audits[0].operator_account_id != record.operator_id or audits[0].method != 'POST'
            or audits[0].status_code != 200):
        raise ValidationError('转单零退款结案或审计凭证不一致')
    return record
