"""Test-only atomic closure for an already completed double-order follow-up.

This does not authorize an endpoint or settle the remaining provider. The
entire operation remains confined to the isolated checkout test databases.
"""
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .financial_lock import lock_order_money
from .models import Order, OrderPointsFollowupFundingReleaseEvidence, PointsNegotiationPosting
from .negotiation_proposals import _lock_case
from .points_checkout_test import require_isolated_checkout
from .points_followup_execution import post_followup_locked
from .points_negotiation_execution import verify_posting_locked


def endpoint_enabled():
    """The endpoint stays closed unless the whole isolated chain is enabled."""
    from .points_checkout_test import points_consumption_enabled
    from .points_followup_settlement import enabled as settlement_enabled
    from .points_negotiation_termination import enabled as first_termination_enabled
    from wallet.refund_deposit import enabled as deficit_enabled
    from wallet.refund_recovery_order import tracking_enabled
    from .points_business_access import permits
    if any(permits(phase='refund', kind=kind) for kind in ('service', 'addon')):
        return bool(first_termination_enabled())
    switches = ('SERVICE_POINTS_FOLLOWUP_ENDPOINT_TESTING', 'SERVICE_POINTS_FOLLOWUP_PREVIEW_TESTING',
        'SERVICE_POINTS_FOLLOWUP_AGREEMENT_TESTING', 'SERVICE_POINTS_FOLLOWUP_EXECUTION_TESTING',
        'SERVICE_POINTS_FOLLOWUP_POSTING_TESTING', 'SERVICE_POINTS_FOLLOWUP_REVISION_TESTING',
        'SERVICE_POINTS_FOLLOWUP_RELEASE_TESTING', 'SERVICE_POINTS_FOLLOWUP_TERMINATION_TESTING',
        'SERVICE_POINTS_FOLLOWUP_FUNDING_TESTING', 'SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING',
        'SERVICE_POINTS_REFUND_DEPOSIT_TESTING')
    return bool(all(getattr(settings, key, False) for key in switches) and first_termination_enabled()
                and settlement_enabled() and points_consumption_enabled() and deficit_enabled() and tracking_enabled())


def endpoint_available(prior_refund_id, *, prior_paid=None):
    """Expose the unpaid branch only with every independently gated operation."""
    if not endpoint_enabled() or type(prior_refund_id) is not int or prior_refund_id <= 0:
        return False
    if prior_paid is None:
        from .models import OrderPointsRefundSettlementPerson
        prior_paid = OrderPointsRefundSettlementPerson.objects.filter(
            settlement__posting__points_refund_id=prior_refund_id).exists()
    previous = PointsNegotiationPosting.objects.select_related('proposal__case__order').filter(
        points_refund_id=prior_refund_id).first()
    from .points_business_access import permits
    formal = previous and any(permits(previous.proposal.case.order, phase='refund', kind=kind)
                              for kind in ('service', 'addon'))
    if formal:
        return True
    if not previous or not addon_followup_available(previous.proposal.case.order, previous, prior_paid=prior_paid):
        return False
    if prior_paid:
        return True
    switches = ('SERVICE_POINTS_UNPAID_FOLLOWUP_AGREEMENT_TESTING',
                'SERVICE_POINTS_UNPAID_FOLLOWUP_POSTING_TESTING',
                'SERVICE_POINTS_UNPAID_FOLLOWUP_TERMINATION_TESTING',
                'SERVICE_POINTS_UNPAID_FOLLOWUP_FUNDING_TESTING',
                'SERVICE_POINTS_UNPAID_FOLLOWUP_SETTLEMENT_TESTING')
    return all(getattr(settings, key, False) for key in switches)


def formal_followup_funding_ready(calculation):
    """Require full projected funding once one provider's income is posted."""
    if not calculation['prior_paid']:
        return True
    return (calculation['conditional_funding'].remaining_units == 0
            and endpoint_available(calculation['prior_refund_id'], prior_paid=True))


def addon_followup_available(order, previous, *, prior_paid):
    """Expose the corresponding isolated add-on settlement path only when wired."""
    from .addon_settlement import has_paid_addons
    if not has_paid_addons(order):
        return True
    from .points_business_access import permits
    if any(permits(order, phase='refund', kind=kind) for kind in ('service', 'addon')):
        return True
    settlement_flag = ('SERVICE_POINTS_FOLLOWUP_ADDON_PAID_SETTLEMENT_TESTING' if prior_paid
                       else 'SERVICE_POINTS_FOLLOWUP_ADDON_UNPAID_SETTLEMENT_TESTING')
    if (not getattr(settings, 'SERVICE_POINTS_FOLLOWUP_ADDON_REVISION_TESTING', False)
            or not getattr(settings, settlement_flag, False)):
        return False
    from .points_checkout_test import require_isolated_checkout
    try:
        require_isolated_checkout()
    except ValidationError:
        return False
    from .addon_main_deduction import deductions_at
    return (deductions_at(order) == deductions_at(order, before=previous.created_at)
            or getattr(settings, 'SERVICE_POINTS_FOLLOWUP_ADDON_CHANGED_DEDUCTION_TESTING', False))


def verify_followup_termination_locked(posting):
    from .points_business_access import permits
    if not any(permits(posting.proposal.case.order, phase=phase, kind=kind)
               for kind in ('service', 'addon') for phase in ('funding', 'refund', 'report', 'settlement')):
        require_isolated_checkout()
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('后续积分售后须在门店事务内核对')
    posting = verify_posting_locked(posting)
    proposal, proof = posting.proposal, posting.points_refund
    order, case, refund = proposal.case.order, proposal.case, proof.refund
    terms = proposal.points_terms
    prior = PointsNegotiationPosting.objects.select_for_update().filter(points_refund_id=terms.prior_refund_id).first()
    if not prior:
        raise ValidationError('后续积分售后的原结案凭证缺失')
    from .points_negotiation_termination import verify_termination_locked
    verify_termination_locked(prior, allow_verified_later_final=True)
    release = OrderPointsFollowupFundingReleaseEvidence.objects.select_for_update().filter(
        revision__posting=posting).first()
    if not release:
        raise ValidationError('后续积分售后的补资释放凭证缺失')
    from .points_followup_release import verify_followup_release_locked
    verify_followup_release_locked(release)
    expected_status = 'REFUNDED' if proof.is_final else 'PARTIALLY_REFUNDED'
    entries = [entry for entry in case.history if isinstance(entry, dict)
               and entry.get('status') == expected_status
               and entry.get('created_at') == proposal.case_opened_at.isoformat()
               and entry.get('client_request_id') == proposal.case_request_id] if isinstance(case.history, list) else []
    try:
        resolved = parse_datetime(entries[0].get('resolved_at') or '') if len(entries) == 1 else None
    except (TypeError, ValueError):
        resolved = None
    if posting.termination_in_service:
        from .models import OrderParticipationSegment, OrderStatusLog
        shares = list(posting.shares.select_related('assignment'))
        segments = list(OrderParticipationSegment.objects.filter(attribution__order=order,
            provider_account_id__in=[share.account_id for share in shares], released_at=posting.accepted_at))
        logs = list(OrderStatusLog.objects.filter(order=order, reason='双方确认后续积分协商终止'))
        status_valid = (posting.completed_by_negotiation and order.completed_at == posting.accepted_at
            and len(shares) == len(segments) == len(logs) == 1
            and shares[0].assignment.released_at == posting.accepted_at
            and segments[0].provider_account_id == shares[0].account_id and segments[0].started_at
            and segments[0].started_at <= proposal.case_opened_at <= posting.accepted_at
            and (proposal.affected_segment_ids is None or proposal.affected_segment_ids == [segments[0].pk])
            and not order.providers.filter(released_at__isnull=True).exists()
            and logs[0].from_status == 'IN_SERVICE' and logs[0].to_status == 'COMPLETED'
            and logs[0].action == 'COMPLETE' and logs[0].operator_account_id == posting.operator_id
            and logs[0].operator_id == refund.operator_id)
    else:
        status_valid = not posting.completed_by_negotiation and order.completed_at and order.completed_at <= proposal.case_opened_at
    if (not posting.termination_processed_at or not status_valid
            or order.status != Order.Status.COMPLETED or not order.completed_at
            or order.payment_status != expected_status
            or not isinstance(refund.benefit_effects, dict)
            or refund.benefit_effects.get('points_followup_termination_version') != 1
            or len(entries) != 1 or not resolved or timezone.is_naive(resolved)
            or not posting.accepted_at <= resolved <= posting.termination_processed_at
            or entries[0].get('operator_id') != posting.operator_id
            or entries[0].get('resolution') != proposal.explanation
            or entries[0].get('affected_provider_account_ids') != proposal.affected_accounts):
        raise ValidationError('后续积分协商结案记录不一致')
    from console.models import AdminAuditLog
    path = f'/api/admin/aftersales/{case.pk}/negotiation-execution/'
    expected = {'proposal_id': proposal.pk, 'refund_id': refund.pk, 'refund_units': refund.amount,
                'refund_equivalent_units': proof.equivalent_units, 'points_hundredths': proof.points_hundredths,
                'client_request_id': refund.client_request_id}
    audits = [row for row in AdminAuditLog.objects.filter(path=path, resource='aftersales', object_id=str(case.pk))
              if isinstance(row.request_body, dict) and row.request_body.get('refund_id') == refund.pk]
    if (len(audits) != 1 or audits[0].request_body != expected or audits[0].operator_account_id != posting.operator_id
            or audits[0].operator_id != refund.operator_id or audits[0].method != 'POST' or audits[0].status_code != 200):
        raise ValidationError('后续积分协商退款审计凭证不一致')
    return posting


@transaction.atomic
def execute_followup_and_close(case_id, proposal_id, *, operator, legacy_operator, request_id):
    from .points_business_access import permits
    formal = any(permits(phase='refund', kind=kind) for kind in ('service', 'addon'))
    if not formal:
        require_isolated_checkout()
    if not formal and not getattr(settings, 'SERVICE_POINTS_FOLLOWUP_TERMINATION_TESTING', False):
        raise ValidationError('后续积分协商结案尚未开放')
    if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
        from console.payroll_event_barrier import lock_payment_refund_events
        lock_payment_refund_events()
    order, case = _lock_case(case_id)
    if formal and not any(permits(order, phase='refund', kind=kind) for kind in ('service', 'addon')):
        raise ValidationError('后续售后不属于当前订单资金事务')
    lock_order_money()
    from .points_receipt_usage import prelock_followup_receipts
    prelock_followup_receipts(order)
    from .models import OrderPointsRefundSettlementPerson, PointsNegotiationTerms
    prior_id = PointsNegotiationTerms.objects.filter(proposal_id=proposal_id, proposal__case=case).values_list(
        'prior_refund_id', flat=True).first()
    if (not prior_id or (not OrderPointsRefundSettlementPerson.objects.filter(
            settlement__posting__points_refund_id=prior_id).exists()
            and not formal and not getattr(settings, 'SERVICE_POINTS_UNPAID_FOLLOWUP_TERMINATION_TESTING', False))):
        raise ValidationError('原陪玩未结算的后续协商退款尚未开放')
    existing = PointsNegotiationPosting.objects.select_for_update().filter(
        proposal_id=proposal_id, proposal__case=case).first()
    if existing:
        posting, _ = post_followup_locked(case_id, proposal_id, operator=operator,
                                          legacy_operator=legacy_operator, request_id=request_id)
        return verify_followup_termination_locked(posting)
    in_service = order.status == Order.Status.IN_SERVICE
    if (order.status not in (Order.Status.IN_SERVICE, Order.Status.COMPLETED)
            or (not in_service and not order.completed_at)
            or order.settlement_mode != Order.SettlementMode.REPORT_APPROVAL
            or order.consumption_confirmed_at or order.funding_benefits_resolved_at):
        raise ValidationError('后续积分协商订单状态待核对')
    from engagement.models import ConsumptionConfirmation, PointLedger
    if (ConsumptionConfirmation.objects.select_for_update().filter(order=order).exists()
            or PointLedger.objects.select_for_update().filter(
                order=order, kind='CONSUMPTION', event_key=f'order:{order.pk}:points').exists()):
        raise ValidationError('后续积分协商订单已有会员或消费积分记录，须先核对')
    if in_service:
        from .negotiation_termination import continuing_team
        if not continuing_team(order, verify=True):
            raise ValidationError('原协商后的继续履约凭证待核对')
    posting, _ = post_followup_locked(case_id, proposal_id, operator=operator,
                                      legacy_operator=legacy_operator, request_id=request_id)
    proposal, proof = posting.proposal, posting.points_refund
    if ((not in_service and order.completed_at > proposal.case_opened_at)
            or proposal.accepted_at < proposal.case_opened_at
            or proposal.accepted_at > timezone.now()):
        raise ValidationError('后续积分售后时间待核对')
    if in_service:
        from .models import OrderParticipationSegment
        shares = list(posting.shares.select_related('assignment'))
        active = list(order.providers.select_for_update().filter(released_at__isnull=True))
        if (len(shares) != 1 or len(active) != 1 or shares[0].assignment_id != active[0].pk
                or active[0].settled_at):
            raise ValidationError('继续履约陪玩已变化，须重新核对')
        segments = list(OrderParticipationSegment.objects.select_for_update().filter(
            attribution__order=order, provider_account_id=shares[0].account_id, released_at__isnull=True))
        selected = proposal.affected_segment_ids
        if (len(segments) != 1 or not segments[0].started_at
                or segments[0].started_at > proposal.case_opened_at
                or (selected is not None and selected != [segments[0].pk])):
            raise ValidationError('继续履约段已变化，须重新核对')
        segments[0].released_at = posting.accepted_at
        segments[0].save(update_fields=['released_at'])
        active[0].released_at = posting.accepted_at
        active[0].save(update_fields=['released_at'])
        from .room_completion_review import invalidate_for_transfer_locked
        invalidate_for_transfer_locked(order, operator, reason='NEGOTIATED_TERMINATION')
        from .state_machine import transition
        def finish(current):
            current.completed_at = posting.accepted_at
        order = transition(order.pk, Order.Status.COMPLETED, operator=legacy_operator,
            operator_account=operator, action='COMPLETE', reason='双方确认后续积分协商终止',
            side_effect=finish, update_fields=['completed_at'], room_points_negotiation_posting_id=posting.pk)
        from .services import refresh_escort_status
        refresh_escort_status(provider_ids=[active[0].provider_id], account_ids=[active[0].provider_account_id],
                              exclude_order_ids=[order.pk])
    order.payment_status = order.PaymentStatus.REFUNDED if proof.is_final else order.PaymentStatus.PARTIALLY_REFUNDED
    order.refunded_at = timezone.now()
    order.save(update_fields=['payment_status', 'refunded_at', 'updated_at'])
    refund = proof.refund
    refund.benefit_effects = {'points_followup_termination_version': 1}
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
    posting.termination_in_service = in_service
    posting.completed_by_negotiation = in_service
    posting.save(update_fields=['termination_processed_at', 'termination_in_service', 'completed_by_negotiation'])
    from console.models import AdminAuditLog
    AdminAuditLog.objects.create(operator=legacy_operator, operator_account=operator,
        operator_name=(operator.nickname or operator.username)[:150], method='POST',
        path=f'/api/admin/aftersales/{case.pk}/negotiation-execution/', resource='aftersales', object_id=str(case.pk),
        request_body={'proposal_id': proposal.pk, 'refund_id': refund.pk, 'refund_units': refund.amount,
            'refund_equivalent_units': proof.equivalent_units, 'points_hundredths': proof.points_hundredths,
            'client_request_id': request_id}, status_code=200)
    verify_followup_termination_locked(posting)
    from .notifier import notify_order_update
    transaction.on_commit(lambda: notify_order_update(order))
    transaction.on_commit(lambda: notify_aftersale_reports(order))
    schedule_aftersale_notices(order, case, resolved=True)
    return posting
