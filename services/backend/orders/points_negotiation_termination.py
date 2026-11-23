"""Test-only atomic closure of an agreed points-order aftersale."""
from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .financial_lock import lock_order_money
from .models import (AfterSale, Order, OrderParticipationSegment, OrderPointsFundingAllocation,
                     OrderPointsFundingReleaseEvidence, PointsNegotiationPosting)
from .negotiation_proposals import _lock_case, enabled as proposals_enabled
from .points_checkout_test import require_isolated_checkout
from .points_negotiation_execution import post_locked, prepare_locked, verify_posting_locked


def enabled():
    from .points_negotiation_preview import agreement_enabled
    from .points_transfer_refund_access import permits
    from .points_business_access import permits as service_permits
    if permits(phase='refund') or service_permits(phase='payment', kind='addon') or any(service_permits(phase=phase, kind=kind)
                                       for kind in ('service', 'addon') for phase in ('funding', 'refund', 'report', 'settlement')):
        return bool(getattr(settings, 'NEGOTIATION_REFUND_EXECUTION_ENABLED', False)
                    and proposals_enabled())
    if not (getattr(settings, 'SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING', False)
            and getattr(settings, 'SERVICE_POINTS_NEGOTIATION_POSTING_TESTING', False)
            and getattr(settings, 'SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING', False)
            and getattr(settings, 'NEGOTIATION_REFUND_EXECUTION_ENABLED', False) and proposals_enabled()
            and agreement_enabled()):
        return False
    try:
        require_isolated_checkout()
    except ValidationError:
        return False
    return True


def transfer_endpoint_enabled():
    """Expose the complete first-refund path only in disposable test stores."""
    from .points_transfer_refund_access import permits
    if permits(phase='refund'):
        return enabled()
    if not enabled() or not getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_ENDPOINT_TESTING', False):
        return False
    required = ('SERVICE_POINTS_TRANSFER_REFUND_AGREEMENT_TESTING', 'SERVICE_POINTS_TRANSFER_REFUND_POSTING_TESTING',
        'SERVICE_POINTS_TRANSFER_REFUND_TERMINATION_TESTING', 'SERVICE_POINTS_TRANSFER_REFUND_PREVIEW_TESTING',
        'SERVICE_POINTS_TRANSFER_CAPTURE_TESTING', 'SERVICE_POINTS_TRANSFER_FUNDING_REVISION_TESTING',
        'SERVICE_POINTS_TRANSFER_SETTLEMENT_TESTING', 'SERVICE_POINTS_TRANSFER_REFUND_REPORT_TESTING',
        'SERVICE_POINTS_FUNDING_RELEASE_TESTING', 'SERVICE_POINTS_TRANSFER_REFUND_RELEASE_TESTING',
        'SERVICE_POINTS_TRANSFER_REFUND_FUNDING_TESTING', 'SERVICE_POINTS_TRANSFER_REFUND_SETTLEMENT_TESTING',
        'SERVICE_POINTS_TRANSFER_REFUND_CONSUMPTION_TESTING', 'SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING',
        'SERVICE_POINTS_REFUND_DEPOSIT_TESTING', 'REFUND_DEPOSIT_DEFICIT_ENABLED',
        'REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED')
    if not all(getattr(settings, name, False) for name in required):
        return False
    from .segment_reports import enabled as reports_enabled
    from .segment_settlement import enabled as settlement_enabled
    from engagement.transfer_refunded_points_confirmation import enabled as consumption_enabled
    if not reports_enabled() or not settlement_enabled() or not consumption_enabled():
        return False
    from engagement.models import (ClubOperationConfig, ConsumptionConfirmation, MemberProfile, OrderFundingAllocation,
                                   PointFundingLot, PointLedger, TransferRefundPointsConsumptionEvidence)
    from users.models import EscortProfile, PassRefundRepaymentReceipt
    from wallet.models import (RefundDepositDeficit, RefundDepositIncomeApplication, RefundDepositRecoveryPermit,
                               Transaction, Wallet)
    from console.models import AdminAuditLog
    from .models import (NegotiationDecision, NegotiationProposal, OrderOriginalAttribution, OrderOriginalShareTransfer, OrderPointsBenefitSnapshot,
        OrderPointsFundingAction, OrderPointsFundingRelease, OrderPointsRedemption, OrderPointsRefund,
        OrderPointsTransferFundingRevision, OrderPointsTransferFundingRelease, OrderSegmentFinancialCoverage,
        OrderSegmentFinancialSnapshot, OrderSegmentPersonSettlement, OrderSegmentReport, OrderSegmentReportImage,
        OrderSegmentReportSubmission, OrderSegmentSettlementManifest, OrderSegmentSubmissionImage, OrderSegmentTransferBasis,
        OrderProvider, OrderRefund, OrderStatusLog, PointsNegotiationPostingShare, PointsNegotiationTerms)
    from stores.db import connection
    from django.db import connections
    directory = connections['default']
    if (directory.settings_dict.get('ENGINE') != 'django.db.backends.sqlite3'
            or directory.settings_dict.get('NAME') not in (':memory:', 'file:memorydb_default?mode=memory&cache=shared')):
        return False
    with connection.cursor() as cursor:
        tables = set(connection.introspection.table_names(cursor))
    required_models = (TransferRefundPointsConsumptionEvidence, ClubOperationConfig, ConsumptionConfirmation,
        MemberProfile, OrderFundingAllocation, PointFundingLot, PointLedger, EscortProfile, PassRefundRepaymentReceipt,
        Transaction, Wallet, AdminAuditLog, RefundDepositDeficit, RefundDepositIncomeApplication, RefundDepositRecoveryPermit,
        AfterSale, Order, OrderProvider, OrderRefund, NegotiationDecision, NegotiationProposal,
        PointsNegotiationPosting, PointsNegotiationPostingShare,
        PointsNegotiationTerms, OrderPointsBenefitSnapshot, OrderPointsRedemption, OrderPointsRefund,
        OrderPointsFundingAction, OrderPointsFundingAllocation, OrderPointsFundingRelease, OrderPointsFundingReleaseEvidence,
        OrderOriginalAttribution, OrderParticipationSegment, OrderOriginalShareTransfer, OrderSegmentFinancialCoverage,
        OrderSegmentFinancialSnapshot, OrderSegmentTransferBasis, OrderStatusLog,
        OrderPointsTransferFundingRevision, OrderPointsTransferFundingRelease, OrderSegmentPersonSettlement,
        OrderSegmentReport, OrderSegmentReportImage, OrderSegmentReportSubmission, OrderSegmentSubmissionImage,
        OrderSegmentSettlementManifest)
    if getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_PRIOR_SETTLEMENT_TESTING', False):
        from .models import OrderSegmentSettlementRevision
        required_models += (OrderSegmentSettlementRevision,)
    if not {model._meta.db_table for model in required_models}.issubset(tables):
        return False
    from stores.models import (PlatformAccount, PlatformAuditEvent, PlatformPassRepaymentClaim,
                               PlatformPassRepaymentOperation, PlatformPointsFundingOperation, PlatformRoleGrant)
    with directory.cursor() as cursor:
        directory_tables = set(directory.introspection.table_names(cursor))
    return {model._meta.db_table for model in (PlatformAccount, PlatformAuditEvent, PlatformPassRepaymentClaim,
            PlatformPassRepaymentOperation, PlatformPointsFundingOperation, PlatformRoleGrant)}.issubset(directory_tables)


def available_for_order(order):
    if not enabled() or order.refund_records.exists():
        return False
    from .addon_settlement import has_paid_addons
    from .points_business_access import permits as service_permits
    formal_addon = service_permits(order, phase='refund', kind='addon')
    if has_paid_addons(order) and not formal_addon and not getattr(settings, 'SERVICE_POINTS_REFUND_ADDON_RELEASE_TESTING', False):
        return False
    from .models import OrderOriginalShareTransfer
    if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
        if not transfer_endpoint_enabled():
            return False
        from .models import OrderSegmentSettlementManifest
        from .points_transfer_refund_access import permits as transfer_permits
        formal_transfer = transfer_permits(order, phase='refund')
        if ((OrderSegmentSettlementManifest.objects.filter(order=order).exists()
                and not (formal_transfer
                         or getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_PRIOR_SETTLEMENT_TESTING', False)))
                or OrderParticipationSegment.objects.filter(attribution__order=order,
                    started_at__isnull=True, original_share__gt=0).exists()):
            return False
        try:
            with transaction.atomic():
                locked = Order.objects.select_for_update().get(pk=order.pk)
                case = locked.aftersales.select_for_update().filter(status='OPEN').first()
                if not case or not case.client_request_id:
                    return False
                from .points_transfer_refund_preview import preview_locked
                preview = preview_locked(case.pk, refund_equivalent_units=10)
            return not formal_transfer or 1 <= len(preview['provider_terms']) <= 2
        except (ValidationError, Order.DoesNotExist):
            return False
    return (formal_addon or service_permits(order, phase='refund', kind='service')
            or not OrderPointsFundingAllocation.objects.filter(redemption__order=order).exists()
            or bool(getattr(settings, 'SERVICE_POINTS_FUNDING_RELEASE_TESTING', False)))


def main_refund_preview_capability(order, case):
    """Return whether the formal funding check applies and the path is enabled."""
    from .points_business_access import permits
    check_required = (permits(order, phase='refund', kind='service')
                      or (case.addon_payment_id is None
                          and any(permits(order, phase='refund', kind=kind) for kind in ('addon', 'transfer'))
                          and not order.refund_records.exists()))
    available = bool(check_required and case.client_request_id and available_for_order(order))
    return check_required, available


def _verify_transfer_termination_segments(order, posting, shares):
    """Check the affected current segment without mistaking older transfers."""
    facts = posting.participants['facts']
    outgoing = {row['previous_segment_id'] for row in facts['transfers']}
    selected = set(posting.proposal.affected_segment_ids or [])
    for share in shares:
        parts = [row for row in facts['segments'] if row['account_id'] == share.account_id and row['started_at']
                 and (not selected or row['id'] in selected)]
        current = [row for row in parts if row['id'] not in outgoing and not row['pre_service_released']]
        if len(current) > 1 or not parts:
            raise ValidationError('积分转单协商履约范围异常')
        if current:
            segment = OrderParticipationSegment.objects.filter(pk=current[0]['id'], attribution__order=order).first()
            if (not segment or segment.released_at != posting.accepted_at
                    or share.assignment.released_at != posting.accepted_at):
                raise ValidationError('积分转单协商终止时间不一致')
        else:
            ended = [row for row in parts if row['id'] in outgoing and row['transfer_released_at']]
            from django.utils.dateparse import parse_datetime
            last = max((parse_datetime(row['transfer_released_at']) for row in ended), default=None)
            active_other = bool(selected and OrderParticipationSegment.objects.filter(
                attribution__order=order, provider_account_id=share.account_id,
                released_at__isnull=True).exclude(pk__in=selected).exists())
            if not last or (not active_other and share.assignment.released_at != last) or last > posting.accepted_at:
                raise ValidationError('积分转单历史履约终止时间不一致')


def verify_termination_locked(posting, *, allow_verified_later_final=False, include_release=False):
    """Verify closure and optionally return its verified release evidence and plan."""
    from .points_transfer_refund_access import require_refund_or_isolated
    from .points_business_access import permits as service_permits
    if not (service_permits(posting.proposal.case.order, phase='payment', kind='addon') or any(service_permits(posting.proposal.case.order, phase=phase, kind=kind)
               for kind in ('service', 'addon') for phase in ('funding', 'refund', 'report', 'settlement'))):
        require_refund_or_isolated()
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('积分协商终止凭证必须在门店事务内核对')
    posting = verify_posting_locked(posting)
    return _verify_termination_from_verified_posting(posting,
        allow_verified_later_final=allow_verified_later_final, include_release=include_release)


def _verify_termination_from_verified_posting(posting, *, allow_verified_later_final=False, facts_only=False,
                                              include_release=False):
    """Check the recorded closure; caller must first verify the posting."""
    from .points_transfer_refund_access import permits
    if facts_only and not (permits(phase='refund') or permits(phase='settlement')
            or getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_TERMINATION_FACTS_TESTING', False)):
        raise ValidationError('转单追加退款的历史结案核对尚未开放')
    proposal, proof = posting.proposal, posting.points_refund
    order, case, refund = proposal.case.order, proposal.case, proof.refund
    if (not posting.termination_processed_at or posting.termination_processed_at < posting.accepted_at
            or not isinstance(case.history, list) or not isinstance(refund.benefit_effects, dict)
            or refund.benefit_effects.get('points_negotiation_termination_version') != 1):
        raise ValidationError('积分协商终止凭证不完整')
    expected_status = 'REFUNDED' if proof.is_final else 'PARTIALLY_REFUNDED'
    entries = [item for item in case.history if isinstance(item, dict)
               and item.get('status') == expected_status
               and item.get('created_at') == proposal.case_opened_at.isoformat()
               and item.get('client_request_id') == proposal.case_request_id]
    from django.utils.dateparse import parse_datetime
    try:
        resolved = parse_datetime(entries[0].get('resolved_at') or '') if len(entries) == 1 else None
    except (ValueError, TypeError):
        resolved = None
    recorded_scope = entries[0].get('affected_provider_account_ids') if len(entries) == 1 else None
    scope_matches = recorded_scope == proposal.affected_accounts
    if recorded_scope is None and isinstance(posting.participants, dict):
        segments = posting.participants['facts']['segments']
        scope_matches = (not any(not row['started_at'] and row['original_share'] for row in segments)
            and sorted({row['account_id'] for row in segments if row['started_at'] and row['original_share']})
                == proposal.affected_accounts)
    status_matches = order.payment_status == expected_status
    if (not status_matches and allow_verified_later_final and expected_status == 'PARTIALLY_REFUNDED'
            and order.payment_status == 'REFUNDED'):
        from .models import OrderPointsRefund
        later = list(OrderPointsRefund.objects.filter(redemption_id=proof.redemption_id,
            refund_id__gt=refund.pk).order_by('refund_id'))
        if len(later) == 1 and later[0].is_final:
            next_posting = PointsNegotiationPosting.objects.filter(points_refund=later[0]).first()
            next_terms = getattr(next_posting.proposal, 'points_terms', None) if next_posting else None
            if next_terms and next_terms.prior_refund_id == proof.pk:
                if facts_only:
                    from .points_negotiation_execution import _verify_transfer_posting_facts_locked
                    _verify_transfer_posting_facts_locked(next_posting)
                else:
                    verify_posting_locked(next_posting)
                status_matches = True
        elif len(later) == 2 and not later[0].is_final and later[1].is_final:
            second = PointsNegotiationPosting.objects.filter(points_refund=later[0]).first()
            third = PointsNegotiationPosting.objects.filter(points_refund=later[1]).first()
            second_terms = getattr(second.proposal, 'points_terms', None) if second else None
            third_terms = getattr(third.proposal, 'points_terms', None) if third else None
            from .models import PointsRefundRoundEvidence
            from .points_refund_round_evidence import _digest
            version = PointsRefundRoundEvidence.objects.filter(order=order, posting=third).select_related(
                'transfer_revision').first() if third else None
            if (second_terms and third_terms and second_terms.prior_refund_id == proof.pk
                    and third_terms.prior_refund_id == later[0].pk and version
                    and version.transfer_revision.first_posting_id == posting.pk
                    and version.transfer_revision.second_posting_id == second.pk
                    and isinstance(version.snapshot, dict) and version.evidence_hash == _digest(version.snapshot)
                    and second.created_at < third.created_at and third.termination_processed_at):
                from .points_negotiation_execution import _verify_transfer_posting_facts_locked, _verify_posting_core_locked
                _verify_transfer_posting_facts_locked(second)
                _verify_posting_core_locked(third)
                status_matches = True
    if (len(entries) != 1 or not resolved or timezone.is_naive(resolved)
            or not posting.accepted_at <= resolved <= posting.termination_processed_at
            or entries[0].get('operator_id') != posting.operator_id
            or entries[0].get('resolution') != proposal.explanation
            or not scope_matches
            or not status_matches):
        raise ValidationError('积分协商售后结案记录不一致')
    shares = list(posting.shares.select_related('assignment').order_by('account_id'))
    if not shares:
        raise ValidationError('积分协商终止范围缺失')
    # Service already completed before the case remains historically ended at
    # its own completion time; in-service affected assignments end at consent.
    if ((posting.completed_by_negotiation and (not posting.termination_in_service
            or order.status != Order.Status.COMPLETED or order.completed_at != posting.accepted_at))
            or (posting.termination_in_service and order.completed_at and order.completed_at < posting.accepted_at)
            or (not posting.termination_in_service and (not order.completed_at
                or order.completed_at > proposal.case_opened_at))):
        raise ValidationError('积分协商结束时的履约状态不一致')
    if posting.termination_in_service:
        if isinstance(posting.participants, dict):
            _verify_transfer_termination_segments(order, posting, shares)
        else:
            for row in shares:
                evidence = next((item for item in posting.participants if item['account_id'] == row.account_id), None)
                segment = OrderParticipationSegment.objects.filter(pk=evidence['segment_id'],
                    attribution__order=order, provider_account_id=row.account_id).first() if evidence else None
                if row.assignment.released_at != posting.accepted_at or not segment or segment.released_at != posting.accepted_at:
                    raise ValidationError('积分协商履约终止时间不一致')
    from .models import OrderStatusLog
    reasons = ('积分协商退款，受影响陪玩终止履约', '双方确认积分协商终止')
    status_rows = list(OrderStatusLog.objects.filter(order=order, reason__in=reasons))
    if posting.termination_in_service:
        expected_reason = reasons[1] if posting.completed_by_negotiation else reasons[0]
        expected_action = 'COMPLETE' if posting.completed_by_negotiation else 'REFUND'
        expected_target = 'COMPLETED' if posting.completed_by_negotiation else 'IN_SERVICE'
        if (len(status_rows) != 1 or status_rows[0].reason != expected_reason
                or status_rows[0].action != expected_action or status_rows[0].from_status != 'IN_SERVICE'
                or status_rows[0].to_status != expected_target or status_rows[0].operator_account_id != posting.operator_id
                or status_rows[0].operator_id != refund.operator_id
                or not posting.accepted_at <= status_rows[0].created_at <= posting.termination_processed_at):
            raise ValidationError('积分协商履约状态日志不一致')
    elif status_rows:
        raise ValidationError('已完成订单的积分协商履约日志不一致')
    from console.models import AdminAuditLog
    path = f'/api/admin/aftersales/{case.pk}/negotiation-execution/'
    expected = {'proposal_id': proposal.pk, 'refund_id': refund.pk, 'refund_units': refund.amount,
                'refund_equivalent_units': proof.equivalent_units, 'points_hundredths': proof.points_hundredths,
                'client_request_id': refund.client_request_id}
    audits = [row for row in AdminAuditLog.objects.filter(path=path, resource='aftersales', object_id=str(case.pk))
              if isinstance(row.request_body, dict) and row.request_body.get('refund_id') == refund.pk]
    if (len(audits) != 1 or audits[0].request_body != expected or audits[0].operator_account_id != posting.operator_id
            or audits[0].operator_id != refund.operator_id or audits[0].method != 'POST' or audits[0].status_code != 200):
        raise ValidationError('积分协商退款审计凭证不一致')
    if include_release or OrderPointsFundingAllocation.objects.filter(redemption__order=order).exists():
        evidence = OrderPointsFundingReleaseEvidence.objects.filter(posting=posting).first()
        if not evidence:
            raise ValidationError('积分协商退款补资释放凭证缺失')
        if facts_only:
            from .points_transfer_refund_release import _verify_release_facts_locked
            _verify_release_facts_locked(evidence)
        else:
            from .points_funding_release import verify_release_locked
            release_plan = verify_release_locked(evidence)
            if include_release:
                return posting, evidence, release_plan
    return posting


def _verify_transfer_termination_facts_locked(posting, *, allow_verified_later_final=False):
    """Verify transfer closure evidence without authorizing settlement state."""
    from .points_transfer_refund_access import permits
    formal = permits(posting.proposal.case.order, phase='refund') or permits(posting.proposal.case.order, phase='settlement')
    if not formal:
        require_isolated_checkout()
    if (not transaction.get_connection().in_atomic_block
            or (not formal and not getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_TERMINATION_FACTS_TESTING', False))):
        raise ValidationError('转单追加退款的历史结案核对尚未开放')
    from .points_negotiation_execution import _verify_transfer_posting_facts_locked
    posting, _, _ = _verify_transfer_posting_facts_locked(posting)
    return _verify_termination_from_verified_posting(posting,
        allow_verified_later_final=allow_verified_later_final, facts_only=True)


@transaction.atomic
def execute_and_terminate(case_id, proposal_id, *, operator, legacy_operator, request_id, _transfer_testing=False):
    from .points_transfer_refund_access import permits, require_refund_or_isolated
    from .points_business_access import permits as service_permits
    if not (service_permits(phase='refund', kind='service') or service_permits(phase='refund', kind='addon')):
        require_refund_or_isolated()
    if not enabled():
        raise ValidationError('积分协商退款终止尚未开放')
    if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
        from console.payroll_event_barrier import lock_payment_refund_events
        lock_payment_refund_events()
    order, case = _lock_case(case_id)
    if permits(phase='refund') and not permits(order, phase='refund'):
        raise ValidationError('转单退款上下文不属于当前订单')
    if any(service_permits(phase='refund', kind=kind) for kind in ('service', 'addon')) and not any(
            service_permits(order, phase='refund', kind=kind) for kind in ('service', 'addon')):
        raise ValidationError('积分退款上下文不属于当前订单')
    lock_order_money()
    from .points_receipt_usage import prelock_transfer_refund_receipts
    prelock_transfer_refund_receipts(order)
    from .models import OrderOriginalShareTransfer
    transferred = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
    if transferred and not permits(order, phase='refund') and (not _transfer_testing or not getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_TERMINATION_TESTING', False)):
        raise ValidationError('转单后的积分退款结案及结算尚未接通')
    existing = PointsNegotiationPosting.objects.select_for_update().filter(
        proposal_id=proposal_id, proposal__case=case).first()
    if existing:
        posting = post_locked(case_id, proposal_id, operator=operator, legacy_operator=legacy_operator, request_id=request_id)
        return verify_termination_locked(posting)
    prepared = prepare_locked(case_id, proposal_id, operator=operator)
    proposal = prepared['proposal']
    if (not order.in_service_at or proposal.accepted_at < order.in_service_at or proposal.accepted_at > timezone.now()
            or order.settlement_mode != Order.SettlementMode.REPORT_APPROVAL
            or order.service_clock_version is None or order.service_aftersale_hours is None
            or order.service_freeze_hours is None or order.service_deposit_percent is None
            or not 0 <= order.service_deposit_percent <= 100
            or order.status not in (Order.Status.IN_SERVICE, Order.Status.COMPLETED)
            or (order.status == Order.Status.COMPLETED and
                (not order.completed_at or order.completed_at < order.in_service_at or order.completed_at > timezone.now()))):
        raise ValidationError('积分协商终止时间或结算规则待核对')
    from engagement.models import ConsumptionConfirmation
    if order.consumption_confirmed_at or ConsumptionConfirmation.objects.filter(order=order, reversed_at__isnull=True).exists():
        raise ValidationError('订单会员实销已确认，请先核对')
    # Capture report drafts before the refund changes the old pre-refund
    # eligibility check. Failure of any later step rolls these back too.
    from .services import ensure_fulfillment_reports, refresh_escort_status
    ensure_fulfillment_reports(order)
    posting = post_locked(case_id, proposal_id, operator=operator, legacy_operator=legacy_operator, request_id=request_id)
    proof, accepted_at = posting.points_refund, posting.accepted_at
    shares = list(posting.shares.select_related('assignment').order_by('account_id'))
    completed_by_negotiation = False
    termination_in_service = order.status == Order.Status.IN_SERVICE
    if termination_in_service:
        affected_ids = {row.account_id for row in shares}
        active = list(order.providers.select_for_update().filter(released_at__isnull=True).order_by('pk'))
        affected = [row for row in active if row.provider_account_id in affected_ids]
        selected = set(posting.proposal.affected_segment_ids or []) if transferred else set()
        if selected:
            current_accounts = set(OrderParticipationSegment.objects.filter(attribution__order=order,
                pk__in=selected, released_at__isnull=True).values_list('provider_account_id', flat=True))
            affected = [row for row in affected if row.provider_account_id in current_accounts]
        if ((not transferred and (len(affected) != len(affected_ids)
                or {row.pk for row in affected} != {row.assignment_id for row in shares}))
                or any(row.settled_at for row in affected)):
            raise ValidationError('积分订单受影响陪玩履约状态已变化')
        if transferred:
            inactive = [row for row in shares if row.account_id not in {item.provider_account_id for item in affected}]
            for row in inactive:
                if selected and OrderParticipationSegment.objects.filter(attribution__order=order,
                        provider_account_id=row.account_id, pk__in=selected,
                        released_at__isnull=False, started_at__isnull=False).exists():
                    continue
                latest = (OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
                    previous_segment__provider_account_id=row.account_id).order_by('-created_at', '-pk').first())
                if (not latest or not latest.previous_segment.started_at or not row.assignment.released_at
                        or row.assignment.released_at != latest.created_at
                        or OrderParticipationSegment.objects.filter(attribution__order=order,
                            provider_account_id=row.account_id, released_at__isnull=True).exists()):
                    raise ValidationError('积分转单历史陪玩履约状态已变化')
        for assignment in affected:
            segments = list(OrderParticipationSegment.objects.select_for_update().filter(
                attribution__order=order, provider_account_id=assignment.provider_account_id, released_at__isnull=True))
            if len(segments) != 1 or not segments[0].started_at:
                raise ValidationError('积分订单陪玩履约片段待核对')
            segments[0].released_at = accepted_at; segments[0].save(update_fields=['released_at'])
            assignment.released_at = accepted_at; assignment.save(update_fields=['released_at'])
        terminated_ids = {row.provider_account_id for row in affected}
        remaining = [row for row in active if row.provider_account_id not in terminated_ids]
        if not remaining and not affected:
            raise ValidationError('积分转单订单当前履约人员待核对')
        if remaining and order.provider_account_id in terminated_ids:
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
                     reason='积分协商退款，受影响陪玩终止履约')
        else:
            from .room_completion_review import invalidate_for_transfer_locked
            invalidate_for_transfer_locked(order, operator, reason='NEGOTIATED_TERMINATION')
            from .state_machine import transition
            def finish(current):
                current.completed_at = accepted_at
            order = transition(order.pk, Order.Status.COMPLETED, operator=legacy_operator,
                operator_account=operator, action='COMPLETE', reason='双方确认积分协商终止',
                side_effect=finish, update_fields=['completed_at'], room_points_negotiation_posting_id=posting.pk)
            completed_by_negotiation = True
        if affected:
            refresh_escort_status(provider_ids=[row.provider_id for row in affected],
                                  account_ids=[row.provider_account_id for row in affected], exclude_order_ids=[order.pk])
    order.payment_status = order.PaymentStatus.REFUNDED if proof.is_final else order.PaymentStatus.PARTIALLY_REFUNDED
    order.refunded_at = timezone.now()
    order.save(update_fields=['payment_status', 'refunded_at', 'updated_at'])
    refund = proof.refund
    refund.benefit_effects = {'points_negotiation_termination_version': 1}
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
    verify_termination_locked(posting)
    from .notifier import notify_order_update
    refresh_providers = (termination_in_service and (permits(order, phase='refund')
                         or (not transferred and any(service_permits(order, phase='refund', kind=kind)
                                                    for kind in ('service', 'addon')))))
    transaction.on_commit(lambda: notify_order_update(order, refresh_providers=refresh_providers))
    transaction.on_commit(lambda: notify_aftersale_reports(order))
    schedule_aftersale_notices(order, case, resolved=True)
    return posting
