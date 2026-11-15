"""Atomic isolated closure of a second transferred-points aftersale."""
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .financial_lock import lock_order_money
from .models import Order, OrderPointsTransferFollowupFundingRevision, PointsNegotiationPosting
from .negotiation_proposals import _lock_case
from .points_checkout_test import require_isolated_checkout


def _guard():
    from .points_transfer_refund_access import permits
    formal = permits(phase='refund') or permits(phase='settlement')
    if not formal:
        require_isolated_checkout()
    from .points_transfer_followup_preview import internal_agreement_enabled
    if (not transaction.get_connection().in_atomic_block or not internal_agreement_enabled()
            or (not formal and not getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_TERMINATION_TESTING', False))):
        raise ValidationError('转单追加退款结案尚未开放')


def endpoint_enabled():
    """Enable the complete two-refund flow only in disposable test stores."""
    from .points_negotiation_termination import transfer_endpoint_enabled
    from .points_transfer_refund_access import permits
    if permits(phase='refund'):
        return transfer_endpoint_enabled()
    from .points_transfer_followup_preview import internal_agreement_enabled
    if (not getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_ENDPOINT_TESTING', False)
            or not internal_agreement_enabled() or not transfer_endpoint_enabled()):
        return False
    required = ('SERVICE_POINTS_TRANSFER_REFUND_PRIOR_SETTLEMENT_TESTING',
        'SERVICE_POINTS_TRANSFER_FOLLOWUP_INTERNAL_EXECUTION_TESTING',
        'SERVICE_POINTS_TRANSFER_FOLLOWUP_POSTING_TESTING', 'SERVICE_POINTS_TRANSFER_FOLLOWUP_REVISION_TESTING',
        'SERVICE_POINTS_TRANSFER_FOLLOWUP_TERMINATION_TESTING', 'SERVICE_POINTS_TRANSFER_FOLLOWUP_FUNDING_TESTING',
        'SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_PLAN_TESTING', 'SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_PREFLIGHT_TESTING',
        'SERVICE_POINTS_TRANSFER_FOLLOWUP_SETTLEMENT_TESTING', 'SERVICE_POINTS_TRANSFER_FOLLOWUP_CONSUMPTION_TESTING',
        'SERVICE_POINTS_TRANSFER_FOLLOWUP_REVISION_FACTS_TESTING', 'SERVICE_POINTS_TRANSFER_FOLLOWUP_TERMINATION_FACTS_TESTING',
        'SERVICE_POINTS_TRANSFER_FOLLOWUP_RELEASE_FACTS_TESTING', 'SERVICE_POINTS_TRANSFER_FOLLOWUP_PAID_SUBSET_TESTING',
        'SERVICE_POINTS_TRANSFER_FOLLOWUP_RECEIPT_FACTS_TESTING', 'SERVICE_POINTS_FOLLOWUP_EXECUTION_TESTING')
    if not all(getattr(settings, key, False) for key in required):
        return False
    from .models import OrderPointsTransferFollowupFundingRevision, OrderSegmentSettlementRevision
    connection = transaction.get_connection()
    with connection.cursor() as cursor:
        tables = set(connection.introspection.table_names(cursor))
    return {OrderPointsTransferFollowupFundingRevision._meta.db_table,
            OrderSegmentSettlementRevision._meta.db_table}.issubset(tables)


def available_for_case(case):
    if not endpoint_enabled() or case.status != 'OPEN' or not case.client_request_id:
        return False
    from .points_transfer_followup_preview import preview_locked
    try:
        with transaction.atomic():
            preview_locked(case.pk, refund_equivalent_units=10)
        return True
    except (ValidationError, Order.DoesNotExist):
        return False


def verify_locked(posting):
    """Verify the two refunds, exact closure episode and staff audit."""
    _guard()
    from .points_transfer_followup_revision import verify_locked as verify_revision
    row = OrderPointsTransferFollowupFundingRevision.objects.select_for_update().filter(posting=posting).first()
    if not row:
        raise ValidationError('转单追加退款资金版本缺失')
    verify_revision(row)
    return _verify_closure_from_verified_posting(posting)


def _verify_closure_from_verified_posting(posting):
    """Verify a closure episode after the caller has verified its posting."""
    posting = PointsNegotiationPosting.objects.select_for_update().select_related(
        'proposal__case__order', 'points_refund__refund').get(pk=posting.pk)
    proposal, proof = posting.proposal, posting.points_refund
    case, order, refund = proposal.case, proposal.case.order, proof.refund
    status = 'REFUNDED' if proof.is_final else 'PARTIALLY_REFUNDED'
    history = [item for item in case.history if isinstance(item, dict) and item.get('status') == status
               and item.get('created_at') == proposal.case_opened_at.isoformat()
               and item.get('client_request_id') == proposal.case_request_id] if isinstance(case.history, list) else []
    try:
        resolved = parse_datetime(history[0].get('resolved_at') or '') if len(history) == 1 else None
    except (TypeError, ValueError):
        resolved = None
    in_service = posting.termination_in_service
    status_matches = order.payment_status == status
    if not status_matches and not proof.is_final and order.payment_status == order.PaymentStatus.REFUNDED:
        from .models import PointsRefundRoundEvidence
        from .points_refund_round_evidence import _digest
        later = PointsRefundRoundEvidence.objects.select_related(
            'posting__points_refund__refund', 'posting__proposal__points_terms', 'transfer_revision').filter(
                order=order, transfer_revision__second_posting=posting).first()
        if (later and isinstance(later.snapshot, dict) and later.evidence_hash == _digest(later.snapshot)
                and later.posting.points_refund.is_final and later.posting.termination_processed_at
                and later.posting.proposal.points_terms.prior_refund_id == proof.pk
                and posting.created_at < later.posting.created_at):
            from .points_negotiation_execution import _verify_posting_core_locked
            _verify_posting_core_locked(later.posting)
            status_matches = True
    if (not posting.termination_processed_at or (posting.completed_by_negotiation and not in_service)
            or order.status not in (Order.Status.IN_SERVICE, Order.Status.COMPLETED) or not status_matches
            or (not in_service and (order.status != Order.Status.COMPLETED or not order.completed_at))
            or (in_service and posting.completed_by_negotiation and
                (order.status != Order.Status.COMPLETED or order.completed_at != posting.accepted_at))
            or (in_service and order.completed_at and order.completed_at < posting.accepted_at)
            or not proposal.accepted_at or proposal.accepted_at < proposal.case_opened_at
            or (not in_service and proposal.accepted_at < order.completed_at)
            or not isinstance(refund.benefit_effects, dict)
            or refund.benefit_effects.get('points_transfer_followup_termination_version') != 1
            or len(history) != 1 or not resolved or timezone.is_naive(resolved)
            or not proposal.accepted_at <= resolved <= posting.termination_processed_at
            or history[0].get('operator_id') != posting.operator_id
            or history[0].get('resolution') != proposal.explanation
            or history[0].get('affected_provider_account_ids') != proposal.affected_accounts):
        raise ValidationError('转单追加退款结案记录不一致')
    if in_service:
        from .points_negotiation_termination import _verify_transfer_termination_segments
        _verify_transfer_termination_segments(order, posting, list(posting.shares.select_related('assignment')))
        from .models import OrderStatusLog
        reason = ('双方确认转单追加协商终止' if posting.completed_by_negotiation
                  else '转单追加协商退款，受影响陪玩终止履约')
        logs = list(OrderStatusLog.objects.filter(order=order, reason=reason,
            operator_account_id=posting.operator_id, created_at__gte=posting.accepted_at,
            created_at__lte=posting.termination_processed_at))
        if (len(logs) != 1 or logs[0].from_status != 'IN_SERVICE'
                or logs[0].to_status != ('COMPLETED' if posting.completed_by_negotiation else 'IN_SERVICE')
                or logs[0].action != ('COMPLETE' if posting.completed_by_negotiation else 'REFUND')
                or logs[0].operator_id != refund.operator_id):
            raise ValidationError('转单追加退款履约状态日志异常')
    from console.models import AdminAuditLog
    path = f'/api/admin/aftersales/{case.pk}/negotiation-execution/'
    expected = {'proposal_id': proposal.pk, 'refund_id': refund.pk, 'refund_units': refund.amount,
                'refund_equivalent_units': proof.equivalent_units, 'points_hundredths': proof.points_hundredths,
                'client_request_id': refund.client_request_id}
    audits = [item for item in AdminAuditLog.objects.filter(path=path, resource='aftersales', object_id=str(case.pk))
              if isinstance(item.request_body, dict) and item.request_body.get('refund_id') == refund.pk]
    if (len(audits) != 1 or audits[0].request_body != expected or audits[0].operator_account_id != posting.operator_id
            or audits[0].operator_id != refund.operator_id or audits[0].method != 'POST' or audits[0].status_code != 200):
        raise ValidationError('转单追加退款审计凭证不一致')
    return posting


def _verify_followup_closure_facts_locked(posting):
    """Verify posting and closure facts without authorizing funding or settlement."""
    _guard()
    from .points_transfer_refund_access import permits
    if not (permits(phase='refund') or permits(phase='settlement')
            or getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_TERMINATION_FACTS_TESTING', False)):
        raise ValidationError('转单追加退款的历史结案核对尚未开放')
    from .points_negotiation_execution import _verify_transfer_posting_facts_locked
    posting, _, _ = _verify_transfer_posting_facts_locked(posting)
    return _verify_closure_from_verified_posting(posting)


def verify_closed_or_settled_locked(posting):
    """Verify a closed refund after any later supported settlement credits."""
    _guard()
    from .models import OrderSegmentSettlementManifest
    order = Order.objects.select_for_update().get(pk=posting.proposal.case.order_id)
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
    terms = manifest.plan.get('points_funding', {}) if manifest and isinstance(manifest.plan, dict) else {}
    if terms.get('followup_posting_id') != posting.pk:
        return verify_locked(posting)
    if order.status == Order.Status.IN_SERVICE and not order.settled_at:
        # A different participant may already have been credited.  Verify the
        # immutable closure and actual paid ledger without pretending the
        # remaining service is ready for final settlement.
        from .points_transfer_followup_revision import _verify_followup_revision_facts_locked
        from .points_transfer_refund_evidence import _prior_settlement, _verify_prior_settlement
        from .segment_addon_settlement_revision import verify_revisions
        revision = OrderPointsTransferFollowupFundingRevision.objects.filter(posting=posting).first()
        paid = _prior_settlement(order)
        if not revision or not paid or manifest.finalized_at or paid.get('manifest_id') != manifest.pk:
            raise ValidationError('履约中的已付清单或退款凭证缺失')
        _verify_followup_revision_facts_locked(revision)
        verify_revisions(manifest)
        _verify_prior_settlement(order, paid)
        return posting
    from .points_transfer_followup_settlement import prepare_locked, verify_manifest_locked
    from .segment_settlement import _validate_records, verify_finalized
    from .settlement import SettlementError
    try:
        plan, expected, prior = prepare_locked(order.pk, allow_recorded=True, allow_finalized=bool(order.settled_at))
        verify_manifest_locked(order, manifest, expected, prior)
        if order.settled_at:
            verify_finalized(order, manifest)
        else:
            if manifest.finalized_at:
                raise SettlementError('追加退款的结算完成状态不一致')
            _validate_records(order, manifest, plan)
    except SettlementError as exc:
        raise ValidationError('追加退款后的结算凭证待核对') from exc
    return _verify_followup_closure_facts_locked(posting)


@transaction.atomic
def execute_locked(case_id, proposal_id, *, operator, legacy_operator, request_id):
    """Post assets, funding releases and case closure in one savepoint."""
    _guard()
    if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
        from console.payroll_event_barrier import lock_payment_refund_events
        lock_payment_refund_events()
    from .points_transfer_followup_execution import post_locked
    order, case = _lock_case(case_id)
    lock_order_money()
    existing = PointsNegotiationPosting.objects.select_for_update().filter(
        proposal_id=proposal_id, proposal__case=case).first()
    if existing:
        return verify_closed_or_settled_locked(post_locked(case_id, proposal_id, operator=operator,
                                                            legacy_operator=legacy_operator, request_id=request_id))
    from engagement.models import ConsumptionConfirmation, PointLedger
    if (order.status not in (Order.Status.IN_SERVICE, Order.Status.COMPLETED)
            or (order.status == Order.Status.COMPLETED and not order.completed_at) or order.settled_at
            or order.settlement_mode != Order.SettlementMode.REPORT_APPROVAL
            or order.consumption_confirmed_at or order.funding_benefits_resolved_at
            or ConsumptionConfirmation.objects.select_for_update().filter(order=order).exists()
            or PointLedger.objects.select_for_update().filter(
                order=order, kind='CONSUMPTION', event_key=f'order:{order.pk}:points').exists()):
        raise ValidationError('转单追加退款的订单或会员状态待核对')
    posting = post_locked(case_id, proposal_id, operator=operator, legacy_operator=legacy_operator, request_id=request_id)
    proposal, proof = posting.proposal, posting.points_refund
    if (not proposal.accepted_at or proposal.accepted_at < proposal.case_opened_at
            or (order.completed_at and proposal.accepted_at < order.completed_at)
            or proposal.accepted_at > timezone.now()):
        raise ValidationError('转单追加退款的协商时间待核对')
    termination_in_service = order.status == Order.Status.IN_SERVICE
    completed_by_negotiation = False
    if termination_in_service:
        from .models import OrderParticipationSegment
        from .services import ensure_fulfillment_reports, refresh_escort_status
        ensure_fulfillment_reports(order)
        shares = list(posting.shares.select_related('assignment').order_by('account_id'))
        selected = set(proposal.affected_segment_ids or [])
        active = list(order.providers.select_for_update().filter(released_at__isnull=True).order_by('pk'))
        affected = []
        for share in shares:
            current = list(OrderParticipationSegment.objects.select_for_update().filter(
                attribution__order=order, provider_account_id=share.account_id, released_at__isnull=True,
                started_at__isnull=False).filter(pk__in=selected) if selected else
                OrderParticipationSegment.objects.select_for_update().filter(attribution__order=order,
                    provider_account_id=share.account_id, released_at__isnull=True, started_at__isnull=False))
            if current:
                if len(current) != 1 or share.assignment.settled_at or share.assignment not in active:
                    raise ValidationError('追加退款受影响履约席位已变化')
                current[0].released_at = posting.accepted_at
                current[0].save(update_fields=['released_at'])
                share.assignment.released_at = posting.accepted_at
                share.assignment.save(update_fields=['released_at'])
                affected.append(share.assignment)
            elif not selected or not OrderParticipationSegment.objects.filter(attribution__order=order,
                    provider_account_id=share.account_id, pk__in=selected, released_at__isnull=False,
                    started_at__isnull=False).exists():
                raise ValidationError('追加退款受影响履约段待核对')
        affected_ids = {item.provider_account_id for item in affected}
        remaining = [item for item in active if item.provider_account_id not in affected_ids]
        if remaining and order.provider_account_id in affected_ids:
            replacement = remaining[0]
            order.provider_id, order.provider_account_id = replacement.provider_id, replacement.provider_account_id
            order.provider_name_snapshot = replacement.provider_name_snapshot
            order.save(update_fields=['provider', 'provider_account', 'provider_name_snapshot', 'updated_at'])
        if remaining:
            if affected:
                from .room_completion_review import invalidate_for_transfer_locked
                invalidate_for_transfer_locked(order, operator, reason='NEGOTIATED_TERMINATION')
            from .state_machine import log_only
            log_only(order, action='REFUND', operator=legacy_operator, operator_account=operator,
                     reason='转单追加协商退款，受影响陪玩终止履约')
        else:
            if not affected:
                raise ValidationError('追加退款未找到可终止的当前履约席位')
            from .room_completion_review import invalidate_for_transfer_locked
            from .state_machine import transition
            invalidate_for_transfer_locked(order, operator, reason='NEGOTIATED_TERMINATION')
            def finish(current):
                current.completed_at = posting.accepted_at
            order = transition(order.pk, Order.Status.COMPLETED, operator=legacy_operator,
                operator_account=operator, action='COMPLETE', reason='双方确认转单追加协商终止',
                side_effect=finish, update_fields=['completed_at'], room_points_negotiation_posting_id=posting.pk)
            completed_by_negotiation = True
        if affected:
            refresh_escort_status(provider_ids=[row.provider_id for row in affected],
                account_ids=[row.provider_account_id for row in affected], exclude_order_ids=[order.pk])
    order.payment_status = order.PaymentStatus.REFUNDED if proof.is_final else order.PaymentStatus.PARTIALLY_REFUNDED
    order.refunded_at = timezone.now()
    order.save(update_fields=['payment_status', 'refunded_at', 'updated_at'])
    refund = proof.refund
    refund.benefit_effects = {'points_transfer_followup_termination_version': 1}
    if proof.is_final:
        from coupons.benefits import restore_order_coupons
        from coupons.models import UserCoupon
        coupons = list(UserCoupon.objects.select_for_update().filter(
            order=order, status=UserCoupon.Status.USED).select_related('coupon').order_by('pk'))
        refund.benefit_effects['restored_coupon_count'] = restore_order_coupons(order)
        if coupons:
            refund.benefit_effects['order_coupon_results'] = [
                {'name': row.coupon.name, 'outcome': 'RETURNED' if row.restore_on_refund else 'NOT_RETURNED'}
                for row in coupons]
    refund.save(update_fields=['benefit_effects'])
    from .aftersale import append_history, notify_aftersale_reports, schedule_aftersale_notices
    case.status = 'REFUNDED' if proof.is_final else 'PARTIALLY_REFUNDED'
    case.operator, case.resolution, case.resolved_at = operator, proposal.explanation, timezone.now()
    append_history(case)
    from wallet.income_priority_evidence import attach_closure
    attach_closure(case)
    case.save(update_fields=['status', 'operator', 'resolution', 'resolved_at', 'history'])
    posting.termination_processed_at = timezone.now()
    posting.termination_in_service = termination_in_service
    posting.completed_by_negotiation = completed_by_negotiation
    posting.save(update_fields=['termination_processed_at', 'termination_in_service', 'completed_by_negotiation'])
    from console.models import AdminAuditLog
    AdminAuditLog.objects.create(operator=legacy_operator, operator_account=operator,
        operator_name=(operator.nickname or operator.username)[:150], method='POST',
        path=f'/api/admin/aftersales/{case.pk}/negotiation-execution/', resource='aftersales', object_id=str(case.pk),
        request_body={'proposal_id': proposal.pk, 'refund_id': refund.pk, 'refund_units': refund.amount,
            'refund_equivalent_units': proof.equivalent_units, 'points_hundredths': proof.points_hundredths,
            'client_request_id': request_id}, status_code=200)
    verify_locked(posting)
    from .notifier import notify_order_update
    transaction.on_commit(lambda: notify_order_update(order))
    transaction.on_commit(lambda: notify_aftersale_reports(order))
    schedule_aftersale_notices(order, case, resolved=True)
    return posting
