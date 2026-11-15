"""Evidence preparation for zero-refund termination after a points refund.

This module does not authorize an endpoint or write a refund. The caller must
hold the order, money and receipt locks in the existing order.
"""
import hashlib
import json
from dataclasses import asdict

from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .models import Order, OrderOriginalShareTransfer, OrderPointsRefundSettlement, OrderParticipationSegment


def applies(order):
    from .points_service_payment import needs_integrated_processing
    from .models import PointsNegotiationPosting
    if (not needs_integrated_processing(order) or order.refund_records.count() != 1
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        return False
    postings = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order,
        termination_in_service=True, completed_by_negotiation=False,
        termination_processed_at__isnull=False).select_related('points_refund'))
    if len(postings) != 1:
        return False
    if postings[0].points_refund.is_final:
        return order.payment_status == Order.PaymentStatus.REFUNDED
    return (order.status in (Order.Status.IN_SERVICE, Order.Status.COMPLETED)
            and order.payment_status == Order.PaymentStatus.PARTIALLY_REFUNDED)


def prepare_locked(order, case):
    from .points_transfer_zero_followup import applies as transfer_applies, prepare_locked as transfer_prepare
    if transfer_applies(order):
        return transfer_prepare(order, case)
    from .points_business_access import permits
    if (not transaction.get_connection().in_atomic_block
            or not any(permits(order, phase='refund', kind=kind) for kind in ('service', 'addon'))
            or order.status not in (Order.Status.IN_SERVICE, Order.Status.COMPLETED)
            or order.payment_status not in (Order.PaymentStatus.REFUNDED, Order.PaymentStatus.PARTIALLY_REFUNDED)
            or order.required_provider_count != 2 or order.refund_records.count() != 1
            or order.settled_at or order.consumption_confirmed_at
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
            or case.order_id != order.pk or case.status != 'OPEN' or case.account_id != order.customer_account_id):
        raise ValidationError('积分全退后的零退款协商范围待核对')
    from .negotiation_termination import continuing_team
    completed = order.status == Order.Status.COMPLETED
    prior = continuing_team(order, verify=True, allow_completed=completed)
    if (not prior or not case.created_at
            or (prior.points_refund.is_final and order.payment_status != Order.PaymentStatus.REFUNDED)
            or (not prior.points_refund.is_final and order.payment_status != Order.PaymentStatus.PARTIALLY_REFUNDED)
            or not prior.termination_processed_at <= case.created_at <= timezone.now()):
        raise ValidationError('原退款、继续履约或收益状态待核对')
    if completed:
        from engagement.confirmation import aftersale_deadline
        deadline = aftersale_deadline(order)
        if (not order.completed_at or not deadline or case.created_at < order.completed_at
                or case.created_at >= deadline):
            raise ValidationError('已完成订单的售后时间待核对')
    elif (OrderPointsRefundSettlement.objects.filter(posting=prior).exists()
          or order.providers.filter(settled_at__isnull=False).exists()):
        raise ValidationError('整单结束前不能已有陪玩结算')
    active = list(order.providers.select_for_update().filter(released_at__isnull=True))
    scope = case.affected_provider_account_ids
    if len(active) != 1 or scope != [active[0].provider_account_id]:
        raise ValidationError('只能协商当前继续履约的陪玩')
    if active[0].settled_at:
        raise ValidationError('受影响陪玩已结算，须另行核对')
    segments = list(OrderParticipationSegment.objects.select_for_update().filter(
        attribution__order=order, provider_account_id=active[0].provider_account_id,
        released_at__isnull=True, started_at__isnull=False))
    if (len(segments) != 1 or segments[0].started_at > case.created_at
            or (case.affected_segment_ids is not None and case.affected_segment_ids != [segments[0].pk])):
        raise ValidationError('继续履约段待核对')
    from .points_service_participation import service_snapshots
    snapshots = service_snapshots(order)
    target = next((row for row in snapshots if row.segment_id == segments[0].pk), None)
    if not target or len(snapshots) != 2:
        raise ValidationError('继续履约的收入快照缺失')
    from .addon_main_deduction import deductions_at
    income = target.income_at_entry - deductions_at(order).get(active[0].provider_account_id, 0)
    if income < 0:
        raise ValidationError('继续履约的待结收益待核对')
    from .points_refunded_funding import later_addon_runs, plan_refunded_order
    from .points_funding_evidence import verify_order_funding
    plan = plan_refunded_order(order.pk)
    funding = verify_order_funding(order.pk)
    runs = later_addon_runs(order, prior)
    settled_person_id = None
    settlement = OrderPointsRefundSettlement.objects.select_for_update().filter(posting=prior).first()
    if settlement:
        if not completed or not funding.fully_funded or settlement.finalized_at or settlement.persons.count() != 1:
            raise ValidationError('既有陪玩结算范围待核对')
        person = settlement.persons.select_for_update().select_related('segment', 'report', 'income_transaction__wallet').get()
        first_share = prior.shares.select_related('assignment').get()
        first_snapshot = next((row for row in snapshots if row.segment_id == person.segment_id), None)
        if not first_snapshot or first_snapshot.segment.provider_account_id != first_share.account_id:
            raise ValidationError('已结算陪玩的收益归属待核对')
        from .points_refunded_settlement import _addon_facts, _check_person, _facts, _funding_rows
        _check_person(order, prior, person, first_snapshot, first_share.assignment)
        addon, parts = _addon_facts(order, prior)
        recorded = tuple(getattr(settlement, key) for key in ('paid_units', 'principal_units', 'gift_units',
            'compensation_units', 'recovered_units', 'funding_units', 'provider_units', 'inviter_units', 'store_units',
            'addon_paid_units', 'addon_principal_units', 'addon_gift_units', 'addon_compensation_units',
            'addon_recovered_units', 'addon_provider_units'))
        if (recorded != _facts(plan, addon) or list(settlement.funding_uses.order_by('allocation_id').values_list(
                'allocation_id', 'amount')) != _funding_rows(order)):
            raise ValidationError('既有结算资金版本待核对')
        from .addon_settlement import verify_addon_records
        if verify_addon_records(order, settlement, parts, [person]) != {
                (part.payment_id, part.segment_id) for part in parts if part.segment_id == person.segment_id}:
            raise ValidationError('已结算陪玩的加购收益凭证待核对')
        settled_person_id = person.pk
    elif order.providers.filter(settled_at__isnull=False).exists():
        raise ValidationError('已有收益但缺少结算凭证')
    snapshot = {'prior_posting_id': prior.pk, 'last_addon_run_id': runs[-1].pk if runs else None,
                'funding': asdict(plan), 'allocated_units': funding.allocated,
                'segment_id': segments[0].pk, 'assignment_id': active[0].pk,
                'income_units': income, 'completed_at': order.completed_at.isoformat() if completed else None,
                'prior_settled_person_id': settled_person_id}
    term = {'account_id': active[0].provider_account_id, 'original_income_units': income,
            'burden_units': 0, 'retained_units': income, 'additional_units': 0}
    return prior, active[0], segments[0], snapshot, term


def digest(proposal, prior, assignment, segment, operator, request_id, snapshot):
    data = [proposal.pk, proposal.terms_hash, proposal.accepted_at.isoformat(), prior.pk,
            assignment.pk, segment.pk, operator.pk, request_id, snapshot]
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def reconcile_unaffected_settlement_locked(order):
    """Advance only the unpaid funding header after B's add-on refund."""
    from .models import OrderPointsRefundSettlementFunding, PointsNegotiationPosting
    from .points_refunded_funding import plan_refunded_order
    from .points_funding_evidence import verify_order_funding
    from .points_refunded_settlement import _addon_facts, _check_person, _facts, _funding_rows
    from .points_service_participation import service_snapshots
    from .addon_settlement import verify_addon_records
    if not transaction.get_connection().in_atomic_block or order.status != Order.Status.COMPLETED or order.settled_at:
        raise ValidationError('未完成订单不能更新已结算陪玩的资金版本')
    prior = PointsNegotiationPosting.objects.select_for_update().filter(points_refund__redemption__order=order).first()
    settlement = OrderPointsRefundSettlement.objects.select_for_update().filter(posting=prior).first() if prior else None
    if not settlement or settlement.finalized_at or settlement.persons.count() != 1:
        raise ValidationError('已结算陪玩的资金版本缺失')
    person = settlement.persons.select_for_update().select_related('segment', 'report', 'income_transaction__wallet').get()
    share = prior.shares.select_related('assignment').get()
    snapshot = next((row for row in service_snapshots(order) if row.segment_id == person.segment_id), None)
    if not snapshot or snapshot.segment.provider_account_id != share.account_id:
        raise ValidationError('已结算陪玩的收益归属待核对')
    _check_person(order, prior, person, snapshot, share.assignment)
    plan, funding = plan_refunded_order(order.pk), verify_order_funding(order.pk)
    if not funding.fully_funded:
        raise ValidationError('加购退款后的整单补资不足')
    addon, parts = _addon_facts(order, prior)
    expected = {(part.payment_id, part.segment_id) for part in parts if part.segment_id == person.segment_id}
    if verify_addon_records(order, settlement, parts, [person]) != expected:
        raise ValidationError('已结算陪玩的加购收益已变化')
    names = ('paid_units', 'principal_units', 'gift_units', 'compensation_units', 'recovered_units',
             'funding_units', 'provider_units', 'inviter_units', 'store_units', 'addon_paid_units',
             'addon_principal_units', 'addon_gift_units', 'addon_compensation_units',
             'addon_recovered_units', 'addon_provider_units')
    for name, value in zip(names, _facts(plan, addon)):
        setattr(settlement, name, value)
    settlement.save(update_fields=list(names))
    settlement.funding_uses.all().delete()
    OrderPointsRefundSettlementFunding.objects.bulk_create([
        OrderPointsRefundSettlementFunding(settlement=settlement, allocation_id=pk, amount=amount)
        for pk, amount in _funding_rows(order)])


def verify_proposal(case, proposal):
    from copy import copy
    from .negotiation_proposals import _decisions, _digest, _snapshot_terms
    episode = copy(case)
    episode.created_at, episode.client_request_id = proposal.case_opened_at, proposal.case_request_id
    if (proposal.case_id != case.pk or proposal.refund_units != 0
            or _snapshot_terms(proposal.provider_terms, proposal.affected_accounts) != proposal.provider_terms
            or proposal.terms_hash != _digest(episode, proposal.version, proposal.affected_accounts, 0,
                                               proposal.provider_terms, proposal.explanation,
                                               proposal.affected_segment_ids)):
        raise ValidationError('零退款协商方案凭证不一致')
    return _decisions(episode, proposal, lock=True)


def verify_record_locked(record, *, pending=False):
    if isinstance(record.funding_snapshot, dict) and record.funding_snapshot.get('kind') == 'TRANSFER_ZERO_V1':
        from .points_transfer_zero_followup import verify_record_locked as verify_transfer
        return verify_transfer(record, pending=pending)
    from .models import OrderStatusLog
    order, proposal = record.order, record.proposal
    case = proposal.case
    verify_proposal(case, proposal)
    if (proposal.status != 'ACCEPTED' or not proposal.accepted_at or proposal.accepted_at != record.accepted_at
            or proposal.affected_accounts != [record.assignment.provider_account_id]
            or record.assignment.order_id != order.pk or record.segment.attribution.order_id != order.pk
            or record.segment.provider_account_id != record.assignment.provider_account_id
            or (proposal.affected_segment_ids is not None and proposal.affected_segment_ids != [record.segment_id])
            or record.prior_posting.points_refund.redemption.order_id != order.pk
            or order.refund_records.count() != 1
            or order.payment_status != (Order.PaymentStatus.REFUNDED if record.prior_posting.points_refund.is_final
                                        else Order.PaymentStatus.PARTIALLY_REFUNDED)
            or not isinstance(record.funding_snapshot, dict)
            or record.funding_snapshot.get('prior_posting_id') != record.prior_posting_id
            or record.funding_snapshot.get('assignment_id') != record.assignment_id
            or record.funding_snapshot.get('segment_id') != record.segment_id
            or record.evidence_hash != digest(proposal, record.prior_posting, record.assignment,
                                               record.segment, record.operator, record.request_id, record.funding_snapshot)):
        raise ValidationError('零退款终止凭证不一致')
    from .points_service_participation import service_snapshots
    from .addon_main_deduction import deductions_at
    snapshots = [row for row in service_snapshots(order) if row.segment_id == record.segment_id]
    if len(snapshots) != 1 or not record.created_at:
        raise ValidationError('零退款协商的本人履约收益快照缺失')
    retained = snapshots[0].income_at_entry - deductions_at(order, before=record.created_at).get(
        record.assignment.provider_account_id, 0)
    expected_term = {'account_id': record.assignment.provider_account_id, 'original_income_units': retained,
                     'burden_units': 0, 'retained_units': retained, 'additional_units': 0}
    if (retained < 0 or record.funding_snapshot.get('income_units') != retained
            or proposal.provider_terms != [expected_term]):
        raise ValidationError('零退款协商的本人保留收益与历史扣减不一致')
    if pending:
        if (record.terminated_at is not None or case.status != 'OPEN' or order.status != Order.Status.IN_SERVICE
                or record.segment.released_at != record.accepted_at or record.assignment.released_at != record.accepted_at
                or order.providers.filter(released_at__isnull=True).exists()):
            raise ValidationError('零退款终止的履约范围已变化')
    else:
        rows = list(OrderStatusLog.objects.filter(order=order, reason='双方确认积分零退款协商终止'))
        entries = [item for item in case.history if isinstance(item, dict)
                   and item.get('status') == 'RESOLVED'
                   and item.get('created_at') == proposal.case_opened_at.isoformat()
                   and item.get('client_request_id') == proposal.case_request_id
                   and item.get('operator_id') == record.operator_id
                   and item.get('affected_provider_account_ids') == proposal.affected_accounts
                   and item.get('resolution') == proposal.explanation] if isinstance(case.history, list) else []
        completed_before = record.funding_snapshot.get('completed_at')
        if completed_before:
            original = list(OrderStatusLog.objects.filter(order=order, action='COMPLETE',
                from_status='IN_SERVICE', to_status='COMPLETED'))
            status_ok = (order.completed_at and order.completed_at.isoformat() == completed_before
                         and order.completed_at <= proposal.case_opened_at and len(original) == 1 and not rows
                         and record.segment.released_at is None and record.assignment.released_at is None)
        else:
            status_ok = (order.completed_at == record.accepted_at and record.segment.released_at == record.accepted_at
                         and record.assignment.released_at == record.accepted_at and len(rows) == 1
                         and rows[0].from_status == 'IN_SERVICE' and rows[0].to_status == 'COMPLETED'
                         and rows[0].operator_account_id == record.operator_id)
        if (not record.terminated_at or order.status != Order.Status.COMPLETED or not status_ok or len(entries) != 1):
            raise ValidationError('零退款履约结束凭证不一致')
        from .points_negotiation_termination import verify_termination_locked
        verify_termination_locked(record.prior_posting)
        from .points_refunded_funding import later_addon_runs
        from .models import OrderPointsFundingReleaseEvidence
        runs = later_addon_runs(order, record.prior_posting)
        before = [run for run in runs if run.created_at <= record.created_at]
        if (record.funding_snapshot.get('last_addon_run_id') != (before[-1].pk if before else None)
                or any(run.created_at == record.created_at for run in runs)):
            raise ValidationError('零退款协商的加购事件版本待核对')
        if before:
            recorded_funding = before[-1].refunded_main_snapshot.get('funding')
        else:
            evidence = OrderPointsFundingReleaseEvidence.objects.filter(posting=record.prior_posting).first()
            if not evidence:
                raise ValidationError('零退款协商的首次退款资金凭证缺失')
            from .points_funding_release import verify_release_locked
            recorded_funding = asdict(verify_release_locked(evidence))
        if recorded_funding != record.funding_snapshot.get('funding'):
            raise ValidationError('零退款协商的资金快照与事件版本不一致')
        settled_person_id = record.funding_snapshot.get('prior_settled_person_id')
        if settled_person_id is not None:
            from .models import OrderPointsRefundSettlementPerson
            person = OrderPointsRefundSettlementPerson.objects.select_for_update().filter(
                pk=settled_person_id, settlement__posting=record.prior_posting).select_related('segment', 'report',
                'income_transaction__wallet').first()
            first_share = record.prior_posting.shares.select_related('assignment').first()
            from .points_service_participation import service_snapshots
            snapshot = next((item for item in service_snapshots(order)
                             if person and item.segment_id == person.segment_id), None)
            if not person or not first_share or not snapshot or snapshot.segment.provider_account_id != first_share.account_id:
                raise ValidationError('原陪玩已结算收益凭证缺失')
            from .points_refunded_settlement import _check_person
            _check_person(order, record.prior_posting, person, snapshot, first_share.assignment)
        from console.models import AdminAuditLog
        expected = {'proposal_id': proposal.pk, 'zero_followup_id': record.pk, 'client_request_id': record.request_id}
        audits = [row for row in AdminAuditLog.objects.filter(
            path=f'/api/admin/aftersales/{case.pk}/negotiation-execution/', resource='aftersales',
            object_id=str(case.pk)) if isinstance(row.request_body, dict)
            and row.request_body.get('zero_followup_id') == record.pk]
        if (len(audits) != 1 or audits[0].request_body != expected or audits[0].operator_account_id != record.operator_id
                or audits[0].method != 'POST' or audits[0].status_code != 200):
            raise ValidationError('零退款协商审计凭证不一致')
    return record


@transaction.atomic
def execute_locked(case_id, proposal_id, *, operator, legacy_operator, request_id):
    from .refunds import authorized_refund
    from .negotiation_proposals import _lock_case
    from .models import NegotiationProposal, PointsZeroFollowupTermination
    if (not authorized_refund(operator) or not operator.is_active or not operator.can_login
            or not isinstance(request_id, str) or not 1 <= len(request_id) <= 64 or request_id != request_id.strip()):
        raise ValidationError('零退款协商经办权限或凭证无效')
    if legacy_operator is not None:
        from club_accounts.models import LegacyAccountMap
        if not LegacyAccountMap.objects.filter(account=operator, legacy_user_id=legacy_operator.pk).exists():
            raise ValidationError('经办人的业务账号与历史身份不一致')
    order, case = _lock_case(case_id)
    from .financial_lock import lock_order_money
    lock_order_money()
    from .points_transfer_zero_followup import applies as transfer_applies
    if transfer_applies(order):
        from .models import PointsNegotiationPosting
        from .points_receipt_usage import prelock_posting_receipts
        prior_posting = PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order).first()
        if not prior_posting:
            raise ValidationError('转单零退款缺少首次退款凭证')
        prelock_posting_receipts(prior_posting)
    else:
        from .points_receipt_usage import prelock_followup_receipts
        prelock_followup_receipts(order)
    existing = PointsZeroFollowupTermination.objects.select_for_update().filter(proposal_id=proposal_id, order=order).first()
    if existing:
        if existing.request_id != request_id or existing.operator_id != operator.pk:
            raise ValidationError('零退款已处理，请核对原操作凭证')
        return verify_record_locked(existing)
    proposal = NegotiationProposal.objects.select_for_update().filter(pk=proposal_id, case=case).first()
    latest = NegotiationProposal.objects.filter(case=case).order_by('-version').first()
    if not proposal or not latest or latest.pk != proposal.pk or proposal.status != 'ACCEPTED':
        raise ValidationError('零退款方案尚未由双方确认')
    verify_proposal(case, proposal)
    prior, assignment, segment, snapshot, term = prepare_locked(order, case)
    if (proposal.provider_terms != [term] or proposal.accepted_at < case.created_at
            or proposal.accepted_at > timezone.now()):
        raise ValidationError('继续履约或收益已变化，请重新确认')
    record = PointsZeroFollowupTermination.objects.create(order=order, proposal=proposal, prior_posting=prior,
        assignment=assignment, segment=segment, operator=operator, request_id=request_id,
        accepted_at=proposal.accepted_at, funding_snapshot=snapshot,
        evidence_hash=digest(proposal, prior, assignment, segment, operator, request_id, snapshot))
    if order.status == Order.Status.IN_SERVICE:
        if snapshot.get('kind') == 'TRANSFER_ZERO_V1':
            from .services import ensure_fulfillment_reports
            ensure_fulfillment_reports(order)
        assignment.released_at = segment.released_at = proposal.accepted_at
        assignment.save(update_fields=['released_at'])
        segment.save(update_fields=['released_at'])
        from .room_completion_review import invalidate_for_transfer_locked
        invalidate_for_transfer_locked(order, operator, reason='NEGOTIATED_TERMINATION')
        from .state_machine import transition
        def finish(current):
            current.completed_at = proposal.accepted_at
        order = transition(order.pk, Order.Status.COMPLETED, operator=legacy_operator, operator_account=operator,
            action='COMPLETE', reason='双方确认积分零退款协商终止', side_effect=finish,
            update_fields=['completed_at'], room_points_zero_followup_id=record.pk)
        from .services import refresh_escort_status
        refresh_escort_status(provider_ids=[assignment.provider_id], account_ids=[assignment.provider_account_id],
                              exclude_order_ids=[order.pk])
    from .aftersale import append_history, notify_aftersale_reports, schedule_aftersale_notices
    case.status, case.operator, case.resolution, case.resolved_at = 'RESOLVED', operator, proposal.explanation, timezone.now()
    append_history(case)
    from wallet.income_priority_evidence import attach_closure
    attach_closure(case)
    case.save(update_fields=['status', 'operator', 'resolution', 'resolved_at', 'history'])
    record.terminated_at = timezone.now()
    record.save(update_fields=['terminated_at'])
    from console.models import AdminAuditLog
    AdminAuditLog.objects.create(operator=legacy_operator, operator_account=operator,
        operator_name=(operator.nickname or operator.username)[:150], method='POST',
        path=f'/api/admin/aftersales/{case.pk}/negotiation-execution/', resource='aftersales', object_id=str(case.pk),
        request_body={'proposal_id': proposal.pk, 'zero_followup_id': record.pk, 'client_request_id': request_id},
        status_code=200)
    record.order = order
    verify_record_locked(record)
    from .notifier import notify_order_update
    transaction.on_commit(lambda: notify_order_update(order))
    transaction.on_commit(lambda: notify_aftersale_reports(order))
    schedule_aftersale_notices(order, case, resolved=True)
    return record
